"""Command-line interface for dupfind.

Exit codes:
    0  no duplicates found
    1  duplicates found (or files deleted / would be deleted)
    2  usage error (bad arguments, missing path)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import ScanResult, find_duplicates

SIZE_SUFFIXES = {"b": 1, "k": 1024, "kb": 1024, "m": 1024**2, "mb": 1024**2,
                 "g": 1024**3, "gb": 1024**3}


def parse_size(text: str) -> int:
    """Parse a byte count like ``1024``, ``10K`` or ``5MB`` into bytes."""
    cleaned = text.strip().lower()
    for suffix in sorted(SIZE_SUFFIXES, key=len, reverse=True):
        if cleaned.endswith(suffix):
            number = cleaned[: -len(suffix)] or "0"
            try:
                return int(float(number) * SIZE_SUFFIXES[suffix])
            except ValueError:
                raise argparse.ArgumentTypeError(
                    f"invalid --min-size value: {text!r}"
                ) from None
    try:
        value = int(cleaned)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid --min-size value: {text!r}"
        ) from None
    if value < 0:
        raise argparse.ArgumentTypeError("--min-size must not be negative")
    return value


def format_size(num_bytes: int) -> str:
    """Human-readable byte count, e.g. 1536 -> '1.5 KiB'."""
    value = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024 or unit == "TiB":
            if unit == "B":
                return f"{num_bytes} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{num_bytes} B"  # pragma: no cover


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dupfind",
        description=(
            "Find duplicate files by content: groups files by size, then "
            "SHA-256-hashes only same-size candidates."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="one or more files or directories to scan",
    )
    parser.add_argument(
        "--min-size",
        type=parse_size,
        default=0,
        metavar="SIZE",
        help="skip files smaller than SIZE, e.g. 1024, 10K, 5MB (default: 0)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help="glob pattern to skip (repeatable); matches file names and "
        "path parts, e.g. --exclude '*.tmp' --exclude '.git'",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print machine-readable JSON instead of the human report",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="interactively delete duplicate copies (keeps the first "
        "file of each group); asks for confirmation per group",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="with --delete, only show what would be deleted",
    )
    parser.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="follow symbolic links (off by default; links are skipped)",
    )
    return parser


def print_human_report(result: ScanResult) -> None:
    if not result.groups:
        print("No duplicates found.")
    for index, group in enumerate(result.groups, start=1):
        print(
            f"\nGroup {index}: {len(group.paths)} files, "
            f"{format_size(group.size)} each, "
            f"sha256 {group.sha256[:12]}…"
        )
        for path in group.paths:
            print(f"  {path}")
    print(
        f"\nScanned {result.files_scanned} file(s); "
        f"{len(result.groups)} duplicate group(s); "
        f"{result.duplicate_files} duplicate file(s); "
        f"{format_size(result.bytes_reclaimable)} reclaimable "
        f"({result.bytes_reclaimable} bytes)."
    )
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)


def confirm(prompt: str) -> bool:
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("y", "yes")


def handle_delete(result: ScanResult, *, dry_run: bool) -> int:
    """Delete (or preview deleting) every copy except the first per group.

    Returns the number of files deleted, or that would be deleted.
    """
    deleted = 0
    for group in result.groups:
        victims = group.paths[1:]
        if dry_run:
            print(f"\nWould delete {len(victims)} file(s) from group "
                  f"({format_size(group.size)} each), keeping {group.paths[0]}:")
            for path in victims:
                print(f"  {path}")
            deleted += len(victims)
            continue
        print(f"\nGroup: {len(group.paths)} files, "
              f"{format_size(group.size)} each; keep {group.paths[0]}")
        for path in victims:
            print(f"  {path}")
        if not confirm("Delete these copies?"):
            print("Skipped.")
            continue
        for path in victims:
            try:
                path.unlink()
            except OSError as exc:
                print(f"warning: could not delete {path}: {exc}",
                      file=sys.stderr)
                continue
            print(f"Deleted {path}")
            deleted += 1
    return deleted


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.dry_run and not args.delete:
        parser.error("--dry-run only makes sense together with --delete")

    missing = [p for p in args.paths if not p.exists()]
    if missing:
        for path in missing:
            print(f"error: path does not exist: {path}", file=sys.stderr)
        return 2

    result = find_duplicates(
        args.paths,
        min_size=args.min_size,
        exclude=args.exclude,
        follow_symlinks=args.follow_symlinks,
    )

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print_human_report(result)

    if args.delete and result.groups:
        deleted = handle_delete(result, dry_run=args.dry_run)
        if args.dry_run:
            print(f"\nDry run: {deleted} file(s) would be deleted, "
                  f"freeing {format_size(result.bytes_reclaimable)}.")
        else:
            print(f"\nDeleted {deleted} file(s).")

    return 1 if result.groups else 0


if __name__ == "__main__":
    raise SystemExit(main())
