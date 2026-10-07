"""Command line interface for source-curator-tool."""

from __future__ import annotations

import argparse
import sys

from . import __version__, config_export, discovery, evaluation, library, review
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
        "--status",
        choices=(library.STATUS_PENDING, library.STATUS_APPROVED, library.STATUS_REJECTED),
        help="Only list sources with this approval status.",
    )
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

    for name, status in (
        ("approve", library.STATUS_APPROVED),
        ("reject", library.STATUS_REJECTED),
        ("pending", library.STATUS_PENDING),
    ):
        status_parser = subparsers.add_parser(name, help=f"Mark a saved source as {status}.")
        status_parser.add_argument("id", type=int, help="Saved source ID, from `list`.")
        status_parser.add_argument(
            "--db",
            default=str(library.DEFAULT_DB_PATH),
            help=f"Path to the SQLite database (default: {library.DEFAULT_DB_PATH}).",
        )

    note_parser = subparsers.add_parser("note", help="Set (replace) a saved source's curator notes.")
    note_parser.add_argument("id", type=int, help="Saved source ID, from `list`.")
    note_parser.add_argument("text", help="Free-text note. Quote it if it contains spaces.")
    note_parser.add_argument(
        "--db",
        default=str(library.DEFAULT_DB_PATH),
        help=f"Path to the SQLite database (default: {library.DEFAULT_DB_PATH}).",
    )

    tag_parser = subparsers.add_parser(
        "tag", help="Set (replace) a saved source's tags. Lower-cased, deduplicated."
    )
    tag_parser.add_argument("id", type=int, help="Saved source ID, from `list`.")
    tag_parser.add_argument("tags", nargs="+", help="One or more tags.")
    tag_parser.add_argument(
        "--db",
        default=str(library.DEFAULT_DB_PATH),
        help=f"Path to the SQLite database (default: {library.DEFAULT_DB_PATH}).",
    )

    find_parser = subparsers.add_parser(
        "find", help="Search your saved source library locally (title, domain, URL, notes, tags)."
    )
    find_parser.add_argument("query", help="Search text, e.g. \"stablecoin regulation\".")
    find_parser.add_argument(
        "--status",
        choices=(library.STATUS_PENDING, library.STATUS_APPROVED, library.STATUS_REJECTED),
        help="Only search sources with this approval status.",
    )
    find_parser.add_argument(
        "--db",
        default=str(library.DEFAULT_DB_PATH),
        help=f"Path to the SQLite database (default: {library.DEFAULT_DB_PATH}).",
    )

    review_parser = subparsers.add_parser(
        "review", help="Generate a Markdown review file for a saved source."
    )
    review_parser.add_argument("id", type=int, help="Saved source ID, from `list`.")
    review_parser.add_argument(
        "--overwrite", action="store_true", help="Replace the review file if it already exists."
    )
    review_parser.add_argument(
        "--dir",
        default=str(review.DEFAULT_REVIEWS_DIR),
        help=f"Directory to write the review into (default: {review.DEFAULT_REVIEWS_DIR}).",
    )
    review_parser.add_argument(
        "--db",
        default=str(library.DEFAULT_DB_PATH),
        help=f"Path to the SQLite database (default: {library.DEFAULT_DB_PATH}).",
    )

    config_parser = subparsers.add_parser(
        "config", help="Generate a YAML config entry for an approved saved source."
    )
    config_parser.add_argument("id", type=int, help="Saved source ID, from `list`.")
    config_parser.add_argument(
        "--output",
        help="Append the entry to this YAML file instead of printing it to stdout.",
    )
    config_parser.add_argument(
        "--db",
        default=str(library.DEFAULT_DB_PATH),
        help=f"Path to the SQLite database (default: {library.DEFAULT_DB_PATH}).",
    )

    export_parser = subparsers.add_parser(
        "export-approved", help="Regenerate a YAML file with every currently-approved source."
    )
    export_parser.add_argument(
        "--output",
        default=str(config_export.DEFAULT_EXPORT_PATH),
        help=f"Output YAML file (default: {config_export.DEFAULT_EXPORT_PATH}). Overwritten each run.",
    )
    export_parser.add_argument(
        "--db",
        default=str(library.DEFAULT_DB_PATH),
        help=f"Path to the SQLite database (default: {library.DEFAULT_DB_PATH}).",
    )

    validate_parser = subparsers.add_parser(
        "validate-config", help="Validate a generated YAML config file."
    )
    validate_parser.add_argument("path", help="Path to a YAML config file to validate.")

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
    tags = library.parse_tags(row["tags"])
    rows = [
        ("ID", str(row["id"])),
        ("Requested URL", row["requested_url"]),
        ("Final URL", row["final_url"]),
        ("Domain", row["domain"]),
        ("Title", row["title"] or "(no title found)"),
        ("Content-Type", row["content_type"] or "(not reported)"),
        ("Feed URL", row["feed_url"] or "(none detected)"),
        ("Status", row["status"]),
        ("Tags", ", ".join(tags) if tags else "(none)"),
        ("Notes", row["notes"] or "(none)"),
        ("Added", row["added_at"]),
    ]
    width = max(len(label) for label, _ in rows)
    return "\n".join(f"{label + ':':<{width + 1}} {value}" for label, value in rows)


def format_found_source(row, score: int) -> str:
    tags = library.parse_tags(row["tags"])
    return "\n".join(
        [
            f"#{row['id']} {row['title'] or '(no title)'}",
            f"   Domain: {row['domain']}",
            f"   Status: {row['status']}",
            f"   Tags:   {', '.join(tags) if tags else '(none)'}",
            f"   Notes:  {row['notes'] or '(none)'}",
            f"   Feed:   {'Yes' if row['feed_url'] else 'No'}",
            f"   Score:  {score}",
        ]
    )


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
        rows = library.list_sources(conn, status=args.status)
    finally:
        conn.close()

    if not rows:
        print(
            "No sources saved yet." if not args.status else f"No sources with status '{args.status}'."
        )
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


def _cmd_set_status(args: argparse.Namespace, status: str) -> int:
    conn = library.connect(args.db)
    try:
        row = library.set_status(conn, args.id, status)
    finally:
        conn.close()

    if row is None:
        print(f"Error: no saved source with ID {args.id}", file=sys.stderr)
        return 1

    print(f"#{row['id']} {row['title'] or '(no title)'}: status -> {row['status']}")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    return _cmd_set_status(args, library.STATUS_APPROVED)


def cmd_reject(args: argparse.Namespace) -> int:
    return _cmd_set_status(args, library.STATUS_REJECTED)


def cmd_pending(args: argparse.Namespace) -> int:
    return _cmd_set_status(args, library.STATUS_PENDING)


def cmd_note(args: argparse.Namespace) -> int:
    conn = library.connect(args.db)
    try:
        row = library.set_notes(conn, args.id, args.text)
    finally:
        conn.close()

    if row is None:
        print(f"Error: no saved source with ID {args.id}", file=sys.stderr)
        return 1

    print(f"#{row['id']} {row['title'] or '(no title)'}: notes updated")
    print(row["notes"] or "(empty)")
    return 0


def cmd_tag(args: argparse.Namespace) -> int:
    conn = library.connect(args.db)
    try:
        row = library.set_tags(conn, args.id, args.tags)
    finally:
        conn.close()

    if row is None:
        print(f"Error: no saved source with ID {args.id}", file=sys.stderr)
        return 1

    tags = library.parse_tags(row["tags"])
    print(f"#{row['id']} {row['title'] or '(no title)'}: tags -> {', '.join(tags) if tags else '(none)'}")
    return 0


def cmd_find(args: argparse.Namespace) -> int:
    conn = library.connect(args.db)
    try:
        matches = library.find_sources(conn, args.query, status=args.status)
    finally:
        conn.close()

    if not matches:
        print(f"No matches found for {args.query!r}.")
        return 0

    for row, score in matches:
        print(format_found_source(row, score))
        print()
    print(f"Matches found: {len(matches)}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    conn = library.connect(args.db)
    try:
        row = library.get_source(conn, args.id)
    finally:
        conn.close()

    if row is None:
        print(f"Error: no saved source with ID {args.id}", file=sys.stderr)
        return 1

    try:
        path = review.write_review(row, reviews_dir=args.dir, overwrite=args.overwrite)
    except review.ReviewExistsError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote review for #{row['id']} to {path}")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    conn = library.connect(args.db)
    try:
        row = library.get_source(conn, args.id)
    finally:
        conn.close()

    if row is None:
        print(f"Error: no saved source with ID {args.id}", file=sys.stderr)
        return 1

    if row["status"] != library.STATUS_APPROVED:
        print(
            f"Error: source #{row['id']} is not approved (status: {row['status']}) "
            "-- only approved sources can be exported to config.",
            file=sys.stderr,
        )
        return 1

    if args.output:
        try:
            path = config_export.append_entry(row, args.output)
        except config_export.DuplicateConfigIdError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(f"Appended source #{row['id']} to {path}")
        return 0

    print(config_export.entry_to_yaml(row), end="")
    return 0


def cmd_export_approved(args: argparse.Namespace) -> int:
    conn = library.connect(args.db)
    try:
        path, count = config_export.export_approved(conn, args.output)
    finally:
        conn.close()

    print(f"Exported {count} approved source(s) to {path}")
    return 0


def cmd_validate_config(args: argparse.Namespace) -> int:
    issues = config_export.validate_config(args.path)

    if not issues:
        print(f"OK: {args.path} is valid.")
        return 0

    print(f"Invalid: {args.path}")
    for issue in issues:
        print(f"  - {issue.message}")
    return 1


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
    "approve": cmd_approve,
    "reject": cmd_reject,
    "pending": cmd_pending,
    "note": cmd_note,
    "tag": cmd_tag,
    "find": cmd_find,
    "review": cmd_review,
    "config": cmd_config,
    "export-approved": cmd_export_approved,
    "validate-config": cmd_validate_config,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return COMMANDS[args.command](args)
