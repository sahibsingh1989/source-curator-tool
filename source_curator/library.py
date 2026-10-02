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
    added_at TEXT NOT NULL
);
"""


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


def list_sources(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM sources ORDER BY id").fetchall()


def get_source(conn: sqlite3.Connection, source_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
