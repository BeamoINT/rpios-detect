"""Evidence rules: data plus small matchers. No host I/O."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rpios_detect.models import Weight
from rpios_detect.snapshot import BootSnapshot, PartitionLayout, RootSnapshot

PI_GEN_URL = "https://github.com/RPi-Distro/pi-gen"
REFERENCE_RE = re.compile(r"Raspberry Pi reference\s+(\d{4}-\d{2}-\d{2})", re.I)
PI_GEN_LINE_RE = re.compile(
    r"Generated using pi-gen,\s*(https://github.com/RPi-Distro/pi-gen)\s*,\s*([0-9a-f]{7,40})\s*,\s*stage(\d+)",
    re.I,
)
STAGE_RE = re.compile(r"\bstage(\d+)\b", re.I)
COMMIT_RE = re.compile(r"\b([0-9a-f]{40})\b")
INSTANCE_ID_RE = re.compile(r"^instance[_-]?id\s*:\s*(\S+)", re.I | re.M)


@dataclass(frozen=True)
class DetectContext:
    boot: BootSnapshot | None
    root: RootSnapshot | None
    layout: PartitionLayout | None

    def boot_or_empty(self) -> BootSnapshot:
        return self.boot or BootSnapshot(files=frozenset(), dirs=frozenset(), texts={})


Matcher = Callable[[DetectContext], str | None]


@dataclass(frozen=True)
class Rule:
    id: str
    weight: Weight
    category: str  # official_smoking, official_support, firmware, layout, negative, fact
    matcher: Matcher
    os_guess: str | None = None


def _boot(ctx: DetectContext) -> BootSnapshot | None:
    return ctx.boot


def _root(ctx: DetectContext) -> RootSnapshot | None:
    return ctx.root if ctx.root and ctx.root.readable else None


def issue_txt_pi_gen(ctx: DetectContext) -> str | None:
    boot = _boot(ctx)
    if not boot:
        return None
    for text in boot.texts_named("issue.txt"):
        has_ref = "Raspberry Pi reference" in text
        has_gen = "Generated using pi-gen" in text
        has_url = PI_GEN_URL in text
        if has_ref and has_gen and has_url:
            date_m = REFERENCE_RE.search(text)
            line_m = PI_GEN_LINE_RE.search(text)
            date = date_m.group(1) if date_m else "unknown-date"
            stage = line_m.group(3) if line_m else "?"
            commit = line_m.group(2) if line_m else ""
            extra = f", {commit[:12]}" if commit else ""
            return f"Raspberry Pi reference {date}, stage{stage}{extra}"
    return None


def issue_txt_custom_pi_gen(ctx: DetectContext) -> str | None:
    """pi-gen mention without the official Raspberry Pi reference line."""
    if issue_txt_pi_gen(ctx):
        return None
    boot = _boot(ctx)
    if not boot:
        return None
    for text in boot.texts_named("issue.txt"):
        if "pi-gen" in text.lower() or PI_GEN_URL in text:
            return "issue.txt mentions pi-gen without 'Raspberry Pi reference'"
    return None


def issue_txt_raspbian(ctx: DetectContext) -> str | None:
    boot = _boot(ctx)
    if not boot:
        return None
    for text in boot.texts_named("issue.txt"):
        if re.search(r"\bRaspbian\b", text):
            return "issue.txt names Raspbian"
    root = _root(ctx)
    if root:
        osr = root.text("etc/os-release")
        if re.search(r"\bRaspbian\b", osr) or "ID=raspbian" in osr:
            return "os-release names Raspbian"
    return None


def meta_data_rpios_image(ctx: DetectContext) -> str | None:
    boot = _boot(ctx)
    if not boot:
        return None
    for name in ("meta-data", "meta_data"):
        text = boot.text(name)
        if not text:
            continue
        m = INSTANCE_ID_RE.search(text)
        value = (m.group(1).strip().lower() if m else "")
        if value in {"rpios-image", "rpios-image"}:
            return f"meta-data instance_id: {m.group(1).strip()}"
        lowered = text.lower()
        if "rpios-image" in lowered or "rpios-image" in lowered:
            return "meta-data contains rpios-image"
    return None


def boot_label_bootfs(ctx: DetectContext) -> str | None:
    labels: list[str] = []
    if ctx.boot and ctx.boot.label:
        labels.append(ctx.boot.label)
    if ctx.layout and ctx.layout.fat_label:
        labels.append(ctx.layout.fat_label)
    for label in labels:
        if label.lower() in {"bootfs", "boot"}:
            return f"FAT volume label '{label}'"
    return None


def root_label_rootfs(ctx: DetectContext) -> str | None:
    labels: list[str] = []
    if ctx.root and ctx.root.label:
        labels.append(ctx.root.label)
    if ctx.layout and ctx.layout.linux_label:
        labels.append(ctx.layout.linux_label)
    for label in labels:
        if label.lower() == "rootfs":
            return "Linux volume label 'rootfs'"
    return None


def root_rpi_issue(ctx: DetectContext) -> str | None:
    root = _root(ctx)
    if not root:
        return None
    for path in ("etc/rpi-issue", "etc/rpi/issue"):
        text = root.text(path)
        if not text:
            continue
        if "Raspberry Pi reference" in text or "pi-gen" in text or "Raspbian" in text:
            snippet = text.strip().splitlines()[0][:120]
            return f"{path}: {snippet}"
    return None


def raspi_list(ctx: DetectContext) -> str | None:
    root = _root(ctx)
    if not root:
        return None
    for path in (
        "etc/apt/sources.list.d/raspi.list",
        "etc/apt/sources.list.d/raspi.list",
        "etc/apt/sources.list.d/raspi.sources",
    ):
        text = root.text(path)
        if text and re.search(r"raspberrypi\.(com|org)|archive\.raspberrypi", text, re.I):
            return f"{path} points at the Raspberry Pi Linux repo"
        if path in root.files:
            return f"{path} present"
    return None


def raspi_config(ctx: DetectContext) -> str | None:
    root = _root(ctx)
    if not root:
        return None
    if root.has_raspi_config or root.has_file("usr/bin/raspi-config"):
        return "/usr/bin/raspi-config present"
    return None


def os_release_rpi_os(ctx: DetectContext) -> str | None:
    root = _root(ctx)
    if not root:
        return None
    text = root.text("etc/os-release") or root.text("usr/lib/os-release")
    if not text:
        return None
    if re.search(r"Raspberry Pi OS", text, re.I):
        return "os-release mentions Raspberry Pi OS"
    if re.search(r"pretty_name=.*raspberry pi", text, re.I):
        return "os-release PRETTY_NAME mentions Raspberry Pi"
    return None


def root_rpi_issue_and_raspi_repo(ctx: DetectContext) -> str | None:
    a = root_rpi_issue(ctx)
    b = raspi_list(ctx)
    if a and b:
        return f"{a}; {b}"
    return None


def firmware_bootcode(ctx: DetectContext) -> str | None:
    boot = _boot(ctx)
    if boot and boot.has_file("bootcode.bin"):
        return "bootcode.bin present"
    return None


def firmware_start_elf(ctx: DetectContext) -> str | None:
    boot = _boot(ctx)
    if not boot:
        return None
    hits = [n for n in ("start.elf", "start4.elf", "start_cd.elf", "start4cd.elf", "start_x.elf", "start4x.elf") if boot.has_file(n)]
    if hits:
        return ", ".join(hits)
    if any(Path(f).name.startswith("start") and Path(f).name.endswith(".elf") for f in boot.files):
        return "start*.elf present"
    return None


def firmware_fixup(ctx: DetectContext) -> str | None:
    boot = _boot(ctx)
    if not boot:
        return None
    hits = [Path(f).name for f in boot.files if Path(f).name.startswith("fixup") and Path(f).name.endswith(".dat")]
    if hits:
        return ", ".join(sorted(set(hits)))
    return None


def firmware_dtb(ctx: DetectContext) -> str | None:
    boot = _boot(ctx)
    if not boot:
        return None
    hits = [
        Path(f).name
        for f in boot.files
        if Path(f).name.endswith(".dtb") and Path(f).name.startswith(("bcm270", "bcm271", "bcm27"))
    ]
    if hits:
        return f"{len(set(hits))} bcm27*.dtb file(s)"
    return None


def firmware_overlays(ctx: DetectContext) -> str | None:
    boot = _boot(ctx)
    if boot and boot.has_dir("overlays"):
        return "overlays/ directory present"
    return None


def firmware_kernel(ctx: DetectContext) -> str | None:
    boot = _boot(ctx)
    if not boot:
        return None
    kernels = [
        n
        for n in (
            "kernel.img",
            "kernel7.img",
            "kernel7l.img",
            "kernel8.img",
            "kernel_2712.img",
        )
        if boot.has_file(n)
    ]
    if kernels:
        return ", ".join(kernels)
    return None


def firmware_licence(ctx: DetectContext) -> str | None:
    boot = _boot(ctx)
    if not boot:
        return None
    for name in ("licence.broadcom", "license.broadcom", "licenсe.broadcom"):
        if boot.has_file(name):
            return name
    # LICENCE.broadcom is the usual spelling.
    if boot.has_file("LICENCE.broadcom".lower()):
        return "LICENCE.broadcom"
    return None


def firmware_config_txt(ctx: DetectContext) -> str | None:
    boot = _boot(ctx)
    if boot and boot.has_file("config.txt"):
        return "config.txt present"
    return None


def firmware_cmdline_txt(ctx: DetectContext) -> str | None:
    boot = _boot(ctx)
    if boot and boot.has_file("cmdline.txt"):
        return "cmdline.txt present"
    return None


def config_txt_rptl(ctx: DetectContext) -> str | None:
    boot = _boot(ctx)
    if not boot:
        return None
    text = boot.text("config.txt")
    if not text:
        return None
    hints = []
    if any(
        needle in text
        for needle in (
            "rptl.io/configtxt",
            "rptl.io/configtxt",
            "rpi.org/config.txt",
        )
    ):
        hints.append("Raspberry Pi Ltd config.txt comment")
    if re.search(r"^\s*arm_64bit\s*=\s*1", text, re.M):
        hints.append("arm_64bit=1")
    if "vc4-kms-v3d" in text:
        hints.append("dtoverlay=vc4-kms-v3d")
    if re.search(r"^\s*auto_initramfs\s*=\s*1", text, re.M) or re.search(
        r"^\s*auto_initramfs\s*=\s*1", text, re.M
    ):
        hints.append("auto_initramfs=1")
    if hints:
        return ", ".join(hints)
    return None


def classic_two_partition(ctx: DetectContext) -> str | None:
    layout = ctx.layout
    if not layout:
        return None
    if layout.has_fat and layout.has_linux:
        return "classic FAT boot + Linux root partition layout"
    return None


def cmdline_ext4_partuuid(ctx: DetectContext) -> str | None:
    boot = _boot(ctx)
    if not boot:
        return None
    text = boot.text("cmdline.txt")
    if not text:
        return None
    if re.search(r"root=PARTUUID=", text) and re.search(r"rootfstype=ext4", text):
        return "cmdline.txt root=PARTUUID=… rootfstype=ext4"
    if re.search(r"root=/dev/mmcblk\d+p2", text) and "ext4" in text:
        return "cmdline.txt root=/dev/mmcblk*p2 ext4"
    return None


def cmdline_resize(ctx: DetectContext) -> str | None:
    boot = _boot(ctx)
    if not boot:
        return None
    text = boot.text("cmdline.txt")
    if not text:
        return None
    if re.search(r"(?:^|\s)resize(?:\s|$)", text) or "init_resize" in text:
        return "cmdline.txt requests first-boot resize"
    return None


def trailing_free_space(ctx: DetectContext) -> str | None:
    layout = ctx.layout
    if not layout or layout.trailing_free_bytes is None:
        return None
    if layout.trailing_free_bytes >= 1_000_000_000:
        gb = layout.trailing_free_bytes / 1_000_000_000
        return f"{gb:.1f} GB unallocated after partitions"
    return None


def dietpi_txt(ctx: DetectContext) -> str | None:
    boot = _boot(ctx)
    if boot and (boot.has_file("dietpi.txt") or boot.texts_named("dietpi.txt")):
        return "dietpi.txt present"
    root = _root(ctx)
    if root and (root.has_file("boot/dietpi/.version") or root.has_file("etc/dietpi/func/dietpi-globals")):
        return "DietPi rootfs markers present"
    return None


def libreelec_system_kernel(ctx: DetectContext) -> str | None:
    boot = _boot(ctx)
    if not boot:
        return None
    has_system = boot.has_file("system")
    has_kernel = boot.has_file("kernel")
    if has_system and has_kernel:
        return "LibreELEC-style SYSTEM + KERNEL files"
    return None


def ubuntu_boot(ctx: DetectContext) -> str | None:
    boot = _boot(ctx)
    root = _root(ctx)
    clues: list[str] = []
    if boot:
        if boot.has_file("vmlinuz") or any(Path(f).name.startswith("vmlinuz") for f in boot.files):
            clues.append("vmlinuz")
        if boot.any_filename_contains("ubuntu"):
            clues.append("ubuntu filename")
        for text in boot.texts.values():
            if re.search(r"\bubuntu\b", text, re.I) and "raspberry pi os" not in text.lower():
                clues.append("ubuntu mentioned in boot text")
                break
    if root:
        osr = root.text("etc/os-release")
        if re.search(r"^ID=ubuntu", osr, re.M) or re.search(r"\bUbuntu\b", osr):
            clues.append("os-release ID=ubuntu")
    # vmlinuz alone is weak; require ubuntu mention OR os-release.
    if clues and any("ubuntu" in c.lower() for c in clues):
        return ", ".join(dict.fromkeys(clues))
    return None


def pinn_noobs(ctx: DetectContext) -> str | None:
    boot = _boot(ctx)
    if not boot:
        return None
    hits = []
    if boot.has_file("recovery.cmdline"):
        hits.append("recovery.cmdline")
    if boot.has_file("os.json") or boot.has_file("os_config.json"):
        hits.append("os.json")
    if boot.has_file("recovery.elf") or boot.has_file("recovery.img"):
        hits.append("recovery.elf/img")
    if boot.has_file("build-data"):
        hits.append("BUILD-DATA")
    if boot.has_dir("os") and (boot.has_file("recovery.cmdline") or boot.has_file("os.json")):
        hits.append("os/ folder")
    if hits:
        return "PINN/NOOBS markers: " + ", ".join(hits)
    return None


def kali_markers(ctx: DetectContext) -> str | None:
    boot = _boot(ctx)
    root = _root(ctx)
    if boot and (boot.has_file("kali.txt") or boot.has_file("kalipi.txt") or boot.any_filename_contains("kali")):
        return "Kali boot marker"
    if root:
        if root.has_file("etc/kali-version"):
            return "/etc/kali-version"
        osr = root.text("etc/os-release")
        if re.search(r"^ID=kali", osr, re.M) or re.search(r"\bKali\b", osr):
            return "os-release names Kali"
    return None


def foreign_os_release(ctx: DetectContext) -> str | None:
    root = _root(ctx)
    if not root:
        return None
    osr = root.text("etc/os-release") or root.text("usr/lib/os-release")
    m = re.search(r"^ID=([a-z0-9]+)", osr, re.M | re.I)
    if not m:
        return None
    distro_id = m.group(1).lower()
    foreign = {
        "fedora": "Fedora",
        "arch": "Arch Linux",
        "manjaro": "Manjaro",
        "alpine": "Alpine",
        "opensuse": "openSUSE",
        "gentoo": "Gentoo",
        "nixos": "NixOS",
    }
    if distro_id in foreign:
        return f"os-release ID={distro_id} ({foreign[distro_id]})"
    return None


def live_squashfs(ctx: DetectContext) -> str | None:
    boot = _boot(ctx)
    if not boot:
        return None
    text = boot.text("cmdline.txt") or boot.text("cmdline")
    if re.search(r"root=.*squashfs|boot=live|\bsquashfs\b", text, re.I):
        return "cmdline points at squashfs/live boot"
    if any(Path(f).name.endswith(".squashfs") for f in boot.files):
        return "squashfs image on boot partition"
    return None


def cloud_init_present(ctx: DetectContext) -> str | None:
    boot = _boot(ctx)
    if not boot:
        return None
    present = [n for n in ("user-data", "meta-data", "network-config") if boot.has_file(n) or boot.text(n)]
    if present:
        return ", ".join(present)
    return None


def serial_enabled(ctx: DetectContext) -> str | None:
    boot = _boot(ctx)
    if not boot:
        return None
    cfg = boot.text("config.txt")
    ud = boot.text("user-data")
    bits = []
    if re.search(r"^\s*enable_uart\s*=\s*1", cfg, re.M):
        bits.append("config.txt enable_uart=1")
    if re.search(r"enable_uart\s*:\s*(true|1)", ud, re.I):
        bits.append("user-data enable_uart")
    if bits:
        return ", ".join(bits)
    return None


RULES: tuple[Rule, ...] = (
    Rule("issue_txt_pi_gen", Weight.CERTAIN, "official_smoking", issue_txt_pi_gen),
    Rule("root_rpi_issue_and_raspi_repo", Weight.CERTAIN, "official_smoking", root_rpi_issue_and_raspi_repo),
    Rule("issue_txt_custom_pi_gen", Weight.HIGH, "like", issue_txt_custom_pi_gen),
    Rule("issue_txt_raspbian", Weight.HIGH, "official_support", issue_txt_raspbian),
    Rule("meta_data_rpios_image", Weight.HIGH, "official_support", meta_data_rpios_image),
    Rule("boot_label_bootfs", Weight.MEDIUM, "official_support", boot_label_bootfs),
    Rule("root_label_rootfs", Weight.MEDIUM, "official_support", root_label_rootfs),
    Rule("root_rpi_issue", Weight.HIGH, "official_support", root_rpi_issue),
    Rule("raspi_list", Weight.HIGH, "official_support", raspi_list),
    Rule("raspi_config", Weight.HIGH, "official_support", raspi_config),
    Rule("os_release_rpi_os", Weight.HIGH, "official_support", os_release_rpi_os),
    Rule("firmware_bootcode", Weight.LOW, "firmware", firmware_bootcode),
    Rule("firmware_start_elf", Weight.LOW, "firmware", firmware_start_elf),
    Rule("firmware_fixup", Weight.LOW, "firmware", firmware_fixup),
    Rule("firmware_dtb", Weight.LOW, "firmware", firmware_dtb),
    Rule("firmware_overlays", Weight.LOW, "firmware", firmware_overlays),
    Rule("firmware_kernel", Weight.LOW, "firmware", firmware_kernel),
    Rule("firmware_licence", Weight.LOW, "firmware", firmware_licence),
    Rule("firmware_config_txt", Weight.LOW, "firmware", firmware_config_txt),
    Rule("firmware_cmdline_txt", Weight.LOW, "firmware", firmware_cmdline_txt),
    Rule("config_txt_rptl", Weight.MEDIUM, "official_support", config_txt_rptl),
    Rule("classic_two_partition", Weight.MEDIUM, "layout", classic_two_partition),
    Rule("cmdline_ext4_partuuid", Weight.MEDIUM, "layout", cmdline_ext4_partuuid),
    Rule("cmdline_resize", Weight.LOW, "layout", cmdline_resize),
    Rule("trailing_free_space", Weight.LOW, "layout", trailing_free_space),
    Rule("dietpi_txt", Weight.CERTAIN, "negative", dietpi_txt, os_guess="DietPi"),
    Rule("libreelec_system_kernel", Weight.CERTAIN, "negative", libreelec_system_kernel, os_guess="LibreELEC"),
    Rule("ubuntu_boot", Weight.HIGH, "negative", ubuntu_boot, os_guess="Ubuntu for Raspberry Pi"),
    Rule("pinn_noobs", Weight.HIGH, "negative", pinn_noobs, os_guess="PINN/NOOBS"),
    Rule("kali_markers", Weight.HIGH, "negative", kali_markers, os_guess="Kali Linux"),
    Rule("foreign_os_release", Weight.HIGH, "negative", foreign_os_release),
    Rule("live_squashfs", Weight.HIGH, "negative", live_squashfs, os_guess="live/appliance image"),
    Rule("cloud_init_present", Weight.LOW, "fact", cloud_init_present),
    Rule("serial_enabled", Weight.LOW, "fact", serial_enabled),
)


def parse_issue_metadata(text: str) -> tuple[str | None, int | None, str | None]:
    """Return (image_date, stage, commit) from issue.txt."""
    date = None
    m = REFERENCE_RE.search(text)
    if m:
        date = m.group(1)
    stage = None
    commit = None
    line = PI_GEN_LINE_RE.search(text)
    if line:
        commit = line.group(2)
        stage = int(line.group(3))
    else:
        sm = STAGE_RE.search(text)
        if sm:
            stage = int(sm.group(1))
        cm = COMMIT_RE.search(text)
        if cm:
            commit = cm.group(1)
    return date, stage, commit


def edition_from_stage(stage: int | None) -> str | None:
    if stage == 2:
        return "lite"
    if stage == 4:
        return "desktop"
    if stage == 5:
        return "full"
    return None
