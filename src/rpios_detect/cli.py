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
            "contain Raspberry Pi OS? Firmware files alone are not enough."
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


def run(argv: list[str] | None = None) -> int:
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
