"""Build, write, and validate YAML config entries for approved sources.

The on-disk format is always a YAML list of mappings (even a single `config
<id>` without --output prints a one-item list), so a file written by `config
--output` and one written by `export-approved` are always compatible and can
always be concatenated/appended without reshaping.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from . import library

DEFAULT_EXPORT_PATH = Path("config") / "approved_sources.yaml"

REQUIRED_FIELDS = ("id", "name", "url", "domain", "type", "topics", "approved")


class DuplicateConfigIdError(Exception):
    """Raised by append_entry when the target file already has this source ID."""

    def __init__(self, source_id: int, path: Path):
        self.source_id = source_id
        self.path = path
        super().__init__(f"source #{source_id} is already present in {path}")


def build_entry(row) -> dict:
    """The structured, deterministic config entry for one approved source."""
    entry = {
        "id": row["id"],
        "name": row["title"] or row["domain"],
        "url": row["final_url"],
        "domain": row["domain"],
        "type": "feed" if row["feed_url"] else "page",
        "topics": library.parse_tags(row["tags"]),
        "approved": True,
    }
    if row["feed_url"]:
        entry["feed_url"] = row["feed_url"]
    return entry


def entries_to_yaml(entries: list[dict]) -> str:
    return yaml.safe_dump(entries, sort_keys=False, default_flow_style=False)


def entry_to_yaml(row) -> str:
    """A single source, as a one-item YAML list (see module docstring)."""
    return entries_to_yaml([build_entry(row)])


def load_entries(path: Path | str) -> list[dict]:
    """Read an existing config file's entries, or [] if it doesn't exist yet
    or is empty. Raises ValueError if its content isn't a YAML list."""
    path = Path(path)
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return []
    data = yaml.safe_load(text)
    if data is None:
        return []
    if not isinstance(data, list):
        raise ValueError(f"{path} does not contain a YAML list of source entries")
    return data


def append_entry(row, output_path: Path | str) -> Path:
    """Append one approved source's entry to `output_path`, creating the file
    and its parent directory if needed. The whole file is re-serialized from
    the combined entry list, so the result is always valid YAML. Raises
    DuplicateConfigIdError if this source's ID is already in the file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    entries = load_entries(output_path)
    if any(entry.get("id") == row["id"] for entry in entries if isinstance(entry, dict)):
        raise DuplicateConfigIdError(row["id"], output_path)

    entries.append(build_entry(row))
    output_path.write_text(entries_to_yaml(entries), encoding="utf-8")
    return output_path


def export_approved(
    conn, output_path: Path | str = DEFAULT_EXPORT_PATH
) -> tuple[Path, int]:
    """Regenerate `output_path` from scratch with every currently-approved
    source, sorted by ID. Overwrites whatever was there before."""
    rows = library.list_sources(conn, status=library.STATUS_APPROVED)
    entries = [build_entry(row) for row in rows]

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(entries_to_yaml(entries), encoding="utf-8")
    return output_path, len(entries)


# --- Validation ------------------------------------------------------------


def is_valid_url(value) -> bool:
    if not isinstance(value, str) or not value:
        return False
    parts = urlsplit(value)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


@dataclass
class ValidationIssue:
    entry_index: int | None  # None for a file-level problem (bad YAML, wrong shape)
    message: str


def validate_config(path: Path | str) -> list[ValidationIssue]:
    """Parse `path` as YAML and check it against the config schema. Returns
    an empty list if it's valid; otherwise one ValidationIssue per problem
    found (parsing never raises -- a parse failure is itself an issue)."""
    path = Path(path)

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [ValidationIssue(None, f"Could not read {path}: {exc}")]

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [ValidationIssue(None, f"Malformed YAML: {exc}")]

    if data is None:
        data = []
    if not isinstance(data, list):
        return [ValidationIssue(None, "Config must be a YAML list of source entries.")]

    issues: list[ValidationIssue] = []
    seen_ids: dict = {}

    for index, entry in enumerate(data):
        if not isinstance(entry, dict):
            issues.append(ValidationIssue(index, f"entry {index}: not a mapping/object"))
            continue

        entry_id = entry.get("id")
        label = f"entry {index} (id={entry_id!r})"

        for field in REQUIRED_FIELDS:
            if field not in entry:
                issues.append(ValidationIssue(index, f"{label}: missing required field '{field}'"))

        if entry_id is not None:
            if entry_id in seen_ids:
                issues.append(
                    ValidationIssue(
                        index, f"{label}: duplicate id (first seen at entry {seen_ids[entry_id]})"
                    )
                )
            else:
                seen_ids[entry_id] = index

        if "url" in entry and not is_valid_url(entry.get("url")):
            issues.append(ValidationIssue(index, f"{label}: malformed URL {entry.get('url')!r}"))

        if "approved" in entry and entry.get("approved") is not True:
            issues.append(
                ValidationIssue(
                    index, f"{label}: invalid 'approved' value {entry.get('approved')!r} (must be true)"
                )
            )

    return issues
