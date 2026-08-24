"""Eject removable media after a scan.

This is not a write to the filesystem. It only unmounts / ejects so the
operator can swap cards. Internal and live-system disks are refused.
The device identity is checked again immediately before eject.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from rpios_detect.probe import DiscoveredDisk
from rpios_detect.safety import SafetyError, run_readonly


@dataclass(frozen=True)
class EjectResult:
    ok: bool
    skipped: bool = False
    reason: str = ""


def eject_removable(disk: DiscoveredDisk, *, discover=None) -> EjectResult:
    """Eject `disk` if it is still the same removable, non-live device."""
    if disk.internal or disk.live_system:
        return EjectResult(
            ok=False,
            skipped=True,
            reason="refusing to eject an internal or live-system disk",
        )
    if discover is None:
        from rpios_detect.scan import discover_disks

        discover = discover_disks
    try:
        present = discover(all_disks=False)
    except TypeError:
        present = discover()
    current = next((d for d in present if d.device == disk.device), None)
    if current is None:
        return EjectResult(ok=True, reason="already gone")
    if current.internal or current.live_system:
        return EjectResult(
            ok=False,
            skipped=True,
            reason="disk is internal or live; not ejecting",
        )
    if not _same_media(disk, current):
        return EjectResult(
            ok=False,
            skipped=True,
            reason="disk identity changed since the scan; not ejecting",
        )
    if sys.platform == "darwin":
        return _eject_macos(current)
    if sys.platform.startswith("linux"):
        return _eject_linux(current)
    if sys.platform == "win32":
        return _eject_windows(current)
    return EjectResult(ok=False, reason=f"eject is not implemented on {sys.platform}")


def _same_media(expected: DiscoveredDisk, current: DiscoveredDisk) -> bool:
    if expected.device != current.device:
        return False
    a, b = expected.size_bytes, current.size_bytes
    if a and b and abs(a - b) > max(1_048_576, int(a * 0.02)):
        return False
    return True


def _run(argv: list[str], timeout: float = 30.0):
    try:
        return run_readonly(argv, timeout=timeout)
    except FileNotFoundError:
        return None
    except SafetyError:
        raise


def _eject_macos(disk: DiscoveredDisk) -> EjectResult:
    ident = disk.device
    _run(["diskutil", "unmountDisk", ident], timeout=45)
    proc = _run(["diskutil", "eject", ident], timeout=45)
    if proc is None:
        return EjectResult(ok=False, reason="diskutil not found")
    if proc.returncode == 0:
        return EjectResult(ok=True)
    err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
    return EjectResult(ok=False, reason=err)


def _eject_linux(disk: DiscoveredDisk) -> EjectResult:
    errors: list[str] = []
    for part in disk.partitions:
        if not part.mountpoint:
            continue
        proc = _run(["udisksctl", "unmount", "-b", part.device], timeout=20)
        if proc is not None and proc.returncode == 0:
            continue
        proc = _run(["umount", part.mountpoint], timeout=20)
        if proc is not None and proc.returncode != 0:
            errors.append((proc.stderr or proc.stdout or f"umount {part.mountpoint} failed").strip())
    proc = _run(["udisksctl", "power-off", "-b", disk.device], timeout=20)
    if proc is not None and proc.returncode == 0:
        return EjectResult(ok=True)
    proc = _run(["eject", disk.device], timeout=20)
    if proc is not None and proc.returncode == 0:
        return EjectResult(ok=True)
    if proc is None and not errors:
        return EjectResult(
            ok=False,
            reason="udisksctl/eject not available; unmount if needed and remove the card",
        )
    err = (proc.stderr or proc.stdout or "").strip() if proc is not None else ""
    if errors:
        err = "; ".join(x for x in [*errors, err] if x)
    return EjectResult(ok=False, reason=err or "linux eject failed")


_DRIVE_LETTER = re.compile(r"^([A-Za-z]):")


def _eject_windows(disk: DiscoveredDisk) -> EjectResult:
    letters: list[str] = []
    for part in disk.partitions:
        raw = (part.mountpoint or part.device or "").replace("/", "\\").strip()
        m = _DRIVE_LETTER.match(raw)
        if m:
            letters.append(m.group(1).upper())
    letters = sorted(set(letters))
    if not letters:
        return EjectResult(
            ok=True,
            reason="no drive letter (unformatted?); remove the card from the reader",
        )
    quoted = ",".join(f"'{c}'" for c in letters)
    script = (
        f"$letters = @({quoted}); "
        "foreach ($c in $letters) { "
        "  try { Dismount-Volume -DriveLetter $c -Force -Confirm:$false "
        "-ErrorAction SilentlyContinue } catch {} "
        "  $app = New-Object -ComObject Shell.Application; "
        "  $item = $app.Namespace(17).ParseName($c + ':'); "
        "  if ($item) { $item.InvokeVerb('Eject') } "
        "}"
    )
    exe = "powershell.exe"
    proc = _run(
        [exe, "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=45,
    )
    if proc is None:
        proc = _run(
            ["pwsh", "-NoProfile", "-NonInteractive", "-Command", script],
            timeout=45,
        )
    if proc is None:
        return EjectResult(ok=False, reason="PowerShell not found")
    if proc.returncode == 0:
        return EjectResult(ok=True)
    err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
    return EjectResult(ok=False, reason=err)


def whole_disk_name(device: str) -> str:
    """Best-effort whole-disk path from a partition node."""
    name = Path(device).name
    if name.startswith("disk") and sys.platform == "darwin":
        return "/dev/" + re.sub(r"s\d+$", "", name)
    if re.match(r"mmcblk\d+p\d+$", name):
        return "/dev/" + re.sub(r"p\d+$", "", name)
    if re.match(r"nvme\d+n\d+p\d+$", name):
        return "/dev/" + re.sub(r"p\d+$", "", name)
    if re.match(r"[a-z]+p?\d+$", name) and not name.startswith("mmc"):
        return "/dev/" + re.sub(r"\d+$", "", name)
    return device
