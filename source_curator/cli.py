"""Command line interface for source-curator-tool."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .inspector import DEFAULT_TIMEOUT, Inspection, InspectionError, inspect


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="source_tool.py",
        description="Inspect a web source and report what the server returned.",
    )
    parser.add_argument("--version", action="version", version=__version__)

    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser(
        "inspect", help="Fetch a URL and print status, final URL, domain, title, content type."
    )
    inspect_parser.add_argument("url", help="An http:// or https:// URL.")
    inspect_parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Request timeout in seconds (default: {DEFAULT_TIMEOUT:g}).",
    )
    return parser


def format_inspection(result: Inspection) -> str:
    rows = [
        ("Status", str(result.status_code)),
        ("Final URL", result.final_url),
        ("Domain", result.domain or "(unknown)"),
        ("Title", result.title or "(no title found)"),
        ("Content-Type", result.content_type or "(not reported)"),
    ]
    width = max(len(label) for label, _ in rows)
    return "\n".join(f"{label + ':':<{width + 1}} {value}" for label, value in rows)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        result = inspect(args.url, timeout=args.timeout)
    except InspectionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(format_inspection(result))
    return 0
