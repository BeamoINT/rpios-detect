"""Flicker-free station screen for `rpiv` / `rpios-detect watch`.

TTY mode paints a full frame in the alternate screen, then skips the write
when the next frame is identical. That is what stops the blink: the poll
loop runs many times a second, but the terminal only changes when the
station actually changes state.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from typing import TextIO

_ANSI_RE = re.compile(r"\033\[[0-9;]*[A-Za-z]")

# Alternate screen + hidden cursor + disable wrap (wrap is a common flicker source).
ENTER_ALT = "\033[?1049h\033[?25l\033[?7l"
LEAVE_ALT = "\033[?7h\033[?25h\033[?1049l"
HOME = "\033[H"
ERASE_DOWN = "\033[J"
# Synchronized output (kitty, iTerm2, ghostty, VTE): one atomic frame, no tear.
SYNC_BEGIN = "\033[?2026h"
SYNC_END = "\033[?2026l"

_BOX_UNI = {"tl": "┌", "tr": "┐", "bl": "└", "br": "┘", "h": "─", "v": "│"}
_BOX_ASC = {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|"}


@dataclass
class StationState:
    version: str = ""
    phase: str = "waiting"  # waiting | settling | scanning | verdict | error
    eject: bool = True
    color: bool = False
    unicode: bool = True
    device: str = ""
    size: str = ""
    hint: str = ""
    kind: str = ""
    card_number: int = 0
    headline: str = ""
    detail: str = ""
    eject_note: str = ""
    checked: int = 0
    yes: int = 0
    no: int = 0
    unsure: int = 0
    last_kind: str = ""
    last_headline: str = ""
    last_device: str = ""
    extra_lines: list[str] = field(default_factory=list)


class _C:
    def __init__(self, on: bool) -> None:
        if on:
            self.reset = "\033[0m"
            self.bold = "\033[1m"
            self.dim = "\033[2m"
            self.green = "\033[32m"
            self.red = "\033[31m"
            self.yellow = "\033[33m"
            self.cyan = "\033[36m"
            self.inv_green = "\033[42;30;1m"
            self.inv_red = "\033[41;37;1m"
            self.inv_yellow = "\033[43;30;1m"
            self.inv_cyan = "\033[46;30;1m"
        else:
            self.reset = self.bold = self.dim = ""
            self.green = self.red = self.yellow = self.cyan = ""
            self.inv_green = self.inv_red = self.inv_yellow = self.inv_cyan = ""


def visible_len(text: str) -> int:
    return len(_ANSI_RE.sub("", text))


def stdout_supports_unicode(stream: TextIO) -> bool:
    enc = getattr(stream, "encoding", None) or "utf-8"
    try:
        "┌".encode(enc)
        return True
    except (LookupError, UnicodeEncodeError):
        return False


def _pad(text: str, width: int, align: str = "left") -> str:
    extra = width - visible_len(text)
    if extra <= 0:
        return text
    if align == "center":
        left = extra // 2
        return (" " * left) + text + (" " * (extra - left))
    if align == "right":
        return (" " * extra) + text
    return text + (" " * extra)


def _clip(text: str, width: int, *, ellipsis: str = "…") -> str:
    if visible_len(text) <= width:
        return text
    raw = _ANSI_RE.sub("", text)
    if width <= 1:
        return raw[:width]
    mark = ellipsis if len(ellipsis) < width else ""
    return raw[: width - len(mark)] + mark


def _box(inner_lines: list[str], width: int, color: str, reset: str, *, glyphs: dict[str, str]) -> list[str]:
    inner_w = max(width - 2, 8)
    body_w = inner_w - 2
    top = color + glyphs["tl"] + (glyphs["h"] * inner_w) + glyphs["tr"] + reset
    bot = color + glyphs["bl"] + (glyphs["h"] * inner_w) + glyphs["br"] + reset
    out = [top]
    for line in inner_lines:
        body = _pad(_clip(line, body_w, ellipsis=glyphs.get("el", "…")), body_w, "center")
        out.append(color + glyphs["v"] + reset + " " + body + " " + color + glyphs["v"] + reset)
    out.append(bot)
    return out


def render_station(state: StationState, *, width: int | None = None, height: int | None = None) -> str:
    """Build one complete screen. Callers skip writing if this string is unchanged."""
    size = shutil.get_terminal_size(fallback=(80, 24))
    width = max(48, min(width or size.columns, 88))
    height = max(16, height or size.lines)
    c = _C(state.color)
    glyphs = dict(_BOX_UNI if state.unicode else _BOX_ASC)
    glyphs["el"] = "…" if state.unicode else "..."
    rule = c.dim + (glyphs["h"] * width) + c.reset
    mode = "scan then eject" if state.eject else "scan only"
    header_l = f"{c.bold}rpiv{c.reset}  {c.dim}{state.version}{c.reset}"
    header_r = f"{c.dim}insert · scan · eject{c.reset}"
    gap = max(1, width - visible_len(header_l) - visible_len(header_r))
    lines = [
        _pad(header_l + (" " * gap) + header_r, width),
        _pad(f"{c.dim}{mode} · Ctrl+C stops{c.reset}", width),
        rule,
        "",
    ]

    phase = state.phase
    if phase == "waiting":
        color = c.cyan
        inv = c.inv_cyan
        title = "WAITING FOR A CARD"
        sub = state.hint or "Insert a MicroSD. The verdict stays until the next card."
        box = ["", inv + "  INSERT  " + c.reset, "", f"{c.bold}{title}{c.reset}", "", sub, ""]
    elif phase == "settling":
        color = c.yellow
        inv = c.inv_yellow
        title = "CARD DETECTED"
        sub = state.hint or "Waiting until the reader settles…"
        box = [
            "",
            inv + "  SETTLE  " + c.reset,
            "",
            f"{c.bold}{title}{c.reset}",
            "",
            f"{state.device}  {state.size}",
            "",
            sub,
            "",
        ]
    elif phase == "scanning":
        color = c.yellow
        inv = c.inv_yellow
        title = "SCANNING"
        sub = state.hint or "Read-only — nothing is written to the card."
        box = [
            "",
            inv + "  SCAN  " + c.reset,
            "",
            f"{c.bold}{title}{c.reset}",
            "",
            f"{state.device}  {state.size}",
            "",
            sub,
            "",
        ]
    elif phase == "error":
        color = c.red
        inv = c.inv_red
        title = "SCAN FAILED"
        sub = state.hint or "Leave the card mounted and check permissions."
        box = ["", inv + "  ERROR  " + c.reset, "", f"{c.bold}{title}{c.reset}", "", sub, ""]
    else:
        kind = state.kind
        if kind == "raspberry_pi_os":
            color = c.green
            inv = c.inv_green
            badge = "  YES  "
            title = state.headline or "RASPBERRY PI OS"
        elif kind == "unsure":
            color = c.yellow
            inv = c.inv_yellow
            badge = "  ?  "
            title = state.headline or "UNSURE"
        else:
            color = c.red
            inv = c.inv_red
            badge = "  NO  "
            title = state.headline or "NOT RASPBERRY PI OS"
        sub = state.detail or f"{state.device}  {state.size}"
        box = [
            "",
            inv + badge + c.reset,
            "",
            f"{c.bold}{color}{title}{c.reset}",
            "",
            f"card {state.card_number}   {sub}",
            "",
            state.eject_note or "Insert the next card when you are ready.",
        ]
        extras = [line for line in state.extra_lines[:3] if line]
        if extras:
            box.append("")
            box.extend(extras)
        box.append("")

    lines.extend(_box(box, width, color, c.reset, glyphs=glyphs))
    lines.append("")
    lines.append(rule)

    last = "none yet"
    if state.last_headline:
        n = state.card_number if phase == "verdict" else state.checked
        last = f"#{n}  {state.last_headline}"
        if state.last_device:
            last += f"  {c.dim}{state.last_device}{c.reset}"
    elif state.checked and state.headline:
        last = f"#{state.checked}  {state.headline}"
    lines.append(_pad(f"  last     {last}", width))
    lines.append(
        _pad(
            f"  session  {state.checked} checked   "
            f"{c.green}{state.yes} yes{c.reset}   "
            f"{c.red}{state.no} no{c.reset}   "
            f"{c.yellow}{state.unsure} unsure{c.reset}",
            width,
        )
    )
    lines.append("")
    # Keep the frame a stable height so leftover glyphs never peek through.
    target = min(max(height - 1, 16), 24)
    while len(lines) < target:
        lines.append("")
    return "\n".join(_pad(line, width) for line in lines[:target]) + "\n"


class StationScreen:
    """Paint a station frame. Identical frames are not written (no flicker)."""

    def __init__(self, stream: TextIO, *, interactive: bool) -> None:
        self.stream = stream
        self.interactive = interactive
        self._last = ""
        self._open = False
        self._cols: int | None = None
        self._rows: int | None = None

    def open(self) -> None:
        if not self.interactive or self._open:
            return
        try:
            self.stream.write(ENTER_ALT + HOME + ERASE_DOWN)
            self.stream.flush()
            self._open = True
        except Exception:
            self.interactive = False

    def close(self) -> None:
        if not self._open:
            return
        try:
            self.stream.write(LEAVE_ALT)
            self.stream.flush()
        except Exception:
            pass
        self._open = False
        self._last = ""
        self._cols = None
        self._rows = None

    def paint(self, frame: str) -> None:
        if frame == self._last:
            return
        self._last = frame
        try:
            if self.interactive:
                try:
                    size = shutil.get_terminal_size(fallback=(80, 24))
                    resized = size.columns != self._cols or size.lines != self._rows
                    self._cols, self._rows = size.columns, size.lines
                except Exception:
                    resized = True
                tail = ERASE_DOWN if resized else ""
                self.stream.write(SYNC_BEGIN + HOME + frame.rstrip("\n") + "\n" + tail + SYNC_END)
            else:
                self.stream.write(frame if frame.endswith("\n") else frame + "\n")
            self.stream.flush()
        except Exception:
            return
