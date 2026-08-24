"""Read-only raw disk image (.img / .iso) partition parsing."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from rpios_detect.fat import FatView, looks_like_fat
from rpios_detect.fs import FileView
from rpios_detect.models import PartitionRole, PartitionTable
from rpios_detect.snapshot import PartitionLayout


@dataclass(frozen=True)
class ImagePartition:
    index: int
    start_lba: int
    sector_count: int
    type_byte: int
    type_name: str
    name: str | None = None


def _u32(b: bytes, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]


def parse_mbr(sector: bytes) -> list[ImagePartition] | None:
    if len(sector) < 512 or sector[510:512] != b"\x55\xaa":
        return None
    parts: list[ImagePartition] = []
    for i in range(4):
        off = 446 + i * 16
        ptype = sector[off + 4]
        start = _u32(sector, off + 8)
        count = _u32(sector, off + 12)
        if ptype == 0 or count == 0:
            continue
        parts.append(
            ImagePartition(
                index=i + 1,
                start_lba=start,
                sector_count=count,
                type_byte=ptype,
                type_name=_mbr_type(ptype),
            )
        )
    return parts


def parse_gpt(blob: bytes, sector_size: int = 512) -> list[ImagePartition] | None:
    header = blob[sector_size : sector_size * 2]
    if header[0:8] != b"EFI PART":
        return None
    part_lba = struct.unpack_from("<Q", header, 72)[0]
    num = struct.unpack_from("<I", header, 80)[0]
    entsize = struct.unpack_from("<I", header, 84)[0]
    if entsize < 128 or num > 128:
        return None
    start = part_lba * sector_size
    parts: list[ImagePartition] = []
    for i in range(num):
        ent = blob[start + i * entsize : start + (i + 1) * entsize]
        if len(ent) < 128 or ent[0:16] == b"\x00" * 16:
            continue
        first = struct.unpack_from("<Q", ent, 32)[0]
        last = struct.unpack_from("<Q", ent, 40)[0]
        name = ent[56:128].decode("utf-16le", errors="ignore").split("\x00")[0] or None
        guid = ent[0:16]
        parts.append(
            ImagePartition(
                index=i + 1,
                start_lba=first,
                sector_count=int(last - first + 1),
                type_byte=0,
                type_name=_gpt_type(guid),
                name=name,
            )
        )
    return parts


def _mbr_type(code: int) -> str:
    mapping = {
        0x01: "fat12",
        0x04: "fat16",
        0x06: "fat16",
        0x0B: "fat32",
        0x0C: "fat32",
        0x0E: "fat16",
        0x0F: "extended",
        0x83: "linux",
        0xEE: "gpt-protective",
    }
    return mapping.get(code, f"0x{code:02x}")


def _gpt_type(guid: bytes) -> str:
    efi = bytes.fromhex("28732ac11ff8d211ba4b00a0c93ec93b")
    basic = bytes.fromhex("a2a0d0ebe5b9334487c068b6b72699c7")
    linux = bytes.fromhex("af3dc60f838447728e793d69d8477de4")
    if guid == efi or guid == basic:
        return "fat"
    if guid == linux:
        return "linux"
    return "unknown"


def inspect_image(path: Path) -> tuple[PartitionLayout, list[ImagePartition], FileView | None]:
    data = path.read_bytes()  # whole image; v0.1 images in tests are small
    size = len(data)
    table = PartitionTable.UNKNOWN
    parts = parse_mbr(data[:512]) or []
    if parts and any(p.type_byte == 0xEE for p in parts):
        gpt = parse_gpt(data)
        if gpt:
            table = PartitionTable.GPT
            parts = gpt
    elif parts:
        table = PartitionTable.MBR

    used_end = 0
    has_fat = False
    has_linux = False
    first_start = None
    fat_view: FileView | None = None
    for p in parts:
        end = (p.start_lba + p.sector_count) * 512
        used_end = max(used_end, end)
        if first_start is None:
            first_start = p.start_lba * 512
        t = p.type_name
        start = p.start_lba * 512
        blob = data[start : start + p.sector_count * 512]
        if looks_like_fat(blob[:512]) or t.startswith("fat"):
            has_fat = True
            if fat_view is None:
                try:
                    fat_view = FatView(blob)
                except ValueError:
                    fat_view = None
        if t == "linux" or t.startswith("0x83"):
            has_linux = True
    trailing = size - used_end if used_end and size > used_end else 0
    layout = PartitionLayout(
        table=table.value,
        size_bytes=size,
        trailing_free_bytes=trailing or None,
        first_partition_start_bytes=first_start,
        has_fat=has_fat,
        has_linux=has_linux,
        partition_count=len(parts),
    )
    return layout, parts, fat_view
