import pytest

from rpios_detect.safety import SafetyError, run_readonly


def test_refuses_destructive_diskutil() -> None:
    with pytest.raises(SafetyError):
        run_readonly(["diskutil", "eraseDisk", "FAT32", "X", "/dev/disk4"])
    with pytest.raises(SafetyError):
        run_readonly(["diskutil", "partitionDisk", "/dev/disk4", "1", "MBR"])


def test_refuses_dd_and_mkfs() -> None:
    with pytest.raises(SafetyError):
        run_readonly(["dd", "if=/dev/zero", "of=/dev/disk4"])
    with pytest.raises(SafetyError):
        run_readonly(["mkfs.vfat", "/dev/disk4s1"])


def test_refuses_readwrite_mount() -> None:
    with pytest.raises(SafetyError):
        run_readonly(["mount", "/dev/disk4s1", "/mnt"])
    with pytest.raises(SafetyError):
        run_readonly(["diskutil", "mount", "/dev/disk4s1"])


def test_allows_diskutil_list() -> None:
    # May fail if diskutil is missing; the guard must still accept the argv.
    try:
        proc = run_readonly(["diskutil", "list"])
    except FileNotFoundError:
        pytest.skip("diskutil not present")
    assert proc.returncode == 0
    assert "/dev/disk" in (proc.stdout or "")
