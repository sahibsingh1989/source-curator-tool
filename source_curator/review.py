"""Generate a Markdown review file for one saved source.

Every statement in the generated file comes directly from data already stored
in the library (the automatically-collected inspection metadata, or the
curator's own notes/tags) -- nothing about trustworthiness, authority, or
accuracy is invented here.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import library

DEFAULT_REVIEWS_DIR = Path("reviews")


class ReviewExistsError(Exception):
    """Raised when a review file already exists and --overwrite wasn't given."""

    def __init__(self, path: Path):
        self.path = path
        super().__init__(f"Review file already exists: {path} (use --overwrite to replace it)")


def slugify(text: str) -> str:
    """Lower-case, alphanumeric-and-hyphen only. Falls back to "source" if
    `text` has nothing usable in it, so a filename is always produced."""
    text = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower())
    return text.strip("-") or "source"


def review_filename(row) -> str:
    """`<id>-<slugified domain>.md` -- deterministic and collision-resistant
    (the ID prefix alone guarantees uniqueness even if two domains slugify
    the same way)."""
    return f"{row['id']}-{slugify(row['domain'])}.md"


def build_usefulness_notes(row) -> list[str]:
    """Deterministic, metadata-only observations -- not a trust/authority claim."""
    tags = library.parse_tags(row["tags"])
    notes: list[str] = []
    if tags:
        notes.append(f"Tagged by a curator with: {', '.join(tags)}.")
    if row["feed_url"]:
        notes.append("Offers an RSS/Atom feed, which makes it easy to track new content over time.")
    if row["notes"]:
        notes.append("A curator has recorded notes about this source (see Curator Notes below).")
    if row["content_type"] and "html" in row["content_type"].lower():
        notes.append("Returned machine-readable HTML the last time it was inspected.")
    if not notes:
        notes.append("No additional stored metadata is available to assess usefulness.")
    return notes


def render_review(row) -> str:
    tags = library.parse_tags(row["tags"])
    lines = [
        f"# {row['title'] or row['domain']}",
        "",
        f"- **URL:** {row['final_url']}",
        f"- **Domain:** {row['domain']}",
        f"- **Approval status:** {row['status']}",
        f"- **Tags:** {', '.join(tags) if tags else '_none_'}",
        f"- **Content type:** {row['content_type'] or '_not reported_'}",
        f"- **RSS/Atom feed:** {'Yes' if row['feed_url'] else 'No'}",
    ]
    if row["feed_url"]:
        lines.append(f"- **Feed URL:** {row['feed_url']}")
    lines.append(f"- **Date added:** {row['added_at']}")
    lines.append("")
    lines.append("## Curator notes")
    lines.append("")
    lines.append("_Written by a curator -- not automatically generated._")
    lines.append("")
    lines.append(row["notes"] if row["notes"] else "_No curator notes recorded._")
    lines.append("")
    lines.append("## Automatically collected metadata")
    lines.append("")
    lines.append(
        "_Collected automatically when this source was inspected/saved -- not a curator judgment._"
    )
    lines.append("")
    lines.append(f"- Title: {row['title'] or '_(no title found)_'}")
    lines.append(f"- Domain: {row['domain']}")
    lines.append(f"- Content type: {row['content_type'] or '_(not reported)_'}")
    lines.append(f"- Feed detected: {'Yes' if row['feed_url'] else 'No'}")
    if row["feed_url"]:
        lines.append(f"- Feed URL: {row['feed_url']}")
    lines.append(f"- Added: {row['added_at']}")
    lines.append("")
    lines.append("## Why this source may be useful")
    lines.append("")
    lines.append(
        "_Based only on the stored metadata and curator notes above -- this is not a "
        "trustworthiness, accuracy, or authority assessment._"
    )
    lines.append("")
    for note in build_usefulness_notes(row):
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def write_review(
    row, *, reviews_dir: Path | str = DEFAULT_REVIEWS_DIR, overwrite: bool = False
) -> Path:
    """Render and write the review file. Raises ReviewExistsError if the file
    is already there and `overwrite` is False."""
    reviews_dir = Path(reviews_dir)
    reviews_dir.mkdir(parents=True, exist_ok=True)
    path = reviews_dir / review_filename(row)
    if path.exists() and not overwrite:
        raise ReviewExistsError(path)
    path.write_text(render_review(row), encoding="utf-8")
    return path
