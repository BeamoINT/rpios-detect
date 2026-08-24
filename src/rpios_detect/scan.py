"""Orchestrate discovery, read-only mounts, snapshots, and detection."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from rpios_detect import __version__
from rpios_detect.detect import Detection, detect
from rpios_detect.fs import AccessError, DirectoryView, looks_like_pi_boot
from rpios_detect.image import inspect_image
from rpios_detect.models import (
    Bus,
    Confidence,
    EvidenceItem,
    HostInfo,
    MediaInfo,
    MediaKind,
    PartitionInfo,
    PartitionRole,
    PartitionTable,
    ScanReport,
    TargetResult,
    Verdict,
    host_info,
    utc_now_iso,
)
from rpios_detect.probe import DiscoveredDisk, DiscoveredPartition
from rpios_detect.snapshot import (
    BootSnapshot,
    PartitionLayout,
    RootSnapshot,
    collect_boot,
    collect_root,
    find_boot_view,
)

_MOUNTED_BY_US: list[tuple[str, str]] = []  # (platform, token)


def scan_targets(targets: list[str], *, all_disks: bool = False, verbose: bool = False) -> ScanReport:
    warnings_global: list[str] = []
    results: list[TargetResult] = []
    try:
        if not targets:
            for disk in discover_disks(all_disks=all_disks):
                results.append(inspect_discovered(disk, verbose=verbose))
        else:
            for raw in targets:
                results.append(inspect_user_target(raw, verbose=verbose))
    finally:
        _cleanup_mounts()
    return ScanReport(
        tool="rpios-detect",
        tool_version=__version__,
        scanned_at=utc_now_iso(),
        host=host_info(),
        results=results,
    )


def discover_disks(*, all_disks: bool = False) -> list[DiscoveredDisk]:
    if sys.platform == "darwin":
        from rpios_detect.probe_macos import discover_macos

        return discover_macos(all_disks=all_disks)
    if sys.platform.startswith("linux"):
        from rpios_detect.probe_linux import discover_linux

        return discover_linux(all_disks=all_disks)
    if sys.platform == "win32":
        from rpios_detect.probe_windows import discover_windows

        return discover_windows(all_disks=all_disks)
    return []


def inspect_user_target(raw: str, *, verbose: bool = False) -> TargetResult:
    # Windows drive letter
    if len(raw) in {1, 2} and raw[0].isalpha() and (len(raw) == 1 or raw[1] == ":"):
        from rpios_detect.probe_windows import inspect_drive_letter

        return inspect_discovered(inspect_drive_letter(raw), verbose=verbose)
    path = Path(raw)
    if path.is_dir():
        return inspect_directory(path, verbose=verbose)
    if path.is_file():
        return inspect_image_file(path, verbose=verbose)
    if raw.startswith("/dev/") or raw.startswith("disk"):
        return inspect_device(raw, verbose=verbose)
    raise FileNotFoundError(f"target not found: {raw}")


def inspect_directory(path: Path, *, verbose: bool = False) -> TargetResult:
    view = DirectoryView(path)
    boot_view, src = find_boot_view(view)
    boot = collect_boot(boot_view, label=path.name)
    root = None
    if view.exists("etc/os-release") or view.exists("etc/rpi-issue") or view.exists("etc/rpi-issue"):
        root = collect_root(view)
    layout = None
    detection = detect(boot, root, layout, verbose=verbose)
    warnings = list(detection.warnings)
    if src != ".":
        warnings.append(f"used boot files from {src}/")
    try:
        files, dirs = set(boot.files), set(boot.dirs)
        if not looks_like_pi_boot(files, dirs, path.name) and detection.verdict == Verdict.NOT_PI:
            warnings.append("directory does not look like a Raspberry Pi boot volume")
    except Exception:
        pass
    return _result_from_detection(
        target=str(path),
        media=MediaInfo(kind=MediaKind.DIRECTORY, size_bytes=None, partition_table=PartitionTable.UNKNOWN, bus=Bus.UNKNOWN),
        partitions=[],
        detection=detection,
        extra_warnings=warnings,
    )


def inspect_image_file(path: Path, *, verbose: bool = False) -> TargetResult:
    layout, parts, fat_view = inspect_image(path)
    boot = collect_boot(fat_view, label=None) if fat_view is not None else None
    detection = detect(boot, None, layout, verbose=verbose)
    partitions = [
        PartitionInfo(
            id=f"p{p.index}",
            type=p.type_name,
            label=p.name,
            size_bytes=p.sector_count * 512,
            role=PartitionRole.BOOT if "fat" in p.type_name else PartitionRole.ROOT if p.type_name == "linux" else PartitionRole.UNKNOWN,
            mountpoint=None,
            readable="fat" in p.type_name and fat_view is not None,
        )
        for p in parts
    ]
    warnings = list(detection.warnings)
    warnings.append("raw image opened read-only; Linux rootfs inside the image is not mounted")
    return _result_from_detection(
        target=str(path),
        media=MediaInfo(
            kind=MediaKind.IMAGE,
            size_bytes=layout.size_bytes,
            partition_table=PartitionTable(layout.table) if layout.table in {"mbr", "gpt"} else PartitionTable.UNKNOWN,
            bus=Bus.UNKNOWN,
        ),
        partitions=partitions,
        detection=detection,
        extra_warnings=warnings,
        live=False,
    )


def inspect_device(spec: str, *, verbose: bool = False) -> TargetResult:
    device = spec if spec.startswith("/dev/") else f"/dev/{spec}"
    disks = discover_disks(all_disks=True)
    for disk in disks:
        if disk.device == device or disk.device.rstrip("s0123456789") == device:
            return inspect_discovered(disk, verbose=verbose)
        if Path(disk.device).name == Path(device).name:
            return inspect_discovered(disk, verbose=verbose)
        for part in disk.partitions:
            if part.device == device or part.id == Path(device).name:
                return inspect_discovered(disk, verbose=verbose)
    raise FileNotFoundError(f"block device not found: {device}")


def inspect_discovered(disk: DiscoveredDisk, *, verbose: bool = False) -> TargetResult:
    warnings = list(disk.warnings)
    boot: BootSnapshot | None = None
    root: RootSnapshot | None = None
    boot_part: DiscoveredPartition | None = None
    root_part: DiscoveredPartition | None = None

    for part in disk.partitions:
        fs = (part.fstype or "").lower()
        if boot_part is None and ("fat" in fs or fs in {"vfat", "msdos", "exfat"} or (part.label or "").lower() in {"boot", "bootfs"}):
            boot_part = part
        if root_part is None and (fs in {"ext4", "ext3", "ext2", "btrfs", "linux"} or (part.label or "").lower() == "rootfs"):
            root_part = part

    if boot_part is None and disk.partitions:
        # last chance: first readable mount
        boot_part = next((p for p in disk.partitions if p.mountpoint), disk.partitions[0])

    if boot_part:
        mp, warning = _ensure_mounted(disk, boot_part)
        if warning:
            warnings.append(warning)
        if mp:
            try:
                boot = collect_boot(DirectoryView(Path(mp)), label=boot_part.label)
                boot_part.readable = True
            except AccessError as exc:
                warnings.append(_permission_hint(str(exc), mp))
            except OSError as exc:
                warnings.append(f"could not read boot volume {mp}: {exc}")

    if root_part:
        mp, warning = _ensure_mounted(disk, root_part)
        if warning:
            warnings.append(warning)
        if mp:
            try:
                root = collect_root(DirectoryView(Path(mp)), label=root_part.label)
                root_part.readable = True
            except AccessError as exc:
                warnings.append(_permission_hint(str(exc), mp))
            except OSError as exc:
                warnings.append(f"could not read root volume {mp}: {exc}")
        elif sys.platform == "darwin" and (root_part.fstype or "").lower() in {"linux", "ext4", "ext3"}:
            warnings.append(
                "Linux root partition is not readable on macOS without extra filesystem support; boot-partition evidence is enough for official Raspberry Pi OS"
            )

    layout = PartitionLayout(
        table=disk.partition_table.value,
        size_bytes=disk.size_bytes,
        trailing_free_bytes=disk.trailing_free_bytes,
        first_partition_start_bytes=None,
        has_fat=boot_part is not None,
        has_linux=root_part is not None,
        fat_label=boot_part.label if boot_part else None,
        linux_label=root_part.label if root_part else None,
        partition_count=len(disk.partitions),
    )
    detection = detect(boot, root, layout, verbose=verbose)
    partitions = []
    for part in disk.partitions:
        role = PartitionRole.UNKNOWN
        if part is boot_part:
            role = PartitionRole.BOOT
        elif part is root_part:
            role = PartitionRole.ROOT
        partitions.append(
            PartitionInfo(
                id=part.id,
                type=part.fstype,
                label=part.label,
                size_bytes=part.size_bytes,
                role=role,
                mountpoint=part.mountpoint,
                readable=part.readable,
            )
        )
    live = disk.live_system
    if live:
        warnings.append("this looks like the live boot device of this machine, not a card in a USB reader")
    return _result_from_detection(
        target=disk.device,
        media=MediaInfo(
            kind=disk.kind,
            size_bytes=disk.size_bytes,
            partition_table=disk.partition_table,
            bus=disk.bus,
        ),
        partitions=partitions,
        detection=detection,
        extra_warnings=warnings,
        live=live,
    )


def _ensure_mounted(disk: DiscoveredDisk, part: DiscoveredPartition) -> tuple[str | None, str | None]:
    if part.mountpoint and os.path.isdir(part.mountpoint):
        return part.mountpoint, None
    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None and geteuid() != 0 and sys.platform.startswith("linux"):
        return None, (
            f"{part.device} is not mounted. Re-run with sudo to mount it read-only, "
            "or pass the mountpoint of the boot partition."
        )
    try:
        if sys.platform == "darwin":
            from rpios_detect.probe_macos import mount_readonly_macos

            mp = mount_readonly_macos(part.device)
            if mp:
                _MOUNTED_BY_US.append(("darwin", part.device))
                part.mountpoint = mp
                return mp, None
            return None, f"diskutil could not mount {part.device} read-only"
        if sys.platform.startswith("linux"):
            from rpios_detect.probe_linux import mount_readonly_linux

            fstype = "vfat" if "fat" in (part.fstype or "") else part.fstype
            mp = mount_readonly_linux(part.device, fstype=fstype)
            if mp:
                _MOUNTED_BY_US.append(("linux", mp))
                part.mountpoint = mp
                return mp, None
            return None, f"could not mount {part.device} read-only (need root?)"
    except Exception as exc:  # noqa: BLE001 — surface as warning, never crash a scan
        return None, f"mount failed for {part.device}: {exc}"
    return None, None


def _cleanup_mounts() -> None:
    while _MOUNTED_BY_US:
        platform, token = _MOUNTED_BY_US.pop()
        try:
            if platform == "darwin":
                from rpios_detect.probe_macos import unmount_macos

                unmount_macos(token)
            elif platform == "linux":
                from rpios_detect.probe_linux import unmount_linux

                unmount_linux(token)
        except Exception:
            continue


def _permission_hint(message: str, mountpoint: str) -> str:
    extra = ""
    if sys.platform == "darwin" and mountpoint.startswith("/Volumes/"):
        extra = (
            " On macOS, grant Full Disk Access to Terminal (or your IDE) under "
            "System Settings → Privacy & Security → Full Disk Access, then retry."
        )
    return f"{message}.{extra}"


def _result_from_detection(
    *,
    target: str,
    media: MediaInfo,
    partitions: list[PartitionInfo],
    detection: Detection,
    extra_warnings: list[str] | None = None,
    live: bool = False,
) -> TargetResult:
    warnings = list(detection.warnings)
    if extra_warnings:
        for w in extra_warnings:
            if w not in warnings:
                warnings.append(w)
    return TargetResult(
        target=target,
        media=media,
        partitions=partitions,
        verdict=detection.verdict,
        confidence=detection.confidence,
        edition=detection.edition,
        image_date=detection.image_date,
        pi_gen_stage=detection.pi_gen_stage,
        pi_gen_commit=detection.pi_gen_commit,
        os_name=detection.os_name,
        os_version_hint=detection.os_version_hint,
        first_boot_resize_pending=detection.first_boot_resize_pending,
        likely_boards=detection.likely_boards,
        other_os_guess=detection.other_os_guess,
        evidence=list(detection.evidence),
        warnings=warnings,
        live_system=live,
        cloud_init_present=detection.cloud_init_present,
        rule_log=list(detection.rule_log),
    )


def exit_code_for(results: list[TargetResult]) -> int:
    from rpios_detect.models import EXIT_AMBIGUOUS, EXIT_NO_MEDIA, EXIT_NOT_RPIOS, EXIT_RPIOS

    if not results:
        return EXIT_NO_MEDIA
    if any(
        r.verdict == Verdict.RASPBERRY_PI_OS and r.confidence in {Confidence.HIGH, Confidence.CERTAIN}
        for r in results
    ):
        return EXIT_RPIOS
    if any(
        r.verdict in {Verdict.RASPBERRY_PI_OS, Verdict.RASPBERRY_PI_OS_LIKE, Verdict.UNKNOWN}
        for r in results
    ):
        return EXIT_AMBIGUOUS
    return EXIT_NOT_RPIOS
