"""Command line interface for source-curator-tool."""

from __future__ import annotations

import argparse
import sys

from . import __version__, discovery, evaluation, library
from .feeds import FeedInfo
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

    add_parser = subparsers.add_parser(
        "add", help="Inspect a URL and save it to the local source library."
    )
    add_parser.add_argument("url", help="An http:// or https:// URL.")
    add_parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Request timeout in seconds (default: {DEFAULT_TIMEOUT:g}).",
    )
    add_parser.add_argument(
        "--db",
        default=str(library.DEFAULT_DB_PATH),
        help=f"Path to the SQLite database (default: {library.DEFAULT_DB_PATH}).",
    )

    list_parser = subparsers.add_parser("list", help="List saved sources.")
    list_parser.add_argument(
        "--db",
        default=str(library.DEFAULT_DB_PATH),
        help=f"Path to the SQLite database (default: {library.DEFAULT_DB_PATH}).",
    )

    show_parser = subparsers.add_parser("show", help="Show one saved source in full.")
    show_parser.add_argument("id", type=int, help="Saved source ID, from `list`.")
    show_parser.add_argument(
        "--db",
        default=str(library.DEFAULT_DB_PATH),
        help=f"Path to the SQLite database (default: {library.DEFAULT_DB_PATH}).",
    )

    discover_parser = subparsers.add_parser(
        "discover", help="Search the web for candidate source websites on a topic."
    )
    discover_parser.add_argument("query", help="A research topic, e.g. \"stablecoin regulation\".")
    discover_parser.add_argument(
        "--limit",
        type=int,
        default=discovery.DEFAULT_LIMIT,
        help=f"Maximum raw search results to request (default: {discovery.DEFAULT_LIMIT}).",
    )
    discover_parser.add_argument(
        "--db",
        default=str(library.DEFAULT_DB_PATH),
        help="Path to the SQLite database used to mark already-saved domains "
        f"(default: {library.DEFAULT_DB_PATH}).",
    )

    evaluate_parser = subparsers.add_parser(
        "evaluate", help="Discover, inspect, and rank candidate sources for a topic."
    )
    evaluate_parser.add_argument("query", help="A research topic, e.g. \"stablecoin regulation\".")
    evaluate_parser.add_argument(
        "--limit",
        type=int,
        default=evaluation.DEFAULT_EVALUATE_LIMIT,
        help=f"Maximum candidates to discover and inspect (default: {evaluation.DEFAULT_EVALUATE_LIMIT}).",
    )
    evaluate_parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Per-candidate page/feed fetch timeout in seconds (default: {DEFAULT_TIMEOUT:g}).",
    )
    evaluate_parser.add_argument(
        "--db",
        default=str(library.DEFAULT_DB_PATH),
        help="Path to the SQLite database used to mark already-saved domains "
        f"(default: {library.DEFAULT_DB_PATH}).",
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
    lines = [f"{label + ':':<{width + 1}} {value}" for label, value in rows]
    lines.append("")
    lines.extend(format_feed(result.feed))
    return "\n".join(lines)


def format_feed(feed: FeedInfo) -> list[str]:
    lines = [f"Feed found:  {'Yes' if feed.found else 'No'}"]
    if not feed.found:
        return lines

    lines.append(f"Feed URL:    {feed.feed_url}")
    if feed.error:
        lines.append(f"Feed error:  {feed.error}")
        return lines

    lines.append(f"Feed title:  {feed.title or '(no title found)'}")
    if not feed.items:
        lines.append("Items:       (none found)")
        return lines

    lines.append(f"Items ({len(feed.items)}):")
    for index, item in enumerate(feed.items, start=1):
        lines.append(f"  {index}. {item.title or '(untitled)'}")
        lines.append(f"     Link:      {item.link or '(no link)'}")
        lines.append(f"     Published: {item.published or '(unknown)'}")
    return lines


def format_source_row(row) -> str:
    feed = "Yes" if row["feed_url"] else "No"
    title = row["title"] or "(no title)"
    return f"#{row['id']:<4} {title:<40.40} {row['domain']:<30.30} {row['status']:<10} Feed: {feed}"


def format_source_detail(row) -> str:
    rows = [
        ("ID", str(row["id"])),
        ("Requested URL", row["requested_url"]),
        ("Final URL", row["final_url"]),
        ("Domain", row["domain"]),
        ("Title", row["title"] or "(no title found)"),
        ("Content-Type", row["content_type"] or "(not reported)"),
        ("Feed URL", row["feed_url"] or "(none detected)"),
        ("Status", row["status"]),
        ("Added", row["added_at"]),
    ]
    width = max(len(label) for label, _ in rows)
    return "\n".join(f"{label + ':':<{width + 1}} {value}" for label, value in rows)


def format_candidate(candidate: discovery.Candidate) -> str:
    lines = [
        f"{candidate.rank}. {candidate.title}",
        f"   Domain:  {candidate.domain or '(unknown)'}",
        f"   URL:     {candidate.url}",
    ]
    if candidate.snippet:
        lines.append(f"   Snippet: {candidate.snippet}")
    lines.append(f"   Already saved: {'Yes' if candidate.already_saved else 'No'}")
    return "\n".join(lines)


def format_discovery_summary(stats: discovery.DiscoveryStats) -> str:
    return "\n".join(
        [
            f"Raw results found:     {stats.raw_count}",
            f"Duplicates removed:    {stats.duplicate_count}",
            f"Unique source domains: {stats.unique_domain_count}",
        ]
    )


def format_evaluated_candidate(item: evaluation.EvaluatedCandidate) -> str:
    return "\n".join(
        [
            f"{item.rank}. {item.title}",
            f"   Domain:         {item.domain}",
            f"   URL:            {item.url}",
            f"   Curation score: {item.score}/100",
            f"   Relevance:      {item.relevance_label}",
            f"   Reachable:      {'Yes' if item.reachable else 'No'}",
            f"   Feed:           {'Yes' if item.feed_found else 'No'}",
            f"   Freshness:      {item.freshness}",
            f"   Already saved:  {'Yes' if item.already_saved else 'No'}",
            f"   Why:            {item.explanation}",
        ]
    )


def cmd_inspect(args: argparse.Namespace) -> int:
    try:
        result = inspect(args.url, timeout=args.timeout)
    except InspectionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(format_inspection(result))
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    try:
        result = inspect(args.url, timeout=args.timeout)
    except InspectionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    feed_url = result.feed.feed_url if result.feed.found else None

    conn = library.connect(args.db)
    try:
        try:
            saved, same_domain = library.add_source(
                conn,
                requested_url=result.requested_url,
                final_url=result.final_url,
                domain=result.domain,
                title=result.title,
                content_type=result.content_type,
                feed_url=feed_url,
            )
        except library.DuplicateSourceError as exc:
            print(f"Not saved: {exc}", file=sys.stderr)
            return 1
    finally:
        conn.close()

    print(f"Saved source #{saved['id']}: {saved['title'] or '(no title)'} ({saved['domain']})")
    if same_domain:
        ids = ", ".join(f"#{row['id']}" for row in same_domain)
        print(
            f"Note: domain '{saved['domain']}' already has "
            f"{len(same_domain)} saved source(s): {ids}"
        )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    conn = library.connect(args.db)
    try:
        rows = library.list_sources(conn)
    finally:
        conn.close()

    if not rows:
        print("No sources saved yet.")
        return 0

    for row in rows:
        print(format_source_row(row))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    conn = library.connect(args.db)
    try:
        row = library.get_source(conn, args.id)
    finally:
        conn.close()

    if row is None:
        print(f"Error: no saved source with ID {args.id}", file=sys.stderr)
        return 1

    print(format_source_detail(row))
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    try:
        raw_results = discovery.search(args.query, limit=args.limit)
    except discovery.SearchError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not raw_results:
        print(f"No results found for {args.query!r}.")
        return 0

    conn = library.connect(args.db)
    try:
        saved_domains = {row["domain"] for row in library.list_sources(conn)}
    finally:
        conn.close()

    candidates, stats = discovery.build_candidates(raw_results, saved_domains)

    for candidate in candidates:
        print(format_candidate(candidate))
        print()
    print(format_discovery_summary(stats))
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    conn = library.connect(args.db)
    try:
        saved_domains = {row["domain"] for row in library.list_sources(conn)}
    finally:
        conn.close()

    try:
        evaluated, stats = evaluation.run_evaluation(
            args.query, limit=args.limit, saved_domains=saved_domains, timeout=args.timeout
        )
    except discovery.SearchError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not evaluated:
        print(f"No results found for {args.query!r}.")
        return 0

    for item in evaluated:
        print(format_evaluated_candidate(item))
        print()
    print(format_discovery_summary(stats))
    return 0


COMMANDS = {
    "inspect": cmd_inspect,
    "add": cmd_add,
    "list": cmd_list,
    "show": cmd_show,
    "discover": cmd_discover,
    "evaluate": cmd_evaluate,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return COMMANDS[args.command](args)
