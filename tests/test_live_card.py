"""Optional checks against a real Raspberry Pi OS card mounted on this Mac.

Skipped in CI and whenever /Volumes/bootfs is not the official image.
Never writes to the card.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from rpios_detect.cli import run
from rpios_detect.models import EXIT_RPIOS
from rpios_detect.safety import run_readonly

BOOTFS = Path("/Volumes/bootfs")
ISSUE = BOOTFS / "issue.txt"


def _card_present() -> bool:
    if not ISSUE.is_file():
        return False
    text = ISSUE.read_text(errors="replace")
    return (
        "Raspberry Pi reference" in text
        and "Generated using pi-gen" in text
        and "https://github.com/RPi-Distro/pi-gen" in text
    )


pytestmark = pytest.mark.skipif(
    not _card_present(), reason="official Raspberry Pi OS bootfs not mounted"
)


def _whole_disk() -> str | None:
    proc = run_readonly(["diskutil", "info", str(BOOTFS)])
    if proc.returncode != 0:
        return None
    m = re.search(r"Part of Whole:\s+(\S+)", proc.stdout or "")
    if not m:
        return None
    return f"/dev/{m.group(1)}"


def test_live_bootfs_directory_is_certain(capsys) -> None:
    code = run(["--json", str(BOOTFS)])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_RPIOS
    result = payload["results"][0]
    assert result["verdict"] == "raspberry_pi_os"
    assert result["confidence"] == "certain"
    assert result["edition"] == "full"
    assert result["image_date"] == "2026-06-18"
    assert result["pi_gen_stage"] == 5
    assert any("trailing free space is unknown" in w for w in result["warnings"])


def test_live_whole_disk_reports_resize_pending(capsys) -> None:
    device = _whole_disk()
    if not device:
        pytest.skip("could not map bootfs to a whole disk")
    code = run(["--json", device])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_RPIOS
    result = payload["results"][0]
    assert result["verdict"] == "raspberry_pi_os"
    assert result["confidence"] == "certain"
    assert result["first_boot_resize_pending"] is True
    assert "pi5" in result["likely_boards"]
    assert {p["role"] for p in result["partitions"]} >= {"boot", "root"}
