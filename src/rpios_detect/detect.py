"""Pure evidence → verdict. No diskutil, no mounts, no subprocess."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from rpios_detect.evidence import RULES, DetectContext, Rule, parse_issue_metadata, edition_from_stage
from rpios_detect.models import (
    Confidence,
    Edition,
    EvidenceItem,
    Verdict,
    Weight,
)
from rpios_detect.snapshot import BootSnapshot, PartitionLayout, RootSnapshot

BOARD_ORDER = ("pi1", "pi_zero", "pi2", "pi3", "pi3_64", "pi4", "pi5")


@dataclass
class Detection:
    verdict: Verdict
    confidence: Confidence
    edition: Edition
    image_date: str | None
    pi_gen_stage: int | None
    pi_gen_commit: str | None
    os_name: str | None
    os_version_hint: str | None
    first_boot_resize_pending: bool
    likely_boards: list[str]
    other_os_guess: str | None
    evidence: list[EvidenceItem]
    warnings: list[str]
    cloud_init_present: bool
    rule_log: list[str] = field(default_factory=list)


def detect(
    boot: BootSnapshot | None,
    root: RootSnapshot | None = None,
    layout: PartitionLayout | None = None,
    *,
    verbose: bool = False,
) -> Detection:
    ctx = DetectContext(boot=boot, root=root, layout=layout)
    hits: list[tuple[Rule, str]] = []
    rule_log: list[str] = []
    for rule in RULES:
        detail = rule.matcher(ctx)
        if detail:
            hits.append((rule, detail))
            rule_log.append(f"HIT  {rule.id} ({rule.weight.value}): {detail}")
        elif verbose:
            rule_log.append(f"miss {rule.id}")

    evidence = [EvidenceItem(id=rule.id, weight=rule.weight, detail=detail) for rule, detail in hits]
    by_cat: dict[str, list[tuple[Rule, str]]] = {}
    ids: set[str] = set()
    for rule, detail in hits:
        by_cat.setdefault(rule.category, []).append((rule, detail))
        ids.add(rule.id)

    smoking = by_cat.get("official_smoking", [])
    support = by_cat.get("official_support", [])
    like = by_cat.get("like", [])
    firmware = by_cat.get("firmware", [])
    layout_hits = by_cat.get("layout", [])
    negative = by_cat.get("negative", [])

    warnings: list[str] = []
    if root is None or not root.readable:
        if layout and layout.has_linux:
            warnings.append(
                "root filesystem not readable on this host (typically ext4); verdict based on the boot partition"
            )

    image_date, stage, commit = _issue_fields(boot)
    edition = _edition(stage)
    boards = likely_boards(boot)
    cloud = "cloud_init_present" in ids
    resize_pending = "cmdline_resize" in ids and "trailing_free_space" in ids

    os_name = _os_name(ids, boot, root)
    version_hint = _version_hint(root, image_date)
    other_guess = _other_guess(negative)

    pinn = "pinn_noobs" in ids
    has_smoking = bool(smoking)
    strong_foreign = [h for h in negative if h[0].id in {"dietpi_txt", "ubuntu_boot", "libreelec_system_kernel", "kali_markers"}]

    if has_smoking and pinn:
        warnings.append(
            "PINN/NOOBS multi-boot manager is present; Raspberry Pi OS was found as an installed OS"
        )
        other_guess = "PINN/NOOBS"
        return Detection(
            verdict=Verdict.RASPBERRY_PI_OS,
            confidence=Confidence.CERTAIN,
            edition=edition,
            image_date=image_date,
            pi_gen_stage=stage,
            pi_gen_commit=commit,
            os_name=os_name or "Raspberry Pi OS",
            os_version_hint=version_hint,
            first_boot_resize_pending=resize_pending,
            likely_boards=boards,
            other_os_guess=other_guess,
            evidence=evidence,
            warnings=warnings,
            cloud_init_present=cloud,
            rule_log=rule_log,
        )

    if has_smoking and strong_foreign:
        warnings.append("official Raspberry Pi OS markers and a foreign-OS marker both present")
        return Detection(
            verdict=Verdict.OTHER_PI_OS,
            confidence=Confidence.HIGH,
            edition=edition,
            image_date=image_date,
            pi_gen_stage=stage,
            pi_gen_commit=commit,
            os_name=os_name,
            os_version_hint=version_hint,
            first_boot_resize_pending=resize_pending,
            likely_boards=boards,
            other_os_guess=other_guess,
            evidence=evidence,
            warnings=warnings,
            cloud_init_present=cloud,
            rule_log=rule_log,
        )

    if has_smoking:
        return Detection(
            verdict=Verdict.RASPBERRY_PI_OS,
            confidence=Confidence.CERTAIN,
            edition=edition,
            image_date=image_date,
            pi_gen_stage=stage,
            pi_gen_commit=commit,
            os_name=os_name or "Raspberry Pi OS",
            os_version_hint=version_hint,
            first_boot_resize_pending=resize_pending,
            likely_boards=boards,
            other_os_guess=None,
            evidence=evidence,
            warnings=warnings,
            cloud_init_present=cloud,
            rule_log=rule_log,
        )

    if negative and not has_smoking:
        conf = Confidence.HIGH if any(r.weight in {Weight.CERTAIN, Weight.HIGH} for r, _ in negative) else Confidence.MEDIUM
        return Detection(
            verdict=Verdict.OTHER_PI_OS,
            confidence=conf,
            edition=Edition.UNKNOWN,
            image_date=image_date,
            pi_gen_stage=stage,
            pi_gen_commit=commit,
            os_name=other_guess,
            os_version_hint=version_hint,
            first_boot_resize_pending=resize_pending,
            likely_boards=boards,
            other_os_guess=other_guess,
            evidence=evidence,
            warnings=warnings,
            cloud_init_present=cloud,
            rule_log=rule_log,
        )

    support_ids = {r.id for r, _ in support}
    officialish = sum(
        [
            "meta_data_rpios_image" in support_ids,
            "boot_label_bootfs" in support_ids or "root_label_rootfs" in support_ids,
            "raspi_config" in support_ids,
            "raspi_list" in support_ids,
            "os_release_rpi_os" in support_ids,
            "root_rpi_issue" in support_ids,
            "issue_txt_raspbian" in support_ids,
        ]
    )
    high_combo = (
        officialish >= 3
        or (
            "meta_data_rpios_image" in support_ids
            and ("boot_label_bootfs" in support_ids or "root_label_rootfs" in support_ids)
            and "raspi_config" in support_ids
        )
        or ("raspi_config" in support_ids and "raspi_list" in support_ids and "boot_label_bootfs" in support_ids)
    )
    if high_combo:
        name = os_name or "Raspberry Pi OS"
        return Detection(
            verdict=Verdict.RASPBERRY_PI_OS,
            confidence=Confidence.HIGH,
            edition=edition,
            image_date=image_date,
            pi_gen_stage=stage,
            pi_gen_commit=commit,
            os_name=name,
            os_version_hint=version_hint,
            first_boot_resize_pending=resize_pending,
            likely_boards=boards,
            other_os_guess=None,
            evidence=evidence,
            warnings=warnings,
            cloud_init_present=cloud,
            rule_log=rule_log,
        )

    if like:
        return Detection(
            verdict=Verdict.RASPBERRY_PI_OS_LIKE,
            confidence=Confidence.HIGH if like[0][0].weight == Weight.HIGH else Confidence.MEDIUM,
            edition=edition,
            image_date=image_date,
            pi_gen_stage=stage,
            pi_gen_commit=commit,
            os_name=os_name or "Raspberry Pi OS-like",
            os_version_hint=version_hint,
            first_boot_resize_pending=resize_pending,
            likely_boards=boards,
            other_os_guess=None,
            evidence=evidence,
            warnings=warnings,
            cloud_init_present=cloud,
            rule_log=rule_log,
        )

    classic = "classic_two_partition" in ids
    cmdline = "cmdline_ext4_partuuid" in ids
    if (classic or cmdline) and firmware and not negative:
        return Detection(
            verdict=Verdict.RASPBERRY_PI_OS_LIKE,
            confidence=Confidence.MEDIUM,
            edition=Edition.UNKNOWN,
            image_date=image_date,
            pi_gen_stage=stage,
            pi_gen_commit=commit,
            os_name=os_name,
            os_version_hint=version_hint,
            first_boot_resize_pending=resize_pending,
            likely_boards=boards,
            other_os_guess=None,
            evidence=evidence,
            warnings=warnings,
            cloud_init_present=cloud,
            rule_log=rule_log,
        )

    if officialish >= 1:
        return Detection(
            verdict=Verdict.RASPBERRY_PI_OS_LIKE,
            confidence=Confidence.MEDIUM,
            edition=edition,
            image_date=image_date,
            pi_gen_stage=stage,
            pi_gen_commit=commit,
            os_name=os_name or "Raspberry Pi OS-like",
            os_version_hint=version_hint,
            first_boot_resize_pending=resize_pending,
            likely_boards=boards,
            other_os_guess=None,
            evidence=evidence,
            warnings=warnings,
            cloud_init_present=cloud,
            rule_log=rule_log,
        )

    if len(firmware) >= 4:
        return Detection(
            verdict=Verdict.OTHER_PI_OS,
            confidence=Confidence.LOW,
            edition=Edition.UNKNOWN,
            image_date=None,
            pi_gen_stage=None,
            pi_gen_commit=None,
            os_name=None,
            os_version_hint=None,
            first_boot_resize_pending=resize_pending,
            likely_boards=boards,
            other_os_guess=None,
            evidence=evidence,
            warnings=warnings,
            cloud_init_present=cloud,
            rule_log=rule_log,
        )

    if firmware or layout_hits:
        return Detection(
            verdict=Verdict.UNKNOWN,
            confidence=Confidence.LOW,
            edition=Edition.UNKNOWN,
            image_date=None,
            pi_gen_stage=None,
            pi_gen_commit=None,
            os_name=None,
            os_version_hint=None,
            first_boot_resize_pending=False,
            likely_boards=boards,
            other_os_guess=None,
            evidence=evidence,
            warnings=warnings,
            cloud_init_present=cloud,
            rule_log=rule_log,
        )

    return Detection(
        verdict=Verdict.NOT_PI,
        confidence=Confidence.NONE,
        edition=Edition.UNKNOWN,
        image_date=None,
        pi_gen_stage=None,
        pi_gen_commit=None,
        os_name=None,
        os_version_hint=None,
        first_boot_resize_pending=False,
        likely_boards=[],
        other_os_guess=None,
        evidence=evidence,
        warnings=warnings,
        cloud_init_present=cloud,
        rule_log=rule_log,
    )


def likely_boards(boot: BootSnapshot | None) -> list[str]:
    if boot is None:
        return []
    names = {Path(f).name.lower() for f in boot.files}
    found: set[str] = set()
    if "kernel.img" in names:
        found.update({"pi1", "pi_zero"})
    if "kernel7.img" in names:
        found.update({"pi2", "pi3"})
    if "kernel7l.img" in names:
        found.add("pi4")
    if "kernel8.img" in names:
        found.update({"pi3_64", "pi4"})
    if "kernel_2712.img" in names:
        found.add("pi5")
    for name in names:
        if not name.endswith(".dtb"):
            continue
        if name.startswith("bcm2712"):
            found.add("pi5")
        elif name.startswith("bcm2711"):
            found.add("pi4")
        elif name.startswith("bcm2710"):
            found.add("pi3")
        elif name.startswith("bcm2709"):
            found.add("pi2")
        elif name.startswith("bcm2708"):
            found.update({"pi1", "pi_zero"})
    return [b for b in BOARD_ORDER if b in found]


def _issue_fields(boot: BootSnapshot | None) -> tuple[str | None, int | None, str | None]:
    if boot is None:
        return None, None, None
    for text in boot.texts_named("issue.txt"):
        date, stage, commit = parse_issue_metadata(text)
        if date or stage or commit:
            return date, stage, commit
    return None, None, None


def _edition(stage: int | None) -> Edition:
    name = edition_from_stage(stage)
    if name == "lite":
        return Edition.LITE
    if name == "desktop":
        return Edition.DESKTOP
    if name == "full":
        return Edition.FULL
    return Edition.UNKNOWN


def _os_name(ids: set[str], boot: BootSnapshot | None, root: RootSnapshot | None) -> str | None:
    if "issue_txt_raspbian" in ids and "issue_txt_pi_gen" not in ids:
        return "Raspbian"
    if "issue_txt_pi_gen" in ids or "root_rpi_issue_and_raspi_repo" in ids:
        if boot:
            for text in boot.texts_named("issue.txt"):
                if re.search(r"\bRaspbian\b", text) and "Raspberry Pi reference" not in text:
                    return "Raspbian"
        return "Raspberry Pi OS"
    if "os_release_rpi_os" in ids:
        return "Raspberry Pi OS"
    if root:
        osr = root.text("etc/os-release")
        m = re.search(r'^PRETTY_NAME="([^"]+)"', osr, re.M)
        if m and "raspberry" in m.group(1).lower():
            return m.group(1)
    return None


def _version_hint(root: RootSnapshot | None, image_date: str | None) -> str | None:
    if root and root.readable:
        osr = root.text("etc/os-release") or root.text("usr/lib/os-release")
        m = re.search(r"^VERSION_CODENAME=(\S+)", osr, re.M)
        if m:
            return f"Debian {m.group(1)} (from os-release)"
        raspi = root.text("etc/apt/sources.list.d/raspi.list") or root.text(
            "etc/apt/sources.list.d/raspi.list"
        )
        for suite in ("forky", "trixie", "bookworm", "bullseye"):
            if suite in raspi.lower():
                return f"Debian {suite} (from raspi.list)"
        m = re.search(r"^VERSION_ID=\"?([^\"]+)\"?", osr, re.M)
        if m:
            return f"Debian {m.group(1)} (from os-release)"
    if image_date:
        return f"Debian release unknown (image dated {image_date}; not inferred from the date)"
    return None


def _other_guess(negative: list[tuple[Rule, str]]) -> str | None:
    for rule, detail in negative:
        if rule.os_guess:
            return rule.os_guess
        if rule.id == "foreign_os_release":
            m = re.search(r"\(([^)]+)\)", detail)
            if m:
                return m.group(1)
            return detail
    return None
