"""Collect boot/root snapshots from a FileView. Host-agnostic."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rpios_detect.fs import (
    MAX_TEXT_BYTES,
    FileView,
    PrefixView,
    walk_names,
)

BOOT_TEXT_BASENAMES = frozenset(
    {
        "issue.txt",
        "issue",
        "cmdline.txt",
        "config.txt",
        "meta-data",
        "user-data",
        "network-config",
        "dietpi.txt",
        "dietpi.txt",
        "os.json",
        "recovery.cmdline",
        "os_config.json",
        "partitions.json",
        "readme",
        "readme.txt",
        "readme.md",
        "os-release",
        "ubuntu.env",
        "kalipi.txt",
        "kali.txt",
        "syslinux.cfg",
        "cmdline",
        "recovery.nfo",
        "installed_os.json",
    }
)

ROOT_TEXT_PATHS = (
    "etc/os-release",
    "etc/rpi-issue",
    "etc/rpi-issue",
    "etc/rpi/issue",
    "usr/lib/os-release",
    "etc/apt/sources.list.d/raspi.list",
    "etc/apt/sources.list.d/raspi.list",
    "etc/apt/sources.list.d/raspi.sources",
    "etc/debian_version",
    "etc/dietpi/func/dietpi-globals",
    "boot/firmware/issue.txt",
    "boot/issue.txt",
)


@dataclass(frozen=True)
class BootSnapshot:
    files: frozenset[str]
    dirs: frozenset[str]
    texts: dict[str, str]
    label: str | None = None

    def has_file(self, basename: str) -> bool:
        want = basename.lower()
        return any(Path(f).name == want for f in self.files)

    def has_path(self, relpath: str) -> bool:
        return relpath.lower().replace("\\", "/") in self.files

    def has_dir(self, name: str) -> bool:
        want = name.lower().rstrip("/")
        return want in self.dirs or any(d == want or d.endswith("/" + want) for d in self.dirs)

    def texts_named(self, basename: str) -> list[str]:
        want = basename.lower()
        return [text for path, text in self.texts.items() if Path(path).name == want]

    def text(self, basename: str) -> str:
        found = self.texts_named(basename)
        return found[0] if found else ""

    def any_filename_contains(self, needle: str) -> bool:
        n = needle.lower()
        return any(n in Path(f).name for f in self.files)


@dataclass(frozen=True)
class RootSnapshot:
    readable: bool
    files: frozenset[str]
    texts: dict[str, str]
    label: str | None = None
    has_raspi_config: bool = False

    def text(self, relpath: str) -> str:
        return self.texts.get(relpath.lower().replace("\\", "/"), "")

    def has_file(self, relpath: str) -> bool:
        return relpath.lower().replace("\\", "/") in self.files


@dataclass(frozen=True)
class PartitionLayout:
    table: str
    size_bytes: int | None
    trailing_free_bytes: int | None
    first_partition_start_bytes: int | None
    has_fat: bool = False
    has_linux: bool = False
    fat_label: str | None = None
    linux_label: str | None = None
    partition_count: int = 0


@dataclass
class Snapshots:
    boot: BootSnapshot | None
    root: RootSnapshot | None
    layout: PartitionLayout | None
    warnings: list[str] = field(default_factory=list)
    boot_source: str | None = None


def _read_named_texts(view: FileView, files: set[str]) -> dict[str, str]:
    texts: dict[str, str] = {}
    for rel in files:
        base = Path(rel).name.lower()
        if base not in BOOT_TEXT_BASENAMES:
            continue
        try:
            raw = view.read_text(rel, max_bytes=MAX_TEXT_BYTES)
        except OSError:
            continue
        if raw is not None:
            texts[rel.lower()] = raw
    return texts


def collect_boot(view: FileView, *, label: str | None = None) -> BootSnapshot:
    files, dirs = walk_names(view)
    texts = _read_named_texts(view, files)
    return BootSnapshot(
        files=frozenset(files),
        dirs=frozenset(dirs),
        texts=texts,
        label=label,
    )


def collect_root(view: FileView, *, label: str | None = None) -> RootSnapshot:
    files: set[str] = set()
    texts: dict[str, str] = {}
    for rel in ROOT_TEXT_PATHS:
        if view.exists(rel):
            files.add(rel.lower())
            try:
                raw = view.read_text(rel)
            except OSError:
                raw = None
            if raw is not None:
                texts[rel.lower()] = raw
    has_raspi = view.exists("usr/bin/raspi-config") or view.exists("usr/sbin/raspi-config")
    if has_raspi:
        files.add("usr/bin/raspi-config")
    # Cheap extra markers without walking the whole rootfs.
    extra_markers = (
        "etc/kali-version",
        "etc/manjaro-release",
        "etc/arch-release",
        "etc/alpine-release",
        "etc/fedora-release",
        "boot/dietpi/.version",
    )
    for rel in extra_markers:
        if view.exists(rel):
            files.add(rel.lower())
            try:
                raw = view.read_text(rel)
            except OSError:
                raw = None
            if raw is not None:
                texts[rel.lower()] = raw
    return RootSnapshot(
        readable=True,
        files=frozenset(files),
        texts=texts,
        label=label,
        has_raspi_config=has_raspi,
    )


_BOOT_HINTS = (
    "issue.txt",
    "bootcode.bin",
    "config.txt",
    "cmdline.txt",
    "start.elf",
    "start4.elf",
    "kernel8.img",
    "kernel_2712.img",
)


def find_boot_view(view: FileView) -> tuple[FileView, str]:
    """If this directory is a rootfs, prefer boot/firmware or boot/."""
    if any(view.exists(name) for name in _BOOT_HINTS):
        return view, "."
    if view.exists("etc/os-release") or view.exists("etc/rpi-issue"):
        for sub in ("boot/firmware", "bootfs", "boot"):
            if view.is_dir(sub) and any(
                PrefixView(view, sub).exists(name) for name in ("issue.txt", "config.txt", "bootcode.bin")
            ):
                return PrefixView(view, sub), sub
    for sub in ("boot", "bootfs", "firmware", "boot/firmware"):
        if view.is_dir(sub) and any(
            PrefixView(view, sub).exists(name) for name in _BOOT_HINTS
        ):
            return PrefixView(view, sub), sub
    return view, "."
