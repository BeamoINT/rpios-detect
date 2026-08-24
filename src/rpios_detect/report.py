"""Human-readable report."""

from __future__ import annotations

from rpios_detect.models import ScanReport, TargetResult


def format_report(report: ScanReport, *, verbose: bool = False) -> str:
    lines: list[str] = [
        f"{report.tool} {report.tool_version}",
        f"Scanned: {report.scanned_at}  host={report.host.os}/{report.host.arch}",
        "",
    ]
    if not report.results:
        lines.append("No candidate media found.")
        lines.append("Insert a MicroSD in a USB reader, or pass a device, mount, directory, or .img path.")
        return "\n".join(lines) + "\n"
    for i, result in enumerate(report.results):
        if i:
            lines.append("")
            lines.append("-" * 60)
            lines.append("")
        lines.extend(_format_result(result, verbose=verbose))
    return "\n".join(lines) + "\n"


def _format_result(result: TargetResult, *, verbose: bool) -> list[str]:
    media = result.media
    size = _fmt_size(media.size_bytes)
    lines = [
        f"Target: {result.target}",
        f"  Media: {media.kind.value}  {size}  {media.partition_table.value}  {media.bus.value}",
        f"  Verdict: {result.verdict.value}  (confidence: {result.confidence.value})",
    ]
    if result.os_name:
        lines.append(f"  OS: {result.os_name}")
    if result.edition.value != "unknown":
        extra = f" (pi-gen stage {result.pi_gen_stage})" if result.pi_gen_stage is not None else ""
        lines.append(f"  Edition: {result.edition.value}{extra}")
    if result.image_date:
        lines.append(f"  Image date: {result.image_date}")
    if result.os_version_hint:
        lines.append(f"  Version hint: {result.os_version_hint}")
    if result.pi_gen_commit:
        lines.append(f"  pi-gen commit: {result.pi_gen_commit}")
    lines.append(f"  First-boot resize pending: {'yes' if result.first_boot_resize_pending else 'no'}")
    if result.likely_boards:
        lines.append(f"  Likely boards: {', '.join(result.likely_boards)}")
    if result.other_os_guess:
        lines.append(f"  Other OS guess: {result.other_os_guess}")
    if result.live_system:
        lines.append("  Live system: yes (this host's boot device)")
    if result.cloud_init_present:
        lines.append("  Cloud-init: present on boot partition")
    if result.partitions:
        lines.append("  Partitions:")
        for p in result.partitions:
            mp = p.mountpoint or "unmounted"
            readable = "readable" if p.readable else "not readable"
            label = f" '{p.label}'" if p.label else ""
            sz = _fmt_size(p.size_bytes)
            lines.append(f"    - {p.id}  {p.type}{label}  {sz}  role={p.role.value}  {mp}  {readable}")
    if result.evidence:
        lines.append("  Evidence:")
        for ev in result.evidence:
            lines.append(f"    - [{ev.weight.value}] {ev.id}: {ev.detail}")
    if result.warnings:
        lines.append("  Warnings:")
        for w in result.warnings:
            lines.append(f"    - {w}")
    if verbose and result.rule_log:
        lines.append("  Rule log:")
        for line in result.rule_log:
            lines.append(f"    {line}")
    return lines


def _fmt_size(n: int | None) -> str:
    if n is None:
        return "?"
    if n >= 10**9:
        return f"{n / 10**9:.1f} GB"
    if n >= 10**6:
        return f"{n / 10**6:.1f} MB"
    return f"{n} B"
