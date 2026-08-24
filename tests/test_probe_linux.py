from rpios_detect.probe_linux import _has_system_mount, _is_root_disk


def test_root_disk_matches_partitions_not_neighbors() -> None:
    assert _is_root_disk("/dev/sda2", "/dev/sda") is True
    assert _is_root_disk("/dev/nvme0n1p3", "/dev/nvme0n1") is True
    assert _is_root_disk("/dev/mmcblk0p2", "/dev/mmcblk0") is True
    assert _is_root_disk("/dev/nvme0n10p1", "/dev/nvme0n1") is False
    assert _is_root_disk("/dev/sdb1", "/dev/sda") is False
    assert _is_root_disk("/dev/sda", "/dev/sda") is True
    assert _is_root_disk(None, "/dev/sda") is False


def test_nested_lvm_root_is_a_system_mount() -> None:
    tree = {
        "mountpoint": None,
        "children": [
            {"mountpoint": None, "children": [{"mountpoint": "/"}]},
        ],
    }
    assert _has_system_mount(tree) is True
    assert _has_system_mount({"mountpoint": "/data"}) is False
    assert _has_system_mount({"mountpoint": "/boot/firmware"}) is True
