"""Linux disk discovery via lsblk. Read-only."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from rpios_detect.models import Bus, MediaKind, PartitionTable
from rpios_detect.probe import DiscoveredDisk, DiscoveredPartition
from rpios_detect.safety import SafetyError, run_readonly


def discover_linux(*, all_disks: bool = False) -> list[DiscoveredDisk]:
    payload = _lsblk_json()
    root_src = _root_source()
    devices: list[DiscoveredDisk] = []
    for dev in payload.get("blockdevices") or []:
        if dev.get("type") not in {"disk", "mmcblk"} and not _is_disk(dev):
            continue
        path = dev.get("path") or f"/dev/{dev.get('name')}"
        name = dev.get("name") or Path(path).name
        tran = (dev.get("tran") or "").lower()
        rm = bool(dev.get("rm"))
        bus = Bus.USB if tran == "usb" else Bus.MMC if tran in {"mmc", "sdio"} or name.startswith("mmcblk") else Bus.UNKNOWN
        internal = not rm and tran not in {"usb", "mmc", "sdio"} and not name.startswith("mmcblk")
        if name.startswith(("loop", "zram", "ram", "sr", "fd")):
            continue
        if not all_disks and internal and not name.startswith("mmcblk"):
            continue
        children = dev.get("children") or []
        parts: list[DiscoveredPartition] = []
        used = 0
        has_gpt = (dev.get("pttype") or "").lower() == "gpt"
        has_dos = (dev.get("pttype") or "").lower() in {"dos", "msdos"}
        for child in children:
            if child.get("type") not in {"part", "crypt"}:
                continue
            cpath = child.get("path") or f"/dev/{child.get('name')}"
            size = _as_int(child.get("size"))
            used += size or 0
            fstype = (child.get("fstype") or "unknown").lower()
            parts.append(
                DiscoveredPartition(
                    id=Path(cpath).name,
                    device=cpath,
                    fstype=fstype if fstype != "vfat" else "fat32",
                    label=child.get("label") or None,
                    size_bytes=size,
                    mountpoint=child.get("mountpoint") or None,
                    readable=bool(child.get("mountpoint")),
                )
            )
        disk_size = _as_int(dev.get("size"))
        trailing = (disk_size - used) if disk_size and used and disk_size > used else None
        live = bool(root_src and (root_src == path or root_src.startswith(path)))
        devices.append(
            DiscoveredDisk(
                device=path,
                size_bytes=disk_size,
                partition_table=PartitionTable.GPT if has_gpt else PartitionTable.MBR if has_dos else PartitionTable.UNKNOWN,
                bus=bus,
                internal=internal,
                removable=rm or bus in {Bus.USB, Bus.MMC},
                kind=MediaKind.REMOVABLE_DISK,
                partitions=parts,
                trailing_free_bytes=trailing,
                live_system=live,
                already_mounted={p.device for p in parts if p.mountpoint},
            )
        )
    return devices


def _is_disk(dev: dict) -> bool:
    name = str(dev.get("name") or "")
    return bool(dev.get("children")) or name.startswith(("sd", "hd", "vd", "nvme", "mmcblk"))


def _as_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _lsblk_json() -> dict:
    try:
        proc = run_readonly(
            [
                "lsblk",
                "-J",
                "-b",
                "-o",
                "NAME,PATH,SIZE,TYPE,FSTYPE,LABEL,MOUNTPOINT,RM,TRAN,PKNAME,MODEL,PTTYPE",
            ]
        )
    except (SafetyError, FileNotFoundError, OSError):
        return {}
    if proc.returncode != 0 or not proc.stdout.strip():
        return {}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def _root_source() -> str | None:
    try:
        proc = run_readonly(["findmnt", "-n", "-o", "SOURCE", "/"])
    except (SafetyError, FileNotFoundError, OSError):
        return None
    src = (proc.stdout or "").strip()
    return src or None


def mount_readonly_linux(device: str, fstype: str | None = None) -> str | None:
    dest = f"/tmp/rpios-detect-{Path(device).name}"
    os.makedirs(dest, exist_ok=True)
    argv = ["mount", "-o", "ro,nosuid,nodev,noexec"]
    if fstype:
        argv.extend(["-t", fstype])
    argv.extend([device, dest])
    proc = run_readonly(argv)
    if proc.returncode != 0:
        try:
            os.rmdir(dest)
        except OSError:
            pass
        return None
    return dest


def unmount_linux(mountpoint: str) -> None:
    run_readonly(["umount", mountpoint])
    try:
        os.rmdir(mountpoint)
    except OSError:
        pass


def linux_available() -> bool:
    return sys.platform.startswith("linux")


def running_on_raspberry_pi() -> bool:
    model = Path("/proc/device-tree/model")
    try:
        text = model.read_bytes().decode("utf-8", errors="ignore")
    except OSError:
        return False
    return "raspberry pi" in text.lower()
