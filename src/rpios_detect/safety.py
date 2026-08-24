"""Read-only subprocess guard.

Every host command goes through here. Destructive disk operations are refused
before exec. Mounts must be read-only.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

FORBIDDEN_RE = re.compile(
    r"""
    eraseDisk|eraseVolume|partitionDisk|zeroDisk|randomDisk|
    \bmkfs\b|\bnewfs\b|\bwipefs\b|\bdiskpart\b|
    format-volume|clear-disk|initialize-disk|new-partition|
    \bdd\b|rawdisk
    """,
    re.IGNORECASE | re.VERBOSE,
)

_ALLOWED_NAMES = frozenset(
    {
        "diskutil",
        "lsblk",
        "blkid",
        "findmnt",
        "mount",
        "umount",
        "umount",
        "findmnt",
        "mount.fat",
        "mount.vfat",
        "mount.exfat",
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
        "wmic",
        "wmic.exe",
        "uname",
        "sysctl",
        "id",
        "whoami",
    }
)

_DISKUTIL_SUBCOMMANDS = frozenset(
    {"list", "info", "mount", "unmount", "unmountdisk"}
)


class SafetyError(RuntimeError):
    """A command was refused because it is not a read-only inspection."""


def _base_name(argv0: str) -> str:
    return Path(argv0).name.lower()


def _ensure_allowed(argv: list[str]) -> None:
    if not argv:
        raise SafetyError("empty command")
    joined = " ".join(argv)
    if FORBIDDEN_RE.search(joined):
        raise SafetyError(f"refusing potentially destructive command: {argv!r}")
    name = _base_name(argv[0])
    if name not in _ALLOWED_NAMES and not name.startswith("mount"):
        raise SafetyError(f"binary not on the read-only allowlist: {argv[0]!r}")
    if name == "diskutil":
        sub = argv[1].lower() if len(argv) > 1 else ""
        if sub not in _DISKUTIL_SUBCOMMANDS:
            raise SafetyError(f"diskutil subcommand not allowed: {sub!r}")
        if sub == "mount" and "readOnly" not in argv:
            raise SafetyError("diskutil mount without readOnly is refused")
    if name in {"mount", "mount.fat", "mount.vfat", "mount.exfat"} or (
        name.startswith("mount") and name != "umount"
    ):
        lower = joined.lower()
        if "ro" not in lower and "read-only" not in lower and "readonly" not in lower:
            raise SafetyError("mount without a read-only flag is refused")


def run_readonly(
    argv: list[str],
    *,
    timeout: float = 30.0,
    check: bool = False,
    env: dict[str, str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a host command that must be inspection-only."""
    _ensure_allowed(argv)
    return subprocess.run(
        argv,
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        cwd=cwd,
    )
