from pathlib import Path

from rpios_detect.models import PartitionTable
from rpios_detect.probe_macos import is_macos_candidate, parse_diskutil_list, parse_size_to_bytes

FIXTURE = Path(__file__).parent / "fixtures" / "diskutil_list.txt"


def test_parse_real_diskutil_list() -> None:
    disks = parse_diskutil_list(FIXTURE.read_text())
    by_id = {d.device: d for d in disks}
    assert "/dev/disk0" in by_id
    assert "/dev/disk4" in by_id
    disk0 = by_id["/dev/disk0"]
    disk4 = by_id["/dev/disk4"]
    assert disk0.internal is True
    assert disk4.internal is False
    assert disk4.removable is True
    assert disk4.partition_table == PartitionTable.MBR
    labels = {p.label for p in disk4.partitions}
    assert "bootfs" in labels
    fstypes = {p.fstype for p in disk4.partitions}
    assert "fat32" in fstypes
    assert "linux" in fstypes
    assert disk4.trailing_free_bytes is not None
    assert disk4.trailing_free_bytes >= 50_000_000_000
    assert is_macos_candidate(disk4) is True
    assert is_macos_candidate(disk0) is False


def test_parse_size() -> None:
    assert parse_size_to_bytes("62.5", "GB") == 62_500_000_000
    assert parse_size_to_bytes("536.9", "MB") == 536_900_000
