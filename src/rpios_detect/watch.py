"""Continuous SD-card station: insert, identify, eject, repeat.

Works on macOS, Linux, and Windows. Detection stays read-only. After a
verdict the card is ejected so the operator can swap the next one.
Internal and live-system disks are never ejected.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, TextIO

from rpios_detect import __version__
from rpios_detect.eject import EjectResult, eject_removable
from rpios_detect.models import (
    EXIT_INTERNAL,
    EXIT_USAGE,
    Confidence,
    MediaKind,
    TargetResult,
    Verdict,
    utc_now_iso,
)
from rpios_detect.probe import DiscoveredDisk, DiscoveredPartition
from rpios_detect.scan import discover_disks, exit_code_for, inspect_disk
from rpios_detect.ui import StationScreen, StationState, render_station, stdout_supports_unicode

DiscoverFn = Callable[..., list[DiscoveredDisk]]
InspectFn = Callable[..., TargetResult]
EjectFn = Callable[..., EjectResult]
SleepFn = Callable[[float], None]
ClockFn = Callable[[], float]
StopFn = Callable[[], bool]


@dataclass
class WatchConfig:
    poll_interval: float = 0.6
    settle_seconds: float = 1.2
    mount_wait_seconds: float = 8.0
    eject: bool = True
    beep: bool = False
    json_lines: bool = False
    once: bool = False
    verbose: bool = False
    color: bool = False


@dataclass
class WatchStats:
    checked: int = 0
    raspberry_pi_os: int = 0
    not_raspberry_pi_os: int = 0
    unsure: int = 0
    last_result: TargetResult | None = None

    def record(self, kind: str) -> None:
        self.checked += 1
        if kind == "raspberry_pi_os":
            self.raspberry_pi_os += 1
        elif kind == "unsure":
            self.unsure += 1
        else:
            self.not_raspberry_pi_os += 1


def media_fingerprint(disk: DiscoveredDisk) -> tuple[Any, ...]:
    parts = tuple(
        sorted(
            (
                p.device,
                p.size_bytes,
                (p.fstype or "").lower(),
                (p.label or "").lower(),
            )
            for p in disk.partitions
        )
    )
    return (disk.device, disk.size_bytes, parts)


def media_content_fingerprint(disk: DiscoveredDisk) -> tuple[Any, ...]:
    """Identity of the media itself, ignoring the reader device node.

    After eject, macOS/Linux often re-enumerate the same card as disk5 / sdc.
    Pending-removal keyed only by /dev/disk4 would treat that as a new card.
    """
    parts = tuple(
        sorted(
            (
                p.size_bytes,
                (p.fstype or "").lower(),
                (p.label or "").lower(),
            )
            for p in disk.partitions
        )
    )
    return (disk.size_bytes, parts)


def looks_like_empty_slot(disk: DiscoveredDisk) -> bool:
    """True when a USB reader is present but no card is in it.

    Empty *cards* still report their real capacity (often several GB) and
    no partitions. Empty *slots* usually report 0 bytes. Treating a 0-byte
    leftover node as "still waiting" would block the next blank card.
    """
    if disk.partitions:
        return False
    size = disk.size_bytes
    if size is None:
        return False
    return size < 1_048_576


def is_watch_candidate(disk: DiscoveredDisk) -> bool:
    if disk.internal or disk.live_system:
        return False
    if looks_like_empty_slot(disk):
        return False
    fs = {(p.fstype or "").lower() for p in disk.partitions}
    if fs & {"apfs", "hfs", "hfs+", "hfsx", "apple_apfs"}:
        return False
    if disk.size_bytes is not None and disk.size_bytes > 2 * 1024**4:
        return False
    if disk.kind == MediaKind.REMOVABLE_DISK:
        return True
    return bool(disk.removable)


def is_new_media(old_fp: tuple[Any, ...], disk: DiscoveredDisk) -> bool:
    """True when this looks like a different card in the same reader."""
    new_fp = media_fingerprint(disk)
    if new_fp == old_fp:
        return False
    old_size, old_parts = old_fp[1], old_fp[2]
    new_parts = new_fp[2]
    if len(new_parts) > len(old_parts):
        return True
    if old_size and disk.size_bytes:
        delta = abs(disk.size_bytes - old_size)
        if delta > max(1_048_576, int(old_size * 0.02)):
            return True
    if old_parts and new_parts and new_parts != old_parts and len(new_parts) >= len(old_parts):
        return True
    return False


def looks_empty(result: TargetResult) -> bool:
    if result.verdict != Verdict.NOT_PI:
        return False
    if not result.partitions:
        return True
    return not result.evidence


def classify_card(result: TargetResult) -> str:
    if result.verdict == Verdict.RASPBERRY_PI_OS and result.confidence in {
        Confidence.HIGH,
        Confidence.CERTAIN,
    }:
        return "raspberry_pi_os"
    if result.verdict in {
        Verdict.UNKNOWN,
        Verdict.RASPBERRY_PI_OS,
        Verdict.RASPBERRY_PI_OS_LIKE,
    }:
        return "unsure"
    if looks_empty(result):
        return "empty"
    return "not_raspberry_pi_os"


def _is_fat(part: DiscoveredPartition) -> bool:
    fs = (part.fstype or "").lower()
    label = (part.label or "").lower()
    return "fat" in fs or fs in {"vfat", "msdos", "exfat"} or label in {"boot", "bootfs"}


def _waiting_for_fat_mount(disk: DiscoveredDisk) -> bool:
    fats = [p for p in disk.partitions if _is_fat(p)]
    if not fats:
        return False
    return not any(p.mountpoint for p in fats)


def _fmt_size(n: int | None) -> str:
    if n is None:
        return "?"
    if n >= 10**9:
        return f"{n / 10**9:.1f} GB"
    if n >= 10**6:
        return f"{n / 10**6:.1f} MB"
    return f"{n} B"


def _enable_windows_ansi() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        return


class _Palette:
    def __init__(self, enabled: bool) -> None:
        if enabled:
            self.green = "\033[32m"
            self.red = "\033[31m"
            self.yellow = "\033[33m"
            self.bold = "\033[1m"
            self.dim = "\033[2m"
            self.reset = "\033[0m"
        else:
            self.green = self.red = self.yellow = self.bold = self.dim = self.reset = ""


def format_watch_banner(cfg: WatchConfig) -> str:
    eject = "scan then eject" if cfg.eject else "scan only (no eject)"
    return (
        f"rpios-detect watch {__version__}\n"
        f"Insert a MicroSD card. Ctrl+C stops. ({eject})\n"
        "Empty cards count as not Raspberry Pi OS and are still ejected.\n"
    )


def format_watch_result(
    result: TargetResult,
    *,
    card_number: int,
    kind: str,
    eject: EjectResult | None,
    palette: _Palette | None = None,
) -> str:
    c = palette or _Palette(False)
    if kind == "raspberry_pi_os":
        headline = f"{c.green}{c.bold}RASPBERRY PI OS{c.reset}"
        color = c.green
    elif kind == "empty":
        headline = f"{c.red}{c.bold}NOT RASPBERRY PI OS{c.reset}  {c.dim}(empty card){c.reset}"
        color = c.red
    elif kind == "unsure":
        headline = f"{c.yellow}{c.bold}UNSURE{c.reset}"
        color = c.yellow
    else:
        extra = ""
        guess = result.os_name or result.other_os_guess
        if guess:
            extra = f"  {c.dim}({guess}){c.reset}"
        headline = f"{c.red}{c.bold}NOT RASPBERRY PI OS{c.reset}{extra}"
        color = c.red
    size = _fmt_size(result.media.size_bytes)
    bus = result.media.bus.value
    lines = [
        color + ("=" * 64) + c.reset,
        f"{c.bold}CARD {card_number}{c.reset}  {result.target}  {size}  {bus}",
        f"  {headline}",
        f"  Verdict: {result.verdict.value}   confidence: {result.confidence.value}",
    ]
    if result.os_name:
        lines.append(f"  OS: {result.os_name}")
    if result.edition.value != "unknown":
        stage = f", pi-gen stage {result.pi_gen_stage}" if result.pi_gen_stage is not None else ""
        date = f", {result.image_date}" if result.image_date else ""
        lines.append(f"  Edition: {result.edition.value}{date}{stage}")
    elif result.image_date:
        lines.append(f"  Image date: {result.image_date}")
    if result.likely_boards:
        lines.append(f"  Boards: {', '.join(result.likely_boards)}")
    for warning in result.warnings[:4]:
        lines.append(f"  Note: {warning}")
    if eject is None:
        lines.append("  Left mounted (--no-eject). Remove the card, then insert the next one.")
    elif eject.skipped:
        lines.append(f"  Not ejected: {eject.reason}")
        lines.append("  Remove the card, then insert the next one.")
    elif eject.ok:
        lines.append("  Ejected. Remove the card, then insert the next one.")
    else:
        lines.append(f"  Eject failed: {eject.reason}")
        lines.append("  Eject it in the OS, remove the card, then insert the next one.")
    lines.append(color + ("=" * 64) + c.reset)
    return "\n".join(lines) + "\n"


def format_watch_summary(stats: WatchStats) -> str:
    return (
        f"Stopped. Checked {stats.checked} card(s): "
        f"{stats.raspberry_pi_os} Raspberry Pi OS, "
        f"{stats.not_raspberry_pi_os} not, "
        f"{stats.unsure} unsure.\n"
    )


def _headline_for(kind: str, result: TargetResult) -> str:
    if kind == "raspberry_pi_os":
        return "RASPBERRY PI OS"
    if kind == "empty":
        return "NOT RASPBERRY PI OS  (empty card)"
    if kind == "unsure":
        return "UNSURE"
    guess = result.os_name or result.other_os_guess
    if guess:
        return f"NOT RASPBERRY PI OS  ({guess})"
    return "NOT RASPBERRY PI OS"


def _eject_note(eject: EjectResult | None, *, ejecting: bool) -> str:
    if not ejecting:
        return "Left mounted. Insert the next card when you are ready."
    if eject is None:
        return "Insert the next card when you are ready."
    if eject.skipped:
        return f"Not ejected: {eject.reason}  Insert the next card when you are ready."
    if eject.ok:
        return "Ejected. Result stays until you insert the next card."
    return f"Eject failed: {eject.reason}  Result stays until you insert the next card."


def _beep(stream: TextIO, count: int) -> None:
    try:
        stream.write("\a" * count)
        stream.flush()
    except Exception:
        return


def wait_until_settled(
    device: str,
    cfg: WatchConfig,
    *,
    discover: DiscoverFn,
    sleep: SleepFn,
    clock: ClockFn,
    should_stop: StopFn,
) -> DiscoveredDisk | None:
    last: tuple[Any, ...] | None = None
    stable_since: float | None = None
    mount_deadline: float | None = None
    hard_stop = clock() + cfg.settle_seconds + cfg.mount_wait_seconds + 20.0
    while clock() < hard_stop and not should_stop():
        disks = {d.device: d for d in discover() if is_watch_candidate(d)}
        disk = disks.get(device)
        if disk is None:
            return None
        fp = media_fingerprint(disk)
        now = clock()
        if fp != last:
            last = fp
            stable_since = now
            mount_deadline = None
        if stable_since is None or (now - stable_since) < cfg.settle_seconds:
            sleep(cfg.poll_interval)
            continue
        if _waiting_for_fat_mount(disk):
            if mount_deadline is None:
                mount_deadline = now + cfg.mount_wait_seconds
            if now < mount_deadline:
                sleep(cfg.poll_interval)
                continue
        return disk
    if should_stop():
        return None
    return None


def run_watch(
    cfg: WatchConfig,
    *,
    discover: DiscoverFn | None = None,
    inspect: InspectFn | None = None,
    eject: EjectFn | None = None,
    sleep: SleepFn = time.sleep,
    clock: ClockFn = time.monotonic,
    should_stop: StopFn | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    discover = discover or discover_disks
    inspect = inspect or inspect_disk
    eject = eject or eject_removable
    should_stop = should_stop or (lambda: False)
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    palette = _Palette(cfg.color)
    stats = WatchStats()
    pending_devices: dict[str, tuple[Any, ...]] = {}
    pending_contents: dict[tuple[Any, ...], float] = {}
    once_content: tuple[Any, ...] | None = None
    announced_wait = False
    interrupted = False
    interactive = (not cfg.json_lines) and bool(getattr(out, "isatty", lambda: False)())
    screen = StationScreen(out, interactive=interactive)
    ui = StationState(
        version=__version__,
        eject=cfg.eject,
        color=cfg.color,
        unicode=stdout_supports_unicode(out),
    )

    def paint(phase: str, **fields: object) -> None:
        prev = ui.phase
        if phase == "waiting" and "hint" not in fields and prev != "waiting":
            ui.hint = ""
        ui.phase = phase
        for key, value in fields.items():
            setattr(ui, key, value)
        ui.checked = stats.checked
        ui.yes = stats.raspberry_pi_os
        ui.no = stats.not_raspberry_pi_os
        ui.unsure = stats.unsure
        if interactive:
            screen.paint(render_station(ui))

    def _park(disk: DiscoveredDisk) -> None:
        pending_devices[disk.device] = media_fingerprint(disk)
        pending_contents[media_content_fingerprint(disk)] = clock()

    def emit(event: str, payload: dict[str, Any]) -> None:
        if not cfg.json_lines:
            return
        row = {"event": event, "ts": utc_now_iso(), **payload}
        out.write(json.dumps(row, default=str) + "\n")
        out.flush()

    def say(text: str) -> None:
        if cfg.json_lines:
            return
        out.write(text if text.endswith("\n") else text + "\n")
        out.flush()

    if cfg.color:
        _enable_windows_ansi()
    done_code: int | None = None
    try:
        if interactive:
            screen.open()
            paint("waiting")
        else:
            say(format_watch_banner(cfg))
        emit("waiting", {})

        try:
            while not should_stop():
                try:
                    found = [d for d in discover() if is_watch_candidate(d)]
                except Exception as exc:  # noqa: BLE001
                    if interactive:
                        paint("error", hint=str(exc))
                    else:
                        say(f"discovery error: {exc}")
                    sleep(max(cfg.poll_interval, 1.0))
                    continue
                by_dev = {d.device: d for d in found}
                present_contents = {media_content_fingerprint(d) for d in found}

                for dev, fp in list(pending_devices.items()):
                    disk = by_dev.get(dev)
                    if disk is None or is_new_media(fp, disk):
                        emit("removed", {"device": dev})
                        del pending_devices[dev]
                        announced_wait = False

                now = clock()
                for d in found:
                    fp = media_content_fingerprint(d)
                    if fp in pending_contents:
                        pending_contents[fp] = now
                cooldown = max(3.0, cfg.settle_seconds)
                for fp, seen in list(pending_contents.items()):
                    if fp not in present_contents and (now - seen) >= cooldown:
                        del pending_contents[fp]

                if cfg.once and stats.last_result is not None:
                    if once_content is None or once_content not in present_contents:
                        done_code = exit_code_for([stats.last_result])
                        break

                available = [
                    d
                    for d in found
                    if d.device not in pending_devices
                    and media_content_fingerprint(d) not in pending_contents
                ]
                if not available:
                    if interactive:
                        # Keep YES/NO on screen after eject (and after the card
                        # is physically gone). The next insert replaces it.
                        if ui.phase in {"verdict", "error"}:
                            paint(ui.phase)
                        else:
                            paint("waiting")
                    elif not announced_wait and stats.checked == 0:
                        say("Waiting for a card…")
                        announced_wait = True
                    sleep(cfg.poll_interval)
                    continue

                disk = available[0]
                size = _fmt_size(disk.size_bytes)
                if interactive:
                    paint(
                        "settling",
                        device=disk.device,
                        size=size,
                        hint="Waiting until the reader settles…",
                    )
                else:
                    say(
                        f"Card detected: {disk.device} ({size}). "
                        "Waiting until it settles…"
                    )
                emit("inserted", {"device": disk.device, "size_bytes": disk.size_bytes})
                settled = wait_until_settled(
                    disk.device,
                    cfg,
                    discover=discover,
                    sleep=sleep,
                    clock=clock,
                    should_stop=should_stop,
                )
                if settled is None:
                    if interactive:
                        paint("waiting", hint="Card was removed before the scan finished.")
                    else:
                        say("Card was removed before the scan finished.")
                    emit("removed", {"device": disk.device, "before_scan": True})
                    announced_wait = False
                    continue

                if interactive:
                    paint(
                        "scanning",
                        device=settled.device,
                        size=_fmt_size(settled.size_bytes),
                        hint="Read-only — nothing is written to the card.",
                    )
                try:
                    result = inspect(settled, verbose=cfg.verbose)
                except TypeError:
                    result = inspect(settled)
                except Exception as exc:  # noqa: BLE001
                    if interactive:
                        paint("error", hint=str(exc), device=settled.device)
                    else:
                        say(f"Scan failed: {exc}")
                        say("Leave the card mounted and check permissions, then retry.")
                    emit("error", {"device": settled.device, "error": str(exc)})
                    _park(settled)
                    sleep(cfg.poll_interval)
                    continue

                kind = classify_card(result)
                stats.record(kind)
                stats.last_result = result
                headline = _headline_for(kind, result)

                eject_res: EjectResult | None = None
                if cfg.eject:
                    try:
                        eject_res = eject(settled, discover=discover)
                    except TypeError:
                        eject_res = eject(settled)
                    except Exception as exc:  # noqa: BLE001
                        eject_res = EjectResult(ok=False, reason=str(exc))

                if eject_res is None:
                    eject_state = "disabled"
                elif eject_res.skipped:
                    eject_state = "skipped"
                elif eject_res.ok:
                    eject_state = "ok"
                else:
                    eject_state = "failed"

                emit(
                    "result",
                    {
                        "card": stats.checked,
                        "kind": kind,
                        "eject": eject_state,
                        "result": result.to_dict(),
                    },
                )
                note = _eject_note(eject_res if cfg.eject else None, ejecting=cfg.eject)
                extra = []
                if result.os_name:
                    extra.append(result.os_name)
                if interactive:
                    paint(
                        "verdict",
                        kind=kind,
                        card_number=stats.checked,
                        device=result.target,
                        size=_fmt_size(result.media.size_bytes),
                        headline=headline,
                        detail=f"{result.target}  {_fmt_size(result.media.size_bytes)}",
                        eject_note=note,
                        extra_lines=extra,
                        last_kind=kind,
                        last_headline=headline,
                        last_device=result.target,
                    )
                else:
                    say(
                        format_watch_result(
                            result,
                            card_number=stats.checked,
                            kind=kind,
                            eject=eject_res if cfg.eject else None,
                            palette=palette,
                        )
                    )
                if cfg.beep:
                    _beep(err, 2 if kind == "raspberry_pi_os" else 1)

                _park(settled)
                if once_content is None:
                    once_content = media_content_fingerprint(settled)
                announced_wait = False
                if not cfg.once:
                    sleep(cfg.poll_interval)
        except KeyboardInterrupt:
            interrupted = True
            if not interactive:
                say("")
    finally:
        screen.close()
    say(format_watch_summary(stats))
    emit(
        "stopped",
        {
            "checked": stats.checked,
            "raspberry_pi_os": stats.raspberry_pi_os,
            "not_raspberry_pi_os": stats.not_raspberry_pi_os,
            "unsure": stats.unsure,
        },
    )
    if interrupted:
        return 0
    if done_code is not None:
        return done_code
    if cfg.once and stats.last_result is not None:
        return exit_code_for([stats.last_result])
    return 0



def build_watch_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rpios-detect watch",
        description=(
            "Watch for removable MicroSD/USB media, decide whether each card is "
            "Raspberry Pi OS, eject it, and wait for the next one. Ctrl+C stops."
        ),
    )
    parser.add_argument(
        "--no-eject",
        action="store_true",
        help="do not unmount/eject after each scan (you remove the card yourself)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit JSON lines (inserted/result/removed/stopped) instead of a station UI",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="handle a single card (insert → scan → eject → remove) and exit",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.6,
        metavar="SEC",
        help="how often to look for cards (default: 0.6)",
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=1.2,
        metavar="SEC",
        help="wait until the partition list stops changing (default: 1.2)",
    )
    parser.add_argument(
        "--mount-wait",
        type=float,
        default=8.0,
        metavar="SEC",
        help="extra wait for a FAT boot volume to mount (default: 8)",
    )
    parser.add_argument("--beep", action="store_true", help="beep after each verdict")
    parser.add_argument("--no-beep", action="store_true", help="never beep")
    parser.add_argument("--color", action="store_true", help="force color output")
    parser.add_argument("--no-color", action="store_true", help="disable color")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="include detection rule hits on the JSON result (rule_log)",
    )
    return parser


def run_watch_cli(argv: Sequence[str] | None = None, *, prog: str = "rpios-detect watch") -> int:
    parser = build_watch_parser()
    parser.prog = prog
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        code = exc.code
        if code in (None, 0):
            return 0
        return EXIT_USAGE

    tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    if args.json or args.no_beep:
        beep = False
    elif args.beep or tty:
        beep = True
    else:
        beep = False
    if args.json or args.no_color:
        color = False
    elif args.color or tty:
        color = True
    else:
        color = False
    if args.interval < 0 or args.settle < 0 or args.mount_wait < 0:
        print("error: intervals must be >= 0", file=sys.stderr)
        return EXIT_USAGE
    cfg = WatchConfig(
        poll_interval=args.interval,
        settle_seconds=args.settle,
        mount_wait_seconds=args.mount_wait,
        eject=not args.no_eject,
        beep=beep,
        json_lines=args.json,
        once=args.once,
        verbose=args.verbose,
        color=color,
    )
    try:
        return run_watch(cfg)
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"internal error: {exc}", file=sys.stderr)
        return EXIT_INTERNAL
