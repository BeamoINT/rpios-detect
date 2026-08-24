"""Read FAT12/16/32 filesystems from a binary view. Read-only."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from rpios_detect.fs import MAX_TEXT_BYTES, FileView


def _u16(b: bytes, off: int) -> int:
    return struct.unpack_from("<H", b, off)[0]


def _u32(b: bytes, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]


def looks_like_fat(boot: bytes) -> bool:
    if len(boot) < 512 or boot[510:512] != b"\x55\xaa":
        return False
    if boot[0] not in (0xEB, 0xE9):
        return False
    oem = boot[0x36:0x3E]
    oem32 = boot[0x52:0x5A]
    return b"FAT" in oem or b"FAT" in oem32 or boot[0x0B:0x0D] == b"\x00\x02"


@dataclass
class _FatFS:
    data: bytes
    bytes_per_sector: int
    sectors_per_cluster: int
    reserved_sectors: int
    fats: int
    root_entries: int
    total_sectors: int
    fat_size: int
    root_cluster: int
    fat_type: int  # 12, 16, 32

    @property
    def fat_offset(self) -> int:
        return self.reserved_sectors * self.bytes_per_sector

    @property
    def root_offset(self) -> int:
        return self.fat_offset + self.fats * self.fat_size * self.bytes_per_sector

    @property
    def root_sectors(self) -> int:
        if self.fat_type == 32:
            return 0
        return (self.root_entries * 32 + self.bytes_per_sector - 1) // self.bytes_per_sector

    @property
    def data_offset(self) -> int:
        return self.root_offset + self.root_sectors * self.bytes_per_sector

    @property
    def cluster_size(self) -> int:
        return self.sectors_per_cluster * self.bytes_per_sector

    def fat_entry(self, cluster: int) -> int:
        fat = self.data[self.fat_offset : self.fat_offset + self.fat_size * self.bytes_per_sector]
        if self.fat_type == 32:
            off = cluster * 4
            return _u32(fat, off) & 0x0FFFFFFF
        if self.fat_type == 16:
            return _u16(fat, cluster * 2)
        # FAT12
        off = cluster + cluster // 2
        val = _u16(fat, off)
        return (val >> 4) if cluster & 1 else (val & 0x0FFF)

    def is_eof(self, cluster: int) -> bool:
        if cluster < 2:
            return True
        if self.fat_type == 12:
            return cluster >= 0xFF8
        if self.fat_type == 16:
            return cluster >= 0xFFF8
        return cluster >= 0x0FFFFFF8

    def cluster_bytes(self, cluster: int) -> bytes:
        off = self.data_offset + (cluster - 2) * self.cluster_size
        return self.data[off : off + self.cluster_size]

    def chain(self, start: int, max_bytes: int = 8 * 1024 * 1024) -> bytes:
        out = bytearray()
        cluster = start
        seen: set[int] = set()
        while not self.is_eof(cluster) and cluster >= 2 and cluster not in seen:
            seen.add(cluster)
            out.extend(self.cluster_bytes(cluster))
            if len(out) >= max_bytes:
                return bytes(out[:max_bytes])
            cluster = self.fat_entry(cluster)
        return bytes(out)


def parse_fat(boot: bytes, blob: bytes) -> _FatFS | None:
    if not looks_like_fat(boot):
        return None
    bps = _u16(boot, 11)
    spc = boot[13]
    reserved = _u16(boot, 14)
    fats = boot[16]
    root_entries = _u16(boot, 17)
    total16 = _u16(boot, 19)
    fat16 = _u16(boot, 22)
    total32 = _u32(boot, 32)
    fat32 = _u32(boot, 36)
    root_cluster = _u32(boot, 44) if fat16 == 0 and root_entries == 0 else 2
    total = total16 or total32
    fat_size = fat16 or fat32
    if bps == 0 or spc == 0 or fats == 0 or fat_size == 0:
        return None
    root_sectors = (root_entries * 32 + bps - 1) // bps
    data_sectors = total - reserved - fats * fat_size - root_sectors
    clusters = data_sectors // spc if spc else 0
    if boot[0x52:0x5A].startswith(b"FAT32") or (fat16 == 0 and root_entries == 0):
        fat_type = 32
    elif clusters < 4085:
        fat_type = 12
    else:
        fat_type = 16
    return _FatFS(
        data=blob,
        bytes_per_sector=bps,
        sectors_per_cluster=spc,
        reserved_sectors=reserved,
        fats=fats,
        root_entries=root_entries,
        total_sectors=total,
        fat_size=fat_size,
        root_cluster=root_cluster,
        fat_type=fat_type,
    )


def _parse_lfn(entry: bytes) -> str:
    chunks = []
    for start, end in ((1, 11), (14, 26), (28, 32)):
        chunks.append(entry[start:end])
    raw = b"".join(chunks)
    return raw.decode("utf-16le", errors="ignore").split("\x00")[0]


def _parse_sfn(entry: bytes) -> str:
    name = entry[0:8].decode("ascii", errors="ignore").rstrip(" ")
    ext = entry[8:11].decode("ascii", errors="ignore").rstrip(" ")
    if entry[0] == 0x05:
        name = "\xe5" + name[1:]
    if ext:
        return f"{name}.{ext}"
    return name


def _dir_entries(fs: _FatFS, blob: bytes) -> list[tuple[str, bool, int, int]]:
    """Return (name, is_dir, cluster, size)."""
    out: list[tuple[str, bool, int, int]] = []
    lfn_parts: list[str] = []
    for i in range(0, len(blob) - 31, 32):
        ent = blob[i : i + 32]
        if ent[0] == 0x00:
            break
        if ent[0] == 0xE5:
            lfn_parts.clear()
            continue
        attr = ent[11]
        if attr == 0x0F:
            lfn_parts.insert(0, _parse_lfn(ent))
            continue
        if attr & 0x08:
            lfn_parts.clear()
            continue
        name = "".join(lfn_parts) or _parse_sfn(ent)
        lfn_parts.clear()
        if name in {".", ".."}:
            continue
        cluster = _u16(ent, 26) | (_u16(ent, 20) << 16)
        size = _u32(ent, 28)
        out.append((name, bool(attr & 0x10), cluster, size))
    return out


class FatView:
    """FileView over a FAT volume image (partition contents)."""

    def __init__(self, blob: bytes) -> None:
        fs = parse_fat(blob[:512], blob)
        if fs is None:
            raise ValueError("not a FAT volume")
        self._fs = fs
        self._index = self._index_tree()

    def _index_tree(self) -> dict[str, tuple[bool, int, int]]:
        """path -> (is_dir, cluster, size). Root is ''."""
        index: dict[str, tuple[bool, int, int]] = {"": (True, self._fs.root_cluster, 0)}

        def walk(prefix: str, raw: bytes, depth: int) -> None:
            if depth > 4:
                return
            for name, is_dir, cluster, size in _dir_entries(self._fs, raw):
                rel = f"{prefix}/{name}" if prefix else name
                key = rel.replace("\\", "/").lower()
                index[key] = (is_dir, cluster, size)
                if is_dir and cluster >= 2:
                    walk(rel, self._fs.chain(cluster), depth + 1)

        if self._fs.fat_type == 32:
            raw = self._fs.chain(self._fs.root_cluster)
        else:
            start = self._fs.root_offset
            end = start + self._fs.root_sectors * self._fs.bytes_per_sector
            raw = self._fs.data[start:end]
        walk("", raw, 0)
        return index

    def exists(self, relpath: str = "") -> bool:
        return relpath.replace("\\", "/").strip("/").lower() in self._index or relpath in ("", ".")

    def is_dir(self, relpath: str = "") -> bool:
        key = relpath.replace("\\", "/").strip("/").lower()
        if key in ("", "."):
            return True
        info = self._index.get(key)
        return bool(info and info[0])

    def listdir(self, relpath: str = "") -> list[str]:
        prefix = relpath.replace("\\", "/").strip("/").lower()
        names: set[str] = set()
        for path in self._index:
            if not path or path == prefix:
                continue
            if prefix:
                if not path.startswith(prefix + "/"):
                    continue
                rest = path[len(prefix) + 1 :]
            else:
                rest = path
            if "/" not in rest:
                names.add(rest)
        return sorted(names)

    def read_bytes(self, relpath: str, max_bytes: int = MAX_TEXT_BYTES) -> bytes | None:
        key = relpath.replace("\\", "/").strip("/").lower()
        info = self._index.get(key)
        if not info or info[0]:
            return None
        _, cluster, size = info
        if cluster < 2:
            return b""
        data = self._fs.chain(cluster, max_bytes=max(size, max_bytes))[:size]
        return data[:max_bytes]

    def read_text(self, relpath: str, max_bytes: int = MAX_TEXT_BYTES) -> str | None:
        data = self.read_bytes(relpath, max_bytes=max_bytes)
        if data is None:
            return None
        return data.decode("utf-8", errors="replace")
