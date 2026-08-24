"""rpios-detect command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
import traceback

from rpios_detect import __version__
from rpios_detect.models import EXIT_INTERNAL, EXIT_USAGE
from rpios_detect.report import format_report
from rpios_detect.scan import exit_code_for, scan_targets


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rpios-detect",
        description=(
            "Read-only detector: does this MicroSD card (or boot volume/image) "
            "contain Raspberry Pi OS? Firmware files alone are not enough. "
            "Use 'rpios-detect watch' for a continuous insert-scan-eject station."
        ),
    )
    parser.add_argument(
        "targets",
        nargs="*",
        help="device (/dev/disk4, /dev/sda), mount (/Volumes/bootfs), directory, drive letter (E:), or .img",
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument(
        "--all",
        action="store_true",
        dest="all_disks",
        help="inspect every candidate, not only obvious removable media",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="print each evidence rule hit/miss on stderr",
    )
    parser.add_argument("--version", action="version", version=f"rpios-detect {__version__}")
    return parser


RPIV_HELP = """rpiv — Raspberry Pi OS card station

  rpiv                     start the insert-scan-eject station
  rpiv watch [flags]       same (explicit)
  rpiv PATH [PATH ...]     one-shot inspect (same as rpios-detect)
  rpios-detect             inspect currently connected removable disks

Install (any machine with Python 3.11+):

  curl -fsSL https://raw.githubusercontent.com/BeamoINT/rpios-detect/main/install.sh | bash
  rpiv

Station flags: --no-eject  --once  --json  --no-beep  --color/--no-color
"""


def run_rpiv(argv: list[str] | None = None) -> int:
    """Short command: `rpiv` runs the station; paths still one-shot inspect."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"-h", "--help"}:
        print(RPIV_HELP)
        return 0
    if argv and argv[0] in {"-V", "--version"}:
        print(f"rpiv {__version__}")
        return 0
    if argv and argv[0] == "watch":
        from rpios_detect.watch import run_watch_cli

        return run_watch_cli(argv[1:], prog="rpiv")
    positionals = [a for a in argv if not a.startswith("-")]
    oneshot_flags = {"--all"}
    if positionals or any(a in oneshot_flags or a.startswith("--all=") for a in argv):
        return run(argv)
    from rpios_detect.watch import run_watch_cli

    return run_watch_cli(argv, prog="rpiv")


def rpiv_main(argv: list[str] | None = None) -> None:
    raise SystemExit(run_rpiv(argv))


def run(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "watch":
        from rpios_detect.watch import run_watch_cli

        return run_watch_cli(argv[1:])
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        if code in (None, 0):
            return 0
        return EXIT_USAGE
    try:
        report = scan_targets(list(args.targets), all_disks=args.all_disks, verbose=args.verbose)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return EXIT_INTERNAL
    except Exception as exc:  # noqa: BLE001
        print(f"internal error: {exc}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return EXIT_INTERNAL
    if args.verbose:
        for result in report.results:
            if result.rule_log:
                print(f"# rules for {result.target}", file=sys.stderr)
                for line in result.rule_log:
                    print(line, file=sys.stderr)
    if args.json:
        json.dump(report.to_dict(), sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(format_report(report, verbose=args.verbose))
    return exit_code_for(report.results)


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(run(argv))
