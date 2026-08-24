"""macOS disk discovery via diskutil. Read-only."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from rpios_detect.models import Bus, MediaKind, PartitionTable
from rpios_detect.probe import DiscoveredDisk, DiscoveredPartition
from rpios_detect.safety import SafetyError, run_readonly

DISK_HEADER = re.compile(r"^/dev/(disk\d+)\s+\(([^)]+)\):\s*$")
SIZE_RE = re.compile(r"([*+]?)(\d+(?:\.\d+)?)\s+([KMGTPE]i?B)")
PART_LINE = re.compile(
    r"^\s+(?:(\d+):)?\s*(.+?)\s+([*+]?)(\d+(?:\.\d+)?)\s+([KMGTPE]i?B)\s+(\S+)\s*$"
)


def parse_size_to_bytes(number: str, unit: str) -> int:
    n = float(number)
    u = unit.upper()
    si = {"B": 1, "KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12, "PB": 10**15}
    iec = {"KIB": 2**10, "MIB": 2**20, "GIB": 2**30, "TIB": 2**40, "PIB": 2**50}
    if u in iec:
        return int(n * iec[u])
    return int(n * si.get(u, 1))


def parse_diskutil_list(text: str) -> list[DiscoveredDisk]:
    disks: list[DiscoveredDisk] = []
    current: DiscoveredDisk | None = None
    trailing = 0
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip() == "..." or "#:" in line and "IDENTIFIER" in line:
            continue
        if "Physical Store" in line:
            continue
        header = DISK_HEADER.match(line)
        if header:
            if current is not None:
                current.trailing_free_bytes = trailing or None
                disks.append(current)
            ident = header.group(1)
            attrs = [a.strip() for a in header.group(2).split(",")]
            current = DiscoveredDisk(
                device=f"/dev/{ident}",
                size_bytes=None,
                partition_table=PartitionTable.UNKNOWN,
                bus=Bus.UNKNOWN,
                internal="internal" in attrs,
                removable="external" in attrs or "removable" in attrs,
                kind=MediaKind.REMOVABLE_DISK if "external" in attrs else MediaKind.MOUNTED_VOLUME,
                live_system=False,
            )
            trailing = 0
            continue
        if current is None:
            continue
        m = PART_LINE.match(line)
        if not m:
            continue
        type_name = (m.group(2) or "").strip()
        size_bytes = parse_size_to_bytes(m.group(4), m.group(5))
        ident = m.group(6)
        if type_name.lower() == "(free space)" or ident == "-":
            trailing += size_bytes
            continue
        if current.size_bytes is None and ident == Path(current.device).name:
            current.size_bytes = size_bytes
            if "FDisk" in type_name or "MBR" in type_name:
                current.partition_table = PartitionTable.MBR
            elif "GUID" in type_name or "GPT" in type_name or "APFS Container Scheme" in type_name:
                current.partition_table = PartitionTable.GPT
            continue
        fstype, label = _split_type_name(type_name)
        part = DiscoveredPartition(
            id=ident,
            device=f"/dev/{ident}",
            fstype=fstype,
            label=label,
            size_bytes=size_bytes,
            mountpoint=None,
        )
        current.partitions.append(part)
    if current is not None:
        current.trailing_free_bytes = trailing or None
        disks.append(current)
    return disks


def _split_type_name(type_name: str) -> tuple[str, str | None]:
    tokens = type_name.split()
    if not tokens:
        return "unknown", None
    joined = type_name.lower()
    if "fat_32" in joined or "fat32" in joined:
        fstype = "fat32"
    elif "fat_16" in joined or "fat16" in joined:
        fstype = "fat16"
    elif "linux" in joined:
        fstype = "linux"
    elif "apfs" in joined:
        fstype = "apfs"
    elif "efi" in joined:
        fstype = "efi"
    else:
        fstype = tokens[0].lower()
    label = None
    skip = {"windows_fat_32", "windows_fat_16", "linux", "efi", "apple_apfs", "apple_apfs_isc", "apple_apfs_recovery"}
    rest = [t for t in tokens[1:] if t.lower() not in {"container"} and not t.lower().startswith("disk")]
    if rest and tokens[0].lower() not in skip and "scheme" not in joined:
        label = rest[-1]
    elif rest:
        label = rest[-1]
    if label and label.lower() in {"container"}:
        label = None
    return fstype, label


def is_macos_candidate(disk: DiscoveredDisk, *, include_internal: bool = False) -> bool:
    if disk.internal and not include_internal:
        return False
    if "synthesized" in disk.device:
        return False
    ident = Path(disk.device).name
    if ident.startswith("disk") and disk.internal:
        return False
    if disk.partition_table == PartitionTable.GPT and any(
        p.fstype == "apfs" for p in disk.partitions
    ) and disk.internal:
        return False
    return disk.removable or include_internal



def _macos_whole_ident(token: str) -> str:
    token = token.strip().removeprefix("/dev/")
    matched = re.match(r"(disk\d+)", token)
    return matched.group(1) if matched else token


def _macos_live_idents(info_text: str | None = None) -> set[str]:
    """Whole-disk ids that hold the live OS: APFS container *and* physical store.

    `diskutil info /` reports Part of Whole as the synthesized container
    (disk3). A USB-booted Mac's physical disk (disk4) is a different node;
    ejecting that would unplug the running system.
    """
    if info_text is None:
        try:
            proc = run_readonly(["diskutil", "info", "/"])
        except (SafetyError, FileNotFoundError, OSError):
            return set()
        if proc.returncode != 0:
            return set()
        info_text = proc.stdout or ""
    idents: set[str] = set()
    for line in info_text.splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip()
        if not val:
            continue
        token = val.split()[0]
        if key in {"Part of Whole", "APFS Container"}:
            idents.add(_macos_whole_ident(token))
        elif "Physical Store" in key:
            idents.add(_macos_whole_ident(token))
    return {i for i in idents if i}


def discover_macos(*, all_disks: bool = False) -> list[DiscoveredDisk]:
    text = _diskutil_list_text()
    disks = parse_diskutil_list(text)
    live_idents = _macos_live_idents()
    out: list[DiscoveredDisk] = []
    for disk in disks:
        ident = Path(disk.device).name
        if ident in live_idents:
            disk.live_system = True
        info = _diskutil_info(ident)
        if info:
            disk.internal = info.get("internal", disk.internal)
            disk.bus = _bus_from_info(info)
            disk.removable = bool(info.get("removable") or info.get("external") or disk.removable)
            if info.get("size"):
                disk.size_bytes = int(info["size"])
        if not all_disks and not is_macos_candidate(disk):
            continue
        if not all_disks and disk.internal:
            continue
        for part in disk.partitions:
            pinfo = _diskutil_info(part.id)
            if pinfo:
                part.mountpoint = pinfo.get("mountpoint") or None
                part.label = pinfo.get("name") or part.label
                part.fstype = pinfo.get("fstype") or part.fstype
                if pinfo.get("size"):
                    part.size_bytes = int(pinfo["size"])
                if part.mountpoint:
                    disk.already_mounted.add(part.device)
                    part.readable = os.path.isdir(part.mountpoint)
        out.append(disk)
    return out


def _bus_from_info(info: dict) -> Bus:
    proto = (info.get("bus") or "").lower()
    if "usb" in proto:
        return Bus.USB
    if "mmc" in proto or "secure" in proto or "sd" == proto:
        return Bus.MMC
    return Bus.UNKNOWN


def _diskutil_list_text() -> str:
    proc = run_readonly(["diskutil", "list"])
    return proc.stdout or ""


def _diskutil_info(ident: str) -> dict[str, object]:
    ident = ident.removeprefix("/dev/")
    try:
        proc = run_readonly(["diskutil", "info", ident])
    except (SafetyError, FileNotFoundError, OSError):
        return {}
    if proc.returncode != 0:
        return {}
    return parse_diskutil_info(proc.stdout or "")


def parse_diskutil_info(text: str) -> dict[str, object]:
    info: dict[str, object] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip()
        if key == "Device Node":
            info["device"] = val
        elif key == "Protocol":
            info["bus"] = val
        elif key == "Internal":
            info["internal"] = val.lower() == "yes"
        elif key == "Removable Media":
            info["removable"] = val.lower() in {"yes", "removable"}
        elif key in {"Disk Size", "Volume Total Space"}:
            m = re.search(r"\((\d+)\s+Bytes\)", val)
            if m:
                info["size"] = int(m.group(1))
        elif key == "Mount Point":
            info["mountpoint"] = "" if val in {"", "Not applicable (no file system)"} else val
        elif key == "Volume Name":
            info["name"] = None if val in {"", "Not applicable (no file system)"} else val
        elif key == "File System Personality":
            info["fstype"] = val.lower().replace(" ", "")
        elif key == "Partition Type":
            info["parttype"] = val
    return info


def mount_readonly_macos(device: str) -> str | None:
    ident = device.removeprefix("/dev/")
    proc = run_readonly(["diskutil", "mount", "readOnly", ident])
    if proc.returncode != 0:
        return None
    info = _diskutil_info(ident)
    mp = info.get("mountpoint")
    return str(mp) if mp else None


def unmount_macos(device: str) -> None:
    ident = device.removeprefix("/dev/")
    run_readonly(["diskutil", "unmount", ident])


def macos_available() -> bool:
    return sys.platform == "darwin"
