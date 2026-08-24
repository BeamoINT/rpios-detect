"""Build a tiny MBR + FAT16 image for tests. Read-path only; never written to a disk."""

from __future__ import annotations

import struct


def _sfn(name: str) -> bytes:
    stem, _, ext = name.upper().partition(".")
    return f"{stem[:8]:<8}{ext[:3]:<3}".encode("ascii")


def build_fat16(files: dict[str, bytes]) -> bytes:
    """Return a FAT16 volume containing `files` (8.3 names, root directory only)."""
    bytes_per_sector = 512
    sectors_per_cluster = 1
    reserved = 1
    fats = 2
    root_entries = 16
    fat_sectors = 16
    root_sectors = (root_entries * 32 + bytes_per_sector - 1) // bytes_per_sector
    data_start = reserved + fats * fat_sectors + root_sectors
    # Cluster 2+ for file contents.
    payloads = [(name, data) for name, data in files.items()]
    clusters_needed = 0
    for _, data in payloads:
        clusters_needed += max(1, (len(data) + bytes_per_sector - 1) // bytes_per_sector)
    total_sectors = data_start + max(clusters_needed, 16) + 8

    boot = bytearray(bytes_per_sector)
    boot[0:3] = b"\xeb\x3c\x90"
    boot[3:11] = b"RPIOSDT "
    struct.pack_into("<H", boot, 11, bytes_per_sector)
    boot[13] = sectors_per_cluster
    struct.pack_into("<H", boot, 14, reserved)
    boot[16] = fats
    struct.pack_into("<H", boot, 17, root_entries)
    struct.pack_into("<H", boot, 19, total_sectors)
    boot[21] = 0xF8
    struct.pack_into("<H", boot, 22, fat_sectors)
    struct.pack_into("<H", boot, 24, 1)
    struct.pack_into("<H", boot, 26, 1)
    boot[0x36:0x3E] = b"FAT16   "
    boot[510:512] = b"\x55\xaa"

    fat = bytearray(fat_sectors * bytes_per_sector)
    struct.pack_into("<H", fat, 0, 0xFFF8)
    struct.pack_into("<H", fat, 2, 0xFFFF)

    root = bytearray(root_sectors * bytes_per_sector)
    data = bytearray((total_sectors - data_start) * bytes_per_sector)

    cluster = 2
    for i, (name, payload) in enumerate(payloads):
        nclust = max(1, (len(payload) + bytes_per_sector - 1) // bytes_per_sector)
        first = cluster
        for c in range(nclust):
            nxt = 0xFFFF if c == nclust - 1 else cluster + 1
            struct.pack_into("<H", fat, cluster * 2, nxt)
            cluster += 1
        off = (first - 2) * bytes_per_sector
        data[off : off + len(payload)] = payload
        ent = bytearray(32)
        ent[0:11] = _sfn(name)
        struct.pack_into("<H", ent, 26, first)
        struct.pack_into("<I", ent, 28, len(payload))
        root[i * 32 : (i + 1) * 32] = ent

    parts = [bytes(boot)]
    parts.extend([bytes(fat)] * fats)
    parts.append(bytes(root))
    parts.append(bytes(data))
    return b"".join(parts)


def wrap_mbr(volume: bytes, *, linux_bytes: int = 9_000_000, trailing_bytes: int = 0) -> bytes:
    """Prefix `volume` with an MBR: FAT type 0x0C at LBA 1, Linux type 0x83 after it."""
    if len(volume) % 512:
        volume = volume + b"\x00" * (512 - len(volume) % 512)
    fat_sectors = len(volume) // 512
    linux_sectors = max(1, linux_bytes // 512)
    mbr = bytearray(512)
    mbr[510:512] = b"\x55\xaa"
    # partition 1: FAT, LBA 1
    mbr[446] = 0x80
    mbr[450] = 0x0C
    struct.pack_into("<I", mbr, 454, 1)
    struct.pack_into("<I", mbr, 458, fat_sectors)
    # partition 2: Linux
    mbr[466] = 0x83
    struct.pack_into("<I", mbr, 470, 1 + fat_sectors)
    struct.pack_into("<I", mbr, 474, linux_sectors)
    trailing = b"\x00" * (linux_bytes + trailing_bytes)
    return bytes(mbr) + volume + trailing
