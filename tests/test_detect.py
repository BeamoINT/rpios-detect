from rpios_detect.detect import detect
from rpios_detect.evidence import edition_from_stage, parse_issue_metadata
from rpios_detect.models import Confidence, Edition, Verdict
from rpios_detect.snapshot import RootSnapshot

from helpers import (
    ISSUE_STAGE2,
    ISSUE_STAGE4,
    ISSUE_STAGE5,
    META_RPIOS,
    boot_of,
    classic_layout,
    detect_files,
    files,
)


def test_stage5_issue_txt_is_certain_full() -> None:
    d = detect_files(
        files({"issue.txt": ISSUE_STAGE5, "meta-data": META_RPIOS}),
        layout=classic_layout(),
    )
    assert d.verdict == Verdict.RASPBERRY_PI_OS
    assert d.confidence == Confidence.CERTAIN
    assert d.edition == Edition.FULL
    assert d.image_date == "2026-06-18"
    assert d.pi_gen_stage == 5
    assert d.pi_gen_commit and d.pi_gen_commit.startswith("ca8aeed0")
    assert d.os_name == "Raspberry Pi OS"
    assert d.first_boot_resize_pending is True
    assert "pi5" in d.likely_boards
    assert "pi4" in d.likely_boards


def test_stage2_is_lite() -> None:
    d = detect_files(files({"issue.txt": ISSUE_STAGE2}))
    assert d.verdict == Verdict.RASPBERRY_PI_OS
    assert d.edition == Edition.LITE
    assert d.pi_gen_stage == 2


def test_stage4_is_desktop() -> None:
    d = detect_files(files({"issue.txt": ISSUE_STAGE4}))
    assert d.verdict == Verdict.RASPBERRY_PI_OS
    assert d.edition == Edition.DESKTOP
    assert d.pi_gen_stage == 4


def test_firmware_only_is_not_raspberry_pi_os() -> None:
    d = detect_files(files())
    assert d.verdict != Verdict.RASPBERRY_PI_OS
    assert d.verdict in {Verdict.OTHER_PI_OS, Verdict.UNKNOWN, Verdict.RASPBERRY_PI_OS_LIKE}


def test_dietpi_is_other_pi_os() -> None:
    d = detect_files(files({"dietpi.txt": "AUTO_SETUP_LOCALE=C.UTF-8\n"}))
    assert d.verdict == Verdict.OTHER_PI_OS
    assert d.other_os_guess == "DietPi"


def test_ubuntu_boot_is_other_pi_os() -> None:
    d = detect_files(files({"vmlinuz": b"", "ubuntu.env": "SNAP_NAME=pi-gadget\n"}))
    assert d.verdict == Verdict.OTHER_PI_OS
    assert d.other_os_guess and "Ubuntu" in d.other_os_guess


def test_libreelec_is_other_pi_os() -> None:
    d = detect_files(files({"SYSTEM": b"", "KERNEL": b""}))
    assert d.verdict == Verdict.OTHER_PI_OS
    assert d.other_os_guess == "LibreELEC"


def test_empty_fat_is_not_pi() -> None:
    d = detect_files(files({"readme.txt": "camera photos\n"}, firmware=False), label="NO NAME")
    assert d.verdict in {Verdict.NOT_PI, Verdict.UNKNOWN}
    assert d.verdict != Verdict.RASPBERRY_PI_OS


def test_resize_pending_needs_cmdline_and_trailing_space() -> None:
    with_space = detect_files(files({"issue.txt": ISSUE_STAGE5}), layout=classic_layout())
    no_space = detect_files(
        files({"issue.txt": ISSUE_STAGE5}),
        layout=classic_layout(trailing=0),
    )
    assert with_space.first_boot_resize_pending is True
    assert no_space.first_boot_resize_pending is False


def test_firmware_must_not_be_enough_even_with_classic_layout() -> None:
    d = detect_files(files(), layout=classic_layout())
    assert d.verdict != Verdict.RASPBERRY_PI_OS


def test_root_rpi_issue_and_raspi_repo_is_certain() -> None:
    boot = boot_of(files())
    root = RootSnapshot(
        readable=True,
        files=frozenset(
            {"etc/rpi-issue", "etc/apt/sources.list.d/raspi.list", "usr/bin/raspi-config"}
        ),
        texts={
            "etc/rpi-issue": ISSUE_STAGE5,
            "etc/apt/sources.list.d/raspi.list": (
                "deb http://archive.raspberrypi.com/debian/ trixie main\n"
            ),
        },
        label="rootfs",
        has_raspi_config=True,
    )
    d = detect(boot, root, classic_layout())
    assert d.verdict == Verdict.RASPBERRY_PI_OS
    assert d.confidence == Confidence.CERTAIN


def test_parse_issue_metadata() -> None:
    date, stage, commit = parse_issue_metadata(ISSUE_STAGE5)
    assert date == "2026-06-18"
    assert stage == 5
    assert commit and commit.startswith("ca8aeed0")
    assert edition_from_stage(2) == "lite"
    assert edition_from_stage(4) == "desktop"
    assert edition_from_stage(5) == "full"
    assert edition_from_stage(3) is None
