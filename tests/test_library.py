"""Tests for source_curator.library. Always uses a temporary SQLite file,
never the real data/sources.db."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from source_curator.library import (
    DEFAULT_STATUS,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    DuplicateSourceError,
    add_source,
    connect,
    find_by_domain,
    find_by_normalized_url,
    find_sources,
    get_source,
    list_sources,
    normalize_tags,
    normalize_url,
    parse_tags,
    serialize_tags,
    set_notes,
    set_status,
    set_tags,
)


class TempDbTestCase(unittest.TestCase):
    """Base class that gives each test its own throwaway SQLite file."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "sources.db"
        self.conn = connect(self.db_path)

    def tearDown(self):
        self.conn.close()
        self._tmpdir.cleanup()

    def add(self, final_url, requested_url=None, domain=None, **extra):
        from urllib.parse import urlparse

        return add_source(
            self.conn,
            requested_url=requested_url or final_url,
            final_url=final_url,
            domain=domain or urlparse(final_url).hostname,
            title=extra.pop("title", "A Title"),
            content_type=extra.pop("content_type", "text/html"),
            feed_url=extra.pop("feed_url", None),
            **extra,
        )


class NormalizeUrlTests(unittest.TestCase):
    def test_trailing_slash_on_root_is_equivalent(self):
        self.assertEqual(normalize_url("https://example.com"), normalize_url("https://example.com/"))

    def test_trailing_slash_on_path_is_equivalent(self):
        self.assertEqual(
            normalize_url("https://example.com/foo/"), normalize_url("https://example.com/foo")
        )

    def test_case_insensitive_scheme_and_host(self):
        self.assertEqual(
            normalize_url("HTTPS://Example.COM/Path"), normalize_url("https://example.com/Path")
        )

    def test_default_port_is_dropped(self):
        self.assertEqual(
            normalize_url("https://example.com:443/path"), normalize_url("https://example.com/path")
        )
        self.assertEqual(
            normalize_url("http://example.com:80/path"), normalize_url("http://example.com/path")
        )

    def test_non_default_port_is_preserved(self):
        self.assertNotEqual(
            normalize_url("https://example.com:8443/path"), normalize_url("https://example.com/path")
        )

    def test_fragment_is_dropped(self):
        self.assertEqual(
            normalize_url("https://example.com/path#section"), normalize_url("https://example.com/path")
        )

    def test_distinct_paths_remain_distinct(self):
        self.assertNotEqual(
            normalize_url("https://example.com/one"), normalize_url("https://example.com/two")
        )

    def test_is_deterministic(self):
        url = "https://Example.com:443/Foo/?x=1#y"
        self.assertEqual(normalize_url(url), normalize_url(url))


class ConnectTests(unittest.TestCase):
    def test_creates_parent_directory_and_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "nested" / "deeper" / "sources.db"
            self.assertFalse(db_path.parent.exists())

            conn = connect(db_path)
            try:
                self.assertTrue(db_path.exists())
                table = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='sources'"
                ).fetchone()
                self.assertIsNotNone(table)
            finally:
                conn.close()

    def test_reopening_existing_db_does_not_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "sources.db"
            connect(db_path).close()
            conn = connect(db_path)
            conn.close()


class AddSourceTests(TempDbTestCase):
    def test_saves_all_fields(self):
        saved, same_domain = self.add(
            "https://blog.example.com/post-1",
            requested_url="https://blog.example.com/post-1/",
            title="Post One",
            content_type="text/html; charset=UTF-8",
            feed_url="https://blog.example.com/feed.xml",
        )

        self.assertEqual(same_domain, [])
        self.assertEqual(saved["requested_url"], "https://blog.example.com/post-1/")
        self.assertEqual(saved["final_url"], "https://blog.example.com/post-1")
        self.assertEqual(saved["domain"], "blog.example.com")
        self.assertEqual(saved["title"], "Post One")
        self.assertEqual(saved["content_type"], "text/html; charset=UTF-8")
        self.assertEqual(saved["feed_url"], "https://blog.example.com/feed.xml")
        self.assertEqual(saved["status"], DEFAULT_STATUS)
        self.assertTrue(saved["added_at"])
        self.assertIsInstance(saved["id"], int)

    def test_normalized_url_is_stored(self):
        saved, _ = self.add("https://example.com/path/")
        self.assertEqual(saved["normalized_url"], normalize_url("https://example.com/path/"))


class DuplicateBlockingTests(TempDbTestCase):
    def test_exact_duplicate_is_blocked(self):
        self.add("https://example.com/page")
        with self.assertRaises(DuplicateSourceError):
            self.add("https://example.com/page")

    def test_trailing_slash_variant_is_blocked(self):
        self.add("https://example.com/page")
        with self.assertRaises(DuplicateSourceError):
            self.add("https://example.com/page/")

    def test_case_and_port_variant_is_blocked(self):
        self.add("https://example.com/page")
        with self.assertRaises(DuplicateSourceError):
            self.add("HTTPS://EXAMPLE.com:443/page")

    def test_duplicate_error_references_existing_row(self):
        saved, _ = self.add("https://example.com/page")
        try:
            self.add("https://example.com/page/")
            self.fail("expected DuplicateSourceError")
        except DuplicateSourceError as exc:
            self.assertEqual(exc.existing["id"], saved["id"])

    def test_duplicate_does_not_add_a_second_row(self):
        self.add("https://example.com/page")
        with self.assertRaises(DuplicateSourceError):
            self.add("https://example.com/page/")
        self.assertEqual(len(list_sources(self.conn)), 1)

    def test_different_paths_on_same_url_are_not_duplicates(self):
        self.add("https://example.com/page-one")
        saved, _ = self.add("https://example.com/page-two")
        self.assertIsNotNone(saved)
        self.assertEqual(len(list_sources(self.conn)), 2)


class SameDomainWarningTests(TempDbTestCase):
    def test_second_source_on_same_domain_is_allowed_and_flagged(self):
        first, _ = self.add("https://example.com/page-one")
        second, same_domain = self.add("https://example.com/page-two")

        self.assertEqual(len(same_domain), 1)
        self.assertEqual(same_domain[0]["id"], first["id"])
        self.assertNotEqual(second["id"], first["id"])
        self.assertEqual(len(list_sources(self.conn)), 2)

    def test_first_source_on_a_domain_has_no_warning(self):
        _, same_domain = self.add("https://example.com/only-page")
        self.assertEqual(same_domain, [])

    def test_different_domain_does_not_warn(self):
        self.add("https://example.com/page")
        _, same_domain = self.add("https://other.example.org/page")
        self.assertEqual(same_domain, [])


class ListAndGetTests(TempDbTestCase):
    def test_list_sources_empty(self):
        self.assertEqual(list_sources(self.conn), [])

    def test_list_sources_ordered_by_id(self):
        first, _ = self.add("https://example.com/a")
        second, _ = self.add("https://example.com/b")

        rows = list_sources(self.conn)

        self.assertEqual([row["id"] for row in rows], [first["id"], second["id"]])

    def test_get_source_found(self):
        saved, _ = self.add("https://example.com/a")
        row = get_source(self.conn, saved["id"])
        self.assertEqual(row["final_url"], "https://example.com/a")

    def test_get_source_missing_returns_none(self):
        self.assertIsNone(get_source(self.conn, 999))

    def test_find_by_domain(self):
        self.add("https://example.com/a")
        self.add("https://example.com/b")
        rows = find_by_domain(self.conn, "example.com")
        self.assertEqual(len(rows), 2)

    def test_find_by_normalized_url_missing_returns_none(self):
        self.assertIsNone(find_by_normalized_url(self.conn, normalize_url("https://nope.example.com/")))

    def test_list_sources_filters_by_status(self):
        first, _ = self.add("https://example.com/a")
        second, _ = self.add("https://example.com/b")
        set_status(self.conn, second["id"], STATUS_APPROVED)

        pending_rows = list_sources(self.conn, status=STATUS_PENDING)
        approved_rows = list_sources(self.conn, status=STATUS_APPROVED)

        self.assertEqual([row["id"] for row in pending_rows], [first["id"]])
        self.assertEqual([row["id"] for row in approved_rows], [second["id"]])


class StatusChangeTests(TempDbTestCase):
    def test_approve_changes_status(self):
        saved, _ = self.add("https://example.com/a")
        updated = set_status(self.conn, saved["id"], STATUS_APPROVED)
        self.assertEqual(updated["status"], STATUS_APPROVED)

    def test_reject_changes_status(self):
        saved, _ = self.add("https://example.com/a")
        updated = set_status(self.conn, saved["id"], STATUS_REJECTED)
        self.assertEqual(updated["status"], STATUS_REJECTED)

    def test_reset_to_pending(self):
        saved, _ = self.add("https://example.com/a")
        set_status(self.conn, saved["id"], STATUS_APPROVED)
        updated = set_status(self.conn, saved["id"], STATUS_PENDING)
        self.assertEqual(updated["status"], STATUS_PENDING)

    def test_default_status_is_pending(self):
        saved, _ = self.add("https://example.com/a")
        self.assertEqual(saved["status"], STATUS_PENDING)

    def test_nonexistent_id_returns_none_and_changes_nothing(self):
        self.assertIsNone(set_status(self.conn, 999, STATUS_APPROVED))
        self.assertEqual(list_sources(self.conn), [])


class NotesTests(TempDbTestCase):
    def test_set_notes_stores_text(self):
        saved, _ = self.add("https://example.com/a")
        updated = set_notes(self.conn, saved["id"], "Useful for stablecoin regulation")
        self.assertEqual(updated["notes"], "Useful for stablecoin regulation")

    def test_set_notes_replaces_previous_text(self):
        saved, _ = self.add("https://example.com/a")
        set_notes(self.conn, saved["id"], "First note")
        updated = set_notes(self.conn, saved["id"], "Second note")
        self.assertEqual(updated["notes"], "Second note")

    def test_new_source_has_no_notes(self):
        saved, _ = self.add("https://example.com/a")
        self.assertIsNone(saved["notes"])

    def test_nonexistent_id_returns_none(self):
        self.assertIsNone(set_notes(self.conn, 999, "text"))


class NormalizeTagsTests(unittest.TestCase):
    def test_lowercases_tags(self):
        self.assertEqual(normalize_tags(["Stablecoins", "REGULATION"]), ["regulation", "stablecoins"])

    def test_removes_duplicates(self):
        self.assertEqual(normalize_tags(["tag", "tag", "TAG"]), ["tag"])

    def test_strips_whitespace(self):
        self.assertEqual(normalize_tags([" tag ", "tag"]), ["tag"])

    def test_drops_empty_tags(self):
        self.assertEqual(normalize_tags(["", "  ", "tag"]), ["tag"])

    def test_sorted_output_is_deterministic(self):
        self.assertEqual(normalize_tags(["zebra", "apple", "mango"]), ["apple", "mango", "zebra"])

    def test_empty_input(self):
        self.assertEqual(normalize_tags([]), [])


class TagSerializationTests(unittest.TestCase):
    def test_round_trips(self):
        tags = ["regulation", "stablecoins", "tokenization"]
        self.assertEqual(parse_tags(serialize_tags(tags)), tags)

    def test_parse_tags_none(self):
        self.assertEqual(parse_tags(None), [])

    def test_parse_tags_empty_string(self):
        self.assertEqual(parse_tags(""), [])


class SetTagsTests(TempDbTestCase):
    def test_sets_normalized_tags(self):
        saved, _ = self.add("https://example.com/a")
        updated = set_tags(self.conn, saved["id"], ["Stablecoins", "Regulation", "stablecoins"])
        self.assertEqual(parse_tags(updated["tags"]), ["regulation", "stablecoins"])

    def test_running_tag_again_replaces_previous_set(self):
        saved, _ = self.add("https://example.com/a")
        set_tags(self.conn, saved["id"], ["old", "tags"])
        updated = set_tags(self.conn, saved["id"], ["new"])
        self.assertEqual(parse_tags(updated["tags"]), ["new"])

    def test_new_source_has_no_tags(self):
        saved, _ = self.add("https://example.com/a")
        self.assertEqual(parse_tags(saved["tags"]), [])

    def test_nonexistent_id_returns_none(self):
        self.assertIsNone(set_tags(self.conn, 999, ["tag"]))


class FindSourcesTests(TempDbTestCase):
    def test_search_by_title(self):
        saved, _ = self.add("https://example.com/a", title="Stablecoin Regulation News")
        self.add("https://other.example.com/b", title="Cooking Recipes")

        matches = find_sources(self.conn, "stablecoin")

        self.assertEqual([row["id"] for row, _ in matches], [saved["id"]])

    def test_search_by_domain(self):
        saved, _ = self.add("https://regulatorynews.example.com/a", title="Untitled")
        self.add("https://other.example.com/b", title="Untitled")

        matches = find_sources(self.conn, "regulatorynews")

        self.assertEqual([row["id"] for row, _ in matches], [saved["id"]])

    def test_search_by_url(self):
        saved, _ = self.add("https://example.com/stablecoin-guide", title="Guide")

        matches = find_sources(self.conn, "stablecoin-guide")

        self.assertEqual([row["id"] for row, _ in matches], [saved["id"]])

    def test_search_by_notes(self):
        saved, _ = self.add("https://example.com/a", title="Untitled")
        set_notes(self.conn, saved["id"], "Useful for issuer disclosures")

        matches = find_sources(self.conn, "disclosures")

        self.assertEqual([row["id"] for row, _ in matches], [saved["id"]])

    def test_search_by_tags(self):
        saved, _ = self.add("https://example.com/a", title="Untitled")
        set_tags(self.conn, saved["id"], ["stablecoins", "tokenization"])

        matches = find_sources(self.conn, "tokenization")

        self.assertEqual([row["id"] for row, _ in matches], [saved["id"]])

    def test_case_insensitive_search(self):
        saved, _ = self.add("https://example.com/a", title="Stablecoin Regulation")

        matches = find_sources(self.conn, "STABLECOIN")

        self.assertEqual([row["id"] for row, _ in matches], [saved["id"]])

    def test_partial_text_match(self):
        saved, _ = self.add("https://example.com/a", title="Stablecoins Explained")

        matches = find_sources(self.conn, "stable")

        self.assertEqual([row["id"] for row, _ in matches], [saved["id"]])

    def test_multiple_matching_words_score_higher(self):
        both, _ = self.add(
            "https://example.com/a", title="Stablecoin Regulation Guide", domain="example.com"
        )
        one, _ = self.add(
            "https://other.example.org/b", title="Stablecoin News", domain="other.example.org"
        )

        matches = find_sources(self.conn, "stablecoin regulation")
        scores = {row["id"]: score for row, score in matches}

        self.assertGreater(scores[both["id"]], scores[one["id"]])

    def test_results_sorted_best_match_first(self):
        weak, _ = self.add("https://weak.example.com/a", title="Stablecoin")
        strong, _ = self.add("https://strong.example.com/b", title="Stablecoin Regulation Guide")

        matches = find_sources(self.conn, "stablecoin regulation guide")

        self.assertEqual([row["id"] for row, _ in matches][0], strong["id"])

    def test_sort_is_deterministic_for_tied_scores(self):
        first, _ = self.add("https://a.example.com/", title="Stablecoin")
        second, _ = self.add("https://b.example.com/", title="Stablecoin")

        matches = find_sources(self.conn, "stablecoin")

        self.assertEqual([row["id"] for row, _ in matches], [first["id"], second["id"]])

    def test_no_matches_returns_empty_list(self):
        self.add("https://example.com/a", title="Cooking Recipes")
        self.assertEqual(find_sources(self.conn, "stablecoin"), [])

    def test_empty_query_returns_empty_list(self):
        self.add("https://example.com/a", title="Stablecoin")
        self.assertEqual(find_sources(self.conn, ""), [])
        self.assertEqual(find_sources(self.conn, "   "), [])

    def test_status_filter_restricts_search(self):
        saved, _ = self.add("https://example.com/a", title="Stablecoin Regulation")
        set_status(self.conn, saved["id"], STATUS_APPROVED)
        self.add("https://other.example.com/b", title="Stablecoin Regulation")

        approved_matches = find_sources(self.conn, "stablecoin", status=STATUS_APPROVED)

        self.assertEqual([row["id"] for row, _ in approved_matches], [saved["id"]])


class MigrationTests(unittest.TestCase):
    """A database created by the Phase 3 version of this tool (no notes/tags
    columns) must keep working and must not lose any existing data."""

    def test_old_schema_database_is_migrated_without_losing_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "sources.db"

            # Build a Phase-3-shaped database by hand: no notes/tags columns.
            raw = sqlite3.connect(db_path)
            raw.execute(
                """
                CREATE TABLE sources (
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
                )
                """
            )
            raw.execute(
                """
                INSERT INTO sources
                    (requested_url, final_url, normalized_url, domain, title,
                     content_type, feed_url, status, added_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "https://example.com/",
                    "https://example.com/",
                    normalize_url("https://example.com/"),
                    "example.com",
                    "Pre-existing Source",
                    "text/html",
                    None,
                    "approved",
                    "2026-01-01T00:00:00+00:00",
                ),
            )
            raw.commit()
            raw.close()

            # Now open it through the current connect(), which must migrate
            # it in place.
            conn = connect(db_path)
            try:
                rows = list_sources(conn)
                self.assertEqual(len(rows), 1)
                row = rows[0]
                self.assertEqual(row["title"], "Pre-existing Source")
                self.assertEqual(row["status"], "approved")
                self.assertIsNone(row["notes"])
                self.assertEqual(parse_tags(row["tags"]), [])

                # The migrated database must support the new functionality.
                updated = set_notes(conn, row["id"], "Added after migration")
                self.assertEqual(updated["notes"], "Added after migration")
                tagged = set_tags(conn, row["id"], ["migrated"])
                self.assertEqual(parse_tags(tagged["tags"]), ["migrated"])
            finally:
                conn.close()

    def test_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "sources.db"
            connect(db_path).close()
            # Reopening (and thus re-running the migration check) on an
            # already-current database must not fail or duplicate columns.
            conn = connect(db_path)
            conn.close()


if __name__ == "__main__":
    unittest.main()
