from __future__ import annotations

import argparse
import sys

from .copying import CopyOptions, run_copy
from .ledger import ROLES, scan_music_root
from .reporting import render_report


def add_cache_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--cache",
        help="Known-drive cache directory. Defaults to LIBRARYSYNC_CACHE or ~/.music-ledger/known-drives.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="library-sync")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Scan one connected Music root.")
    scan_parser.add_argument("--root", required=True, help="Path to the Music root.")
    scan_parser.add_argument("--role", choices=sorted(ROLES), help="Drive role.")
    add_cache_argument(scan_parser)
    scan_parser.set_defaults(func=handle_scan)

    report_parser = subparsers.add_parser("report", help="Report differences from cached scans.")
    report_parser.add_argument(
        "--artist-level",
        action="append",
        default=[],
        help="Force artist-level grouping for a relative artist path, e.g. F/Frank Zappa.",
    )
    add_cache_argument(report_parser)
    report_parser.set_defaults(func=handle_report)

    copy_parser = subparsers.add_parser("copy", help="Plan or copy folders between two connected Music roots.")
    copy_parser.add_argument("--source", required=True, help="Source Music root.")
    copy_parser.add_argument("--target", required=True, help="Target Music root.")
    copy_parser.add_argument("--source-role", choices=sorted(ROLES), help="Source role.")
    copy_parser.add_argument("--target-role", choices=sorted(ROLES), help="Target role.")
    copy_parser.add_argument(
        "--artist-level",
        action="append",
        default=[],
        help="Force artist-level grouping for a relative artist path, e.g. F/Frank Zappa.",
    )
    copy_parser.add_argument("--execute", action="store_true", help="Actually copy files.")
    copy_parser.add_argument(
        "--yes",
        action="store_true",
        help="With --execute, accept every copy group.",
    )
    copy_parser.add_argument(
        "--replace-conflicts",
        action="store_true",
        help="Treat conflicts as replacements; only writes with --execute.",
    )
    add_cache_argument(copy_parser)
    copy_parser.set_defaults(func=handle_copy)

    return parser


def handle_scan(args: argparse.Namespace) -> int:
    info = scan_music_root(args.root, role=args.role, cache_dir=args.cache)
    print(
        f"Scanned role={info.role} files={info.file_count} reportable={info.reportable_count} "
        f"at {info.last_scan_at}"
    )
    return 0


def handle_report(args: argparse.Namespace) -> int:
    print(render_report(args.cache, set(args.artist_level)))
    return 0


def handle_copy(args: argparse.Namespace) -> int:
    return run_copy(
        CopyOptions(
            source=args.source,
            target=args.target,
            source_role=args.source_role,
            target_role=args.target_role,
            cache_dir=args.cache,
            execute=args.execute,
            yes=args.yes,
            replace_conflicts=args.replace_conflicts,
            artist_level=set(args.artist_level),
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def scan_main(argv: list[str] | None = None) -> int:
    return main(["scan", *(sys.argv[1:] if argv is None else argv)])


def report_main(argv: list[str] | None = None) -> int:
    return main(["report", *(sys.argv[1:] if argv is None else argv)])


def copy_main(argv: list[str] | None = None) -> int:
    return main(["copy", *(sys.argv[1:] if argv is None else argv)])
