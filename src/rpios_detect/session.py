"""Saved station session for `rpiv --resume` / `--clear` / `--status`.

The file lives in the user state directory (or `$RPIV_SESSION`). It is not a
write to SD/USB media.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rpios_detect.models import utc_now_iso

SESSION_ENV = "RPIV_SESSION"
SESSION_SCHEMA = 1
_LAST_KEYS = ("kind", "headline", "device", "size", "card_number", "os_name", "eject_note")


class SessionError(Exception):
    """Saved session file exists but cannot be used."""


@dataclass
class SessionSnapshot:
    schema: int = SESSION_SCHEMA
    tool: str = "rpiv"
    tool_version: str = ""
    started_at: str = ""
    updated_at: str = ""
    checked: int = 0
    raspberry_pi_os: int = 0
    not_raspberry_pi_os: int = 0
    unsure: int = 0
    last: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": int(self.schema),
            "tool": self.tool,
            "tool_version": self.tool_version,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "checked": int(self.checked),
            "raspberry_pi_os": int(self.raspberry_pi_os),
            "not_raspberry_pi_os": int(self.not_raspberry_pi_os),
            "unsure": int(self.unsure),
            "last": _clean_last(self.last),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SessionSnapshot:
        if not isinstance(data, Mapping):
            raise SessionError("session file is not a JSON object")
        try:
            schema = int(data.get("schema") or SESSION_SCHEMA)
            checked = int(data.get("checked") or 0)
            yes = int(data.get("raspberry_pi_os") or 0)
            no = int(data.get("not_raspberry_pi_os") or 0)
            unsure = int(data.get("unsure") or 0)
        except (TypeError, ValueError) as exc:
            raise SessionError("session file has invalid counts") from exc
        if schema < 1 or min(checked, yes, no, unsure) < 0:
            raise SessionError("session file has invalid counts")
        last_raw = data.get("last")
        last = _clean_last(last_raw) if last_raw else None
        return cls(
            schema=schema,
            tool=str(data.get("tool") or "rpiv"),
            tool_version=str(data.get("tool_version") or ""),
            started_at=str(data.get("started_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            checked=checked,
            raspberry_pi_os=yes,
            not_raspberry_pi_os=no,
            unsure=unsure,
            last=last,
        )


def snapshot_from_counts(
    *,
    started_at: str,
    checked: int,
    raspberry_pi_os: int,
    not_raspberry_pi_os: int,
    unsure: int,
    last: dict[str, Any] | None,
    tool_version: str,
) -> SessionSnapshot:
    now = utc_now_iso()
    return SessionSnapshot(
        schema=SESSION_SCHEMA,
        tool="rpiv",
        tool_version=tool_version,
        started_at=started_at or now,
        updated_at=now,
        checked=checked,
        raspberry_pi_os=raspberry_pi_os,
        not_raspberry_pi_os=not_raspberry_pi_os,
        unsure=unsure,
        last=_clean_last(last),
    )


def default_session_path(
    *,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> Path:
    env = os.environ if environ is None else environ
    override = (env.get(SESSION_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    plat = sys.platform if platform is None else platform
    home = Path.home()
    if plat == "darwin":
        return home / "Library" / "Application Support" / "rpiv" / "session.json"
    if plat.startswith("win"):
        base = env.get("LOCALAPPDATA")
        root = Path(base) if base else home / "AppData" / "Local"
        return root / "rpiv" / "session.json"
    xdg = (env.get("XDG_STATE_HOME") or "").strip()
    root = Path(xdg).expanduser() if xdg else home / ".local" / "state"
    return root / "rpiv" / "session.json"


def load_session(path: Path) -> SessionSnapshot | None:
    path = Path(path)
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SessionError(f"could not read session file: {exc}") from exc
    if not raw.strip():
        raise SessionError("session file is empty")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SessionError("session file is not valid JSON") from exc
    return SessionSnapshot.from_dict(data)


def save_session(path: Path, snapshot: SessionSnapshot) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = snapshot.to_dict()
    if not payload.get("updated_at"):
        payload["updated_at"] = utc_now_iso()
    text = json.dumps(payload, indent=2) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def clear_session(path: Path) -> bool:
    path = Path(path)
    if not path.is_file():
        return False
    path.unlink()
    return True


def format_session_status(snapshot: SessionSnapshot, *, path: Path) -> str:
    last = "none yet"
    if snapshot.last:
        n = snapshot.last.get("card_number") or snapshot.checked
        headline = snapshot.last.get("headline") or snapshot.last.get("kind") or ""
        device = snapshot.last.get("device") or ""
        last = f"#{n}  {headline}".strip()
        if device:
            last += f"  {device}"
    started = snapshot.started_at or "?"
    updated = snapshot.updated_at or "?"
    return (
        f"rpiv session\n"
        f"  file     {path}\n"
        f"  started  {started}\n"
        f"  updated  {updated}\n"
        f"  checked  {snapshot.checked}\n"
        f"  yes      {snapshot.raspberry_pi_os} Raspberry Pi OS\n"
        f"  no       {snapshot.not_raspberry_pi_os} not\n"
        f"  unsure   {snapshot.unsure}\n"
        f"  last     {last}\n"
    )


def _clean_last(last: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not last:
        return None
    if not isinstance(last, Mapping):
        raise SessionError("session last-card field is not an object")
    out: dict[str, Any] = {}
    for key in _LAST_KEYS:
        if key not in last:
            continue
        value = last[key]
        if value is None:
            continue
        if key == "card_number":
            try:
                out[key] = int(value)
            except (TypeError, ValueError) as exc:
                raise SessionError("session last-card number is invalid") from exc
            continue
        out[key] = str(value)
    return out or None
