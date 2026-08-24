from rpios_detect.eject import eject_removable
from rpios_detect.models import Bus, MediaKind, PartitionTable
from rpios_detect.probe import DiscoveredDisk, DiscoveredPartition


def _disk(*, internal: bool = False, live: bool = False, size: int = 8_000_000_000) -> DiscoveredDisk:
    return DiscoveredDisk(
        device="/dev/sdb",
        size_bytes=size,
        partition_table=PartitionTable.MBR,
        bus=Bus.USB,
        internal=internal,
        removable=not internal,
        kind=MediaKind.REMOVABLE_DISK,
        partitions=[
            DiscoveredPartition(
                id="sdb1",
                device="/dev/sdb1",
                fstype="vfat",
                label="bootfs",
                size_bytes=512_000_000,
                mountpoint="/mnt/boot",
            )
        ],
        live_system=live,
    )


def test_eject_refuses_internal() -> None:
    disk = _disk(internal=True)
    result = eject_removable(disk, discover=lambda **_: [disk])
    assert result.ok is False
    assert result.skipped is True


def test_eject_refuses_live_system() -> None:
    disk = _disk(live=True)
    result = eject_removable(disk, discover=lambda **_: [disk])
    assert result.ok is False
    assert result.skipped is True


def test_eject_already_gone_is_ok() -> None:
    disk = _disk()
    result = eject_removable(disk, discover=lambda **_: [])
    assert result.ok is True
    assert "gone" in result.reason


def test_eject_refuses_identity_change() -> None:
    original = _disk(size=8_000_000_000)
    swapped = _disk(size=64_000_000_000)
    result = eject_removable(original, discover=lambda **_: [swapped])
    assert result.ok is False
    assert result.skipped is True


def test_macos_eject_uses_diskutil(monkeypatch) -> None:
    import subprocess

    from rpios_detect import eject as eject_mod

    calls: list[list[str]] = []

    def fake_run(argv: list[str], timeout: float = 30.0, **_kwargs: object):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(eject_mod, "run_readonly", fake_run)
    monkeypatch.setattr(eject_mod.sys, "platform", "darwin")
    disk = _disk()
    result = eject_removable(disk, discover=lambda **_: [disk])
    assert result.ok is True
    assert ["diskutil", "unmountDisk", "/dev/sdb"] in calls
    assert ["diskutil", "eject", "/dev/sdb"] in calls


def test_linux_eject_uses_udisksctl(monkeypatch) -> None:
    import subprocess

    from rpios_detect import eject as eject_mod

    calls: list[list[str]] = []

    def fake_run(argv: list[str], timeout: float = 30.0, **_kwargs: object):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(eject_mod, "run_readonly", fake_run)
    monkeypatch.setattr(eject_mod.sys, "platform", "linux")
    disk = _disk()
    result = eject_removable(disk, discover=lambda **_: [disk])
    assert result.ok is True
    assert ["udisksctl", "unmount", "-b", "/dev/sdb1"] in calls
    assert ["udisksctl", "power-off", "-b", "/dev/sdb"] in calls


def test_windows_eject_uses_powershell(monkeypatch) -> None:
    import subprocess

    from rpios_detect import eject as eject_mod
    from rpios_detect.probe import DiscoveredPartition

    calls: list[list[str]] = []

    def fake_run(argv: list[str], timeout: float = 30.0, **_kwargs: object):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(eject_mod, "run_readonly", fake_run)
    monkeypatch.setattr(eject_mod.sys, "platform", "win32")
    disk = _disk()
    disk.partitions = [
        DiscoveredPartition(
            id="E",
            device="E:",
            fstype="fat32",
            label="bootfs",
            size_bytes=512_000_000,
            mountpoint="E:\\",
        )
    ]
    result = eject_removable(disk, discover=lambda **_: [disk])
    assert result.ok is True
    assert calls
    assert calls[0][0] in {"powershell.exe", "pwsh"}
    assert "Eject" in calls[0][-1]
