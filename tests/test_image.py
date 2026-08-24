from pathlib import Path

from rpios_detect.detect import detect
from rpios_detect.image import inspect_image
from rpios_detect.models import Verdict
from rpios_detect.snapshot import collect_boot

from fatimg import build_fat16, wrap_mbr
from helpers import ISSUE_STAGE5, META_RPIOS


def test_img_with_issue_txt(tmp_path: Path) -> None:
    volume = build_fat16(
        {
            "ISSUE.TXT": ISSUE_STAGE5.encode(),
            "CONFIG.TXT": b"arm_64bit=1\n",
            "CMDLINE.TXT": b"root=PARTUUID=abcd-02 rootfstype=ext4 resize\n",
            "META-DAT": META_RPIOS.encode(),  # 8.3 truncates; issue.txt is the smoking gun
            "BOOTCODE.BIN": b"x",
            "START.ELF": b"x",
            "KERNEL8.IMG": b"x",
        }
    )
    img = wrap_mbr(volume, linux_bytes=1024 * 1024, trailing_bytes=2_000_000_000)
    path = tmp_path / "card.img"
    path.write_bytes(img)
    layout, parts, fat_view = inspect_image(path)
    assert any("fat" in p.type_name for p in parts)
    assert any(p.type_name == "linux" for p in parts)
    assert fat_view is not None
    boot = collect_boot(fat_view, label="bootfs")
    d = detect(boot, None, layout)
    assert d.verdict == Verdict.RASPBERRY_PI_OS
    assert d.confidence.value in {"certain", "high"}
    assert d.image_date == "2026-06-18"
    assert d.pi_gen_stage == 5
