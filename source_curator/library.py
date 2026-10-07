"""A local SQLite library of saved, inspected sources.

Plain sqlite3 (stdlib), no ORM. Deduplicates on a normalized version of the
final URL so trivial variations (trailing slash, default port, case, fragment)
don't create duplicate rows.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

DEFAULT_DB_PATH = Path("data") / "sources.db"
DEFAULT_STATUS = "pending"

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

# New databases get every column straight away via this CREATE TABLE.
# Databases created by an older version of this tool (missing notes/tags from
# before Phase 6, or the last_* health columns from before Phase 8) are
# brought up to date by the migration in connect() below -- ALTER TABLE ADD
# COLUMN never touches existing rows' other data.
SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requested_url TEXT NOT NULL,
    final_url TEXT NOT NULL,
    normalized_url TEXT NOT NULL UNIQUE,
    domain TEXT NOT NULL,
    title TEXT,
    content_type TEXT,
    feed_url TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    added_at TEXT NOT NULL,
    notes TEXT,
    tags TEXT,
    last_checked_at TEXT,
    last_http_status INTEGER,
    last_reachable INTEGER,
    last_feed_reachable INTEGER,
    last_freshness TEXT,
    last_health_status TEXT,
    last_error TEXT
);
"""

# column_name -> the ALTER TABLE statement that adds it, for migrating a
# database created before that column existed.
_MIGRATIONS = {
    "notes": "ALTER TABLE sources ADD COLUMN notes TEXT",
    "tags": "ALTER TABLE sources ADD COLUMN tags TEXT",
    "last_checked_at": "ALTER TABLE sources ADD COLUMN last_checked_at TEXT",
    "last_http_status": "ALTER TABLE sources ADD COLUMN last_http_status INTEGER",
    "last_reachable": "ALTER TABLE sources ADD COLUMN last_reachable INTEGER",
    "last_feed_reachable": "ALTER TABLE sources ADD COLUMN last_feed_reachable INTEGER",
    "last_freshness": "ALTER TABLE sources ADD COLUMN last_freshness TEXT",
    "last_health_status": "ALTER TABLE sources ADD COLUMN last_health_status TEXT",
    "last_error": "ALTER TABLE sources ADD COLUMN last_error TEXT",
}


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Add any columns introduced after a database was first created.

    Safe to run every time: only adds missing columns, never drops or
    rewrites existing ones, so existing rows and their data are untouched.
    """
    existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sources)")}
    for column, ddl in _MIGRATIONS.items():
        if column not in existing_columns:
            conn.execute(ddl)
    conn.commit()


class DuplicateSourceError(Exception):
    """Raised by add_source when the normalized URL is already saved."""

    def __init__(self, existing: sqlite3.Row):
        self.existing = existing
        super().__init__(f"already saved as #{existing['id']} ({existing['final_url']})")


def normalize_url(url: str) -> str:
    """Collapse trivial duplicate variations of a URL into one canonical string.

    Lower-cases scheme/host, drops a default port (80/443), drops the fragment,
    and strips a trailing slash from any path longer than "/". Deliberately
    simple: only enough to catch the obvious, deterministic cases.
    """
    parts = urlsplit((url or "").strip())
    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower()
    default_port = {"http": 80, "https": 443}.get(scheme)
    netloc = hostname
    if parts.port and parts.port != default_port:
        netloc = f"{hostname}:{parts.port}"
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open the sources database, creating its directory and schema if needed."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    conn.commit()
    _migrate_schema(conn)
    return conn


def find_by_normalized_url(conn: sqlite3.Connection, normalized_url: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM sources WHERE normalized_url = ?", (normalized_url,)
    ).fetchone()


def find_by_domain(conn: sqlite3.Connection, domain: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM sources WHERE domain = ? ORDER BY id", (domain,)
    ).fetchall()


def add_source(
    conn: sqlite3.Connection,
    *,
    requested_url: str,
    final_url: str,
    domain: str,
    title: str | None,
    content_type: str | None,
    feed_url: str | None,
    status: str = DEFAULT_STATUS,
) -> tuple[sqlite3.Row, list[sqlite3.Row]]:
    """Save a source keyed on normalize_url(final_url).

    Raises DuplicateSourceError if that normalized URL is already saved.
    Returns (saved_row, other_rows_already_saved_on_the_same_domain) so the
    caller can warn about (but still allow) a second source on one domain.
    """
    normalized = normalize_url(final_url)

    existing = find_by_normalized_url(conn, normalized)
    if existing is not None:
        raise DuplicateSourceError(existing)

    same_domain = find_by_domain(conn, domain)

    added_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cursor = conn.execute(
        """
        INSERT INTO sources
            (requested_url, final_url, normalized_url, domain, title,
             content_type, feed_url, status, added_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            requested_url,
            final_url,
            normalized,
            domain,
            title,
            content_type,
            feed_url,
            status,
            added_at,
        ),
    )
    conn.commit()
    saved = get_source(conn, cursor.lastrowid)
    return saved, same_domain


def list_sources(
    conn: sqlite3.Connection, status: str | None = None, health: str | None = None
) -> list[sqlite3.Row]:
    clauses = []
    params: list[str] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if health:
        clauses.append("last_health_status = ?")
        params.append(health)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return conn.execute(f"SELECT * FROM sources{where} ORDER BY id", params).fetchall()


def get_source(conn: sqlite3.Connection, source_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()


def set_status(conn: sqlite3.Connection, source_id: int, status: str) -> sqlite3.Row | None:
    """Update a saved source's approval status. Returns the updated row, or
    None if `source_id` doesn't exist (nothing is changed in that case)."""
    if get_source(conn, source_id) is None:
        return None
    conn.execute("UPDATE sources SET status = ? WHERE id = ?", (status, source_id))
    conn.commit()
    return get_source(conn, source_id)


def set_notes(conn: sqlite3.Connection, source_id: int, notes: str) -> sqlite3.Row | None:
    """Replace a saved source's curator notes. Returns the updated row, or
    None if `source_id` doesn't exist."""
    if get_source(conn, source_id) is None:
        return None
    conn.execute("UPDATE sources SET notes = ? WHERE id = ?", (notes, source_id))
    conn.commit()
    return get_source(conn, source_id)


def normalize_tags(tags: list[str]) -> list[str]:
    """Lower-case, strip, drop empties, de-duplicate, and sort a list of tags."""
    seen: set[str] = set()
    normalized = []
    for raw in tags:
        tag = (raw or "").strip().lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        normalized.append(tag)
    return sorted(normalized)


def serialize_tags(tags: list[str]) -> str:
    return ",".join(tags)


def parse_tags(value: str | None) -> list[str]:
    if not value:
        return []
    return [tag for tag in value.split(",") if tag]


def set_tags(conn: sqlite3.Connection, source_id: int, tags: list[str]) -> sqlite3.Row | None:
    """Replace a saved source's tag set (normalized) with `tags`. Returns the
    updated row, or None if `source_id` doesn't exist."""
    if get_source(conn, source_id) is None:
        return None
    normalized = normalize_tags(tags)
    conn.execute(
        "UPDATE sources SET tags = ? WHERE id = ?", (serialize_tags(normalized), source_id)
    )
    conn.commit()
    return get_source(conn, source_id)


def find_sources(
    conn: sqlite3.Connection, query: str, status: str | None = None
) -> list[tuple[sqlite3.Row, int]]:
    """Search saved sources locally (no network) across title, domain, final
    URL, notes, and tags.

    Case-insensitive, partial-text matching: each lower-cased query word that
    appears as a substring of any searched field scores one point for that
    field, so more matching words -- and words matching in more fields --
    rank higher. Returns (row, score) pairs for score > 0, sorted best match
    first, ties broken by ascending ID for a deterministic order.
    """
    terms = [term for term in (query or "").lower().split() if term]
    if not terms:
        return []

    rows = list_sources(conn, status=status)

    scored = []
    for row in rows:
        fields = (
            (row["title"] or "").lower(),
            (row["domain"] or "").lower(),
            (row["final_url"] or "").lower(),
            (row["notes"] or "").lower(),
            (row["tags"] or "").lower(),
        )
        score = sum(1 for term in terms for field in fields if term in field)
        if score > 0:
            scored.append((row, score))

    scored.sort(key=lambda pair: (-pair[1], pair[0]["id"]))
    return scored


def record_health_check(
    conn: sqlite3.Connection,
    source_id: int,
    *,
    checked_at: str,
    http_status: int | None,
    reachable: bool,
    feed_reachable: bool | None,
    freshness: str,
    health_status: str,
    error: str | None,
) -> sqlite3.Row | None:
    """Persist the result of a `check`/`check-all` health check. Returns the
    updated row, or None if `source_id` doesn't exist (nothing is changed)."""
    if get_source(conn, source_id) is None:
        return None
    conn.execute(
        """
        UPDATE sources SET
            last_checked_at = ?,
            last_http_status = ?,
            last_reachable = ?,
            last_feed_reachable = ?,
            last_freshness = ?,
            last_health_status = ?,
            last_error = ?
        WHERE id = ?
        """,
        (checked_at, http_status, reachable, feed_reachable, freshness, health_status, error, source_id),
    )
    conn.commit()
    return get_source(conn, source_id)
