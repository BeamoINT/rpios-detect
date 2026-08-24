"""Shared test helpers."""

from __future__ import annotations

from rpios_detect.detect import Detection, detect
from rpios_detect.fs import DictView
from rpios_detect.snapshot import BootSnapshot, PartitionLayout, collect_boot

ISSUE_STAGE5 = (
    "Raspberry Pi reference 2026-06-18\n"
    "Generated using pi-gen, https://github.com/RPi-Distro/pi-gen, "
    "ca8aeed0ae300c2a89f55ce9617d5f96a27e99e5, stage5\n"
)
ISSUE_STAGE2 = (
    "Raspberry Pi reference 2026-03-01\n"
    "Generated using pi-gen, https://github.com/RPi-Distro/pi-gen, "
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa, stage2\n"
)
ISSUE_STAGE4 = (
    "Raspberry Pi reference 2025-11-20\n"
    "Generated using pi-gen, https://github.com/RPi-Distro/pi-gen, "
    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb, stage4\n"
)
CMDLINE_RESIZE = (
    "console=serial0,115200 console=tty1 root=PARTUUID=e5fdffb4-02 "
    "rootfstype=ext4 fsck.repair=yes rootwait resize quiet splash "
    "plymouth.ignore-serial-consoles\n"
)
CONFIG = """# For more options and information see
# http://rptl.io/configtxt
arm_64bit=1
dtoverlay=vc4-kms-v3d
auto_initramfs=1
"""
META_RPIOS = """dsmode: local
instance_id: rpios-image
"""

FIRMWARE: dict[str, bytes | str] = {
    "bootcode.bin": b"",
    "start.elf": b"",
    "start4.elf": b"",
    "fixup.dat": b"",
    "kernel8.img": b"",
    "kernel_2712.img": b"",
    "bcm2711-rpi-4-b.dtb": b"",
    "bcm2712-rpi-5-b.dtb": b"",
    "overlays/.keep": b"",
    "LICENCE.broadcom": b"",
    "config.txt": CONFIG,
    "cmdline.txt": CMDLINE_RESIZE,
}


def files(*extra: dict[str, bytes | str], firmware: bool = True) -> dict[str, bytes | str]:
    out: dict[str, bytes | str] = dict(FIRMWARE) if firmware else {}
    for block in extra:
        out.update(block)
    return out


def boot_of(filemap: dict[str, bytes | str], label: str | None = "bootfs") -> BootSnapshot:
    return collect_boot(DictView(filemap), label=label)


def detect_files(
    filemap: dict[str, bytes | str],
    *,
    label: str | None = "bootfs",
    layout: PartitionLayout | None = None,
    verbose: bool = True,
) -> Detection:
    return detect(boot_of(filemap, label=label), None, layout, verbose=verbose)


def classic_layout(*, trailing: int = 52_800_000_000) -> PartitionLayout:
    return PartitionLayout(
        table="mbr",
        size_bytes=62_500_000_000,
        trailing_free_bytes=trailing,
        first_partition_start_bytes=4 * 1024 * 1024,
        has_fat=True,
        has_linux=True,
        fat_label="bootfs",
        linux_label="rootfs",
        partition_count=2,
    )
