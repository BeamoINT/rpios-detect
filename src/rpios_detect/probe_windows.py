"""Windows discovery (best-effort). FAT boot volumes via drive letters."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from rpios_detect.models import Bus, MediaKind, PartitionTable
from rpios_detect.probe import DiscoveredDisk, DiscoveredPartition
from rpios_detect.safety import SafetyError, run_readonly

_PS = r"""
$ErrorActionPreference = 'Stop'
$disks = Get-CimInstance Win32_DiskDrive | ForEach-Object {
  $d = $_
  $parts = Get-CimInstance -Query ("ASSOCIATORS OF {Win32_DiskDrive.DeviceID='$($d.DeviceID)'} WHERE AssocClass=Win32_DiskDriveToDiskPartition")
  $partitions = @()
  foreach ($p in $parts) {
    $lds = Get-CimInstance -Query ("ASSOCIATORS OF {Win32_DiskPartition.DeviceID='$($p.DeviceID)'} WHERE AssocClass=Win32_LogicalDiskToPartition")
    foreach ($ld in $lds) {
      $partitions += [pscustomobject]@{
        id = $ld.DeviceID
        device = $ld.DeviceID
        fstype = $ld.FileSystem
        label = $ld.VolumeName
        size = [int64]$ld.Size
        mountpoint = ($ld.DeviceID + '\')
      }
    }
  }
  [pscustomobject]@{
    device = $d.DeviceID
    size = [int64]$d.Size
    bus = $d.InterfaceType
    removable = ($d.MediaType -match 'Removable')
    model = $d.Model
    partitions = $partitions
  }
}
$disks | ConvertTo-Json -Depth 6 -Compress
"""


def discover_windows(*, all_disks: bool = False) -> list[DiscoveredDisk]:
    raw = _powershell_json()
    if not raw:
        return _drive_letter_fallback()
    items = raw if isinstance(raw, list) else [raw]
    disks: list[DiscoveredDisk] = []
    for item in items:
        bus_s = str(item.get("bus") or "").lower()
        bus = Bus.USB if "usb" in bus_s else Bus.MMC if "mmc" in bus_s or "sd" in bus_s else Bus.UNKNOWN
        removable = bool(item.get("removable")) or bus == Bus.USB
        if not all_disks and not removable:
            continue
        parts = []
        for p in item.get("partitions") or []:
            letter = str(p.get("mountpoint") or p.get("device") or "")
            parts.append(
                DiscoveredPartition(
                    id=str(p.get("id") or letter),
                    device=letter,
                    fstype=str(p.get("fstype") or "unknown").lower(),
                    label=p.get("label") or None,
                    size_bytes=_as_int(p.get("size")),
                    mountpoint=letter if letter else None,
                    readable=bool(letter) and Path(letter).exists(),
                )
            )
        disks.append(
            DiscoveredDisk(
                device=str(item.get("device") or ""),
                size_bytes=_as_int(item.get("size")),
                partition_table=PartitionTable.UNKNOWN,
                bus=bus,
                internal=not removable,
                removable=removable,
                kind=MediaKind.REMOVABLE_DISK,
                partitions=parts,
                already_mounted={p.device for p in parts if p.mountpoint},
            )
        )
    return disks


def inspect_drive_letter(letter: str) -> DiscoveredDisk:
    letter = letter.rstrip("\\/")
    if len(letter) == 2 and letter[1] == ":":
        root = letter + "\\"
    else:
        root = letter
        if not root.endswith(("\\", "/")):
            root += "\\"
    path = Path(root)
    return DiscoveredDisk(
        device=letter,
        size_bytes=None,
        partition_table=PartitionTable.UNKNOWN,
        bus=Bus.UNKNOWN,
        internal=False,
        removable=True,
        kind=MediaKind.MOUNTED_VOLUME,
        partitions=[
            DiscoveredPartition(
                id=letter,
                device=letter,
                fstype="fat32",
                label=None,
                size_bytes=None,
                mountpoint=str(path),
                readable=path.exists(),
            )
        ],
        already_mounted={letter},
    )


def _drive_letter_fallback() -> list[DiscoveredDisk]:
    disks: list[DiscoveredDisk] = []
    for code in range(ord("D"), ord("Z") + 1):
        letter = f"{chr(code)}:"
        root = Path(letter + "\\")
        if not root.exists():
            continue
        disks.append(inspect_drive_letter(letter))
    return disks


def _as_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _powershell_json() -> object:
    exe = "powershell.exe" if sys.platform == "win32" else "pwsh"
    try:
        proc = run_readonly([exe, "-NoProfile", "-Command", _PS], timeout=45)
    except (SafetyError, FileNotFoundError, OSError):
        return None
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def windows_available() -> bool:
    return sys.platform == "win32"
