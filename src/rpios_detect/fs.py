"""Filesystem abstraction used by detection.

Detection never talks to diskutil; it only sees this view of files.
Lookups are case-insensitive because boot partitions are typically FAT.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, runtime_checkable


MAX_TEXT_BYTES = 64 * 1024
MAX_WALK_FILES = 800
MAX_WALK_DEPTH = 4


class AccessError(OSError):
    """Readable path exists but this process cannot open it."""


@runtime_checkable
class FileView(Protocol):
    def exists(self, relpath: str = "") -> bool: ...
    def is_dir(self, relpath: str = "") -> bool: ...
    def listdir(self, relpath: str = "") -> list[str]: ...
    def read_bytes(self, relpath: str, max_bytes: int = MAX_TEXT_BYTES) -> bytes | None: ...
    def read_text(self, relpath: str, max_bytes: int = MAX_TEXT_BYTES) -> str | None: ...


def _norm(relpath: str) -> str:
    relpath = relpath.replace("\\", "/").strip("/")
    return relpath


class DirectoryView:
    """Read files from a directory tree. Never writes."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _resolve(self, relpath: str) -> Path | None:
        relpath = _norm(relpath)
        if not relpath:
            return self.root
        direct = self.root.joinpath(*relpath.split("/"))
        try:
            if direct.exists():
                return direct
        except OSError:
            return direct
        # Case-insensitive fallback (Linux mounts of FAT can be case-sensitive).
        current = self.root
        for part in relpath.split("/"):
            if not current.is_dir():
                return None
            try:
                names = os.listdir(current)
            except OSError:
                return None
            match = next((n for n in names if n.lower() == part.lower()), None)
            if match is None:
                return None
            current = current / match
        return current

    def exists(self, relpath: str = "") -> bool:
        path = self._resolve(relpath)
        if path is None:
            return False
        try:
            return path.exists()
        except OSError:
            return False

    def is_dir(self, relpath: str = "") -> bool:
        path = self._resolve(relpath)
        if path is None:
            return False
        try:
            return path.is_dir()
        except OSError:
            return False

    def listdir(self, relpath: str = "") -> list[str]:
        path = self._resolve(relpath)
        if path is None:
            return []
        try:
            return os.listdir(path)
        except PermissionError as exc:
            raise AccessError(f"Permission denied reading {path}") from exc
        except OSError:
            return []

    def read_bytes(self, relpath: str, max_bytes: int = MAX_TEXT_BYTES) -> bytes | None:
        path = self._resolve(relpath)
        if path is None or not path.is_file():
            return None
        try:
            with path.open("rb") as fh:
                return fh.read(max_bytes)
        except PermissionError as exc:
            raise AccessError(f"Permission denied reading {path}") from exc
        except OSError:
            return None

    def read_text(self, relpath: str, max_bytes: int = MAX_TEXT_BYTES) -> str | None:
        data = self.read_bytes(relpath, max_bytes=max_bytes)
        if data is None:
            return None
        return data.decode("utf-8", errors="replace")


class PrefixView:
    """Restrict a view to a subdirectory (e.g. boot/firmware)."""

    def __init__(self, inner: FileView, prefix: str) -> None:
        self.inner = inner
        self.prefix = _norm(prefix)

    def _join(self, relpath: str) -> str:
        relpath = _norm(relpath)
        if not relpath:
            return self.prefix
        return f"{self.prefix}/{relpath}" if self.prefix else relpath

    def exists(self, relpath: str = "") -> bool:
        return self.inner.exists(self._join(relpath))

    def is_dir(self, relpath: str = "") -> bool:
        return self.inner.is_dir(self._join(relpath))

    def listdir(self, relpath: str = "") -> list[str]:
        return self.inner.listdir(self._join(relpath))

    def read_bytes(self, relpath: str, max_bytes: int = MAX_TEXT_BYTES) -> bytes | None:
        return self.inner.read_bytes(self._join(relpath), max_bytes=max_bytes)

    def read_text(self, relpath: str, max_bytes: int = MAX_TEXT_BYTES) -> str | None:
        return self.inner.read_text(self._join(relpath), max_bytes=max_bytes)


class DictView:
    """In-memory file tree for unit tests. Keys are relative paths."""

    def __init__(self, files: dict[str, bytes | str]) -> None:
        self._files: dict[str, bytes] = {}
        for key, value in files.items():
            path = _norm(key).lower()
            if isinstance(value, str):
                self._files[path] = value.encode("utf-8")
            else:
                self._files[path] = value

    def _dirs(self) -> set[str]:
        dirs: set[str] = {""}
        for path in self._files:
            parts = path.split("/")
            for i in range(len(parts)):
                dirs.add("/".join(parts[:i]))
        return dirs

    def exists(self, relpath: str = "") -> bool:
        key = _norm(relpath).lower()
        return key in self._files or key in self._dirs()

    def is_dir(self, relpath: str = "") -> bool:
        key = _norm(relpath).lower()
        if key in self._files:
            return False
        return key in self._dirs()

    def listdir(self, relpath: str = "") -> list[str]:
        prefix = _norm(relpath).lower()
        names: set[str] = set()
        for path in list(self._files) + [d for d in self._dirs() if d]:
            if prefix:
                if path == prefix or not path.startswith(prefix + "/"):
                    continue
                rest = path[len(prefix) + 1 :]
            else:
                rest = path
            if not rest:
                continue
            names.add(rest.split("/", 1)[0])
        return sorted(names)

    def read_bytes(self, relpath: str, max_bytes: int = MAX_TEXT_BYTES) -> bytes | None:
        key = _norm(relpath).lower()
        data = self._files.get(key)
        if data is None:
            return None
        return data[:max_bytes]

    def read_text(self, relpath: str, max_bytes: int = MAX_TEXT_BYTES) -> str | None:
        data = self.read_bytes(relpath, max_bytes=max_bytes)
        if data is None:
            return None
        return data.decode("utf-8", errors="replace")


_SKIP_DIR_NAMES = frozenset(
    {
        ".spotlight-v100",
        ".fseventsd",
        ".trashes",
        ".temporaryitems",
        "system volume information",
        "$recycle.bin",
    }
)
_NO_RECURSE_DIRS = frozenset({"overlays"})


def walk_names(view: FileView, *, max_depth: int = MAX_WALK_DEPTH) -> tuple[set[str], set[str]]:
    """Return (files, dirs) as lower-case relative POSIX paths."""
    files: set[str] = set()
    dirs: set[str] = set()

    def rec(prefix: str, depth: int) -> None:
        if len(files) >= MAX_WALK_FILES:
            return
        try:
            names = view.listdir(prefix)
        except AccessError:
            if prefix == "":
                raise
            return
        except OSError:
            return
        for name in names:
            if name.lower() in _SKIP_DIR_NAMES:
                continue
            rel = f"{prefix}/{name}" if prefix else name
            rel_key = rel.replace("\\", "/")
            try:
                isdir = view.is_dir(rel)
            except OSError:
                continue
            if isdir:
                dirs.add(rel_key.lower())
                if depth < max_depth and name.lower() not in _NO_RECURSE_DIRS:
                    rec(rel, depth + 1)
            else:
                files.add(rel_key.lower())
                if len(files) >= MAX_WALK_FILES:
                    return

    rec("", 0)
    return files, dirs


def looks_like_pi_boot(files: Iterable[str], dirs: Iterable[str], label: str | None = None) -> bool:
    """Cheap pre-filter: firmware names, Pi labels, or official markers."""
    names = {Path(f).name.lower() for f in files}
    dirset = {d.lower().rstrip("/") for d in dirs}
    if label and label.lower() in {"boot", "bootfs", "bootfs"}:
        return True
    firmware = {
        "bootcode.bin",
        "start.elf",
        "start4.elf",
        "fixup.dat",
        "config.txt",
        "cmdline.txt",
        "issue.txt",
        "kernel.img",
        "kernel7.img",
        "kernel7l.img",
        "kernel8.img",
        "kernel_2712.img",
        "licence.broadcom",
    }
    if names & firmware:
        return True
    if any(n.endswith(".dtb") and n.startswith("bcm27") for n in names):
        return True
    if any(d == "overlays" or d.endswith("/overlays") for d in dirset):
        return True
    return False
