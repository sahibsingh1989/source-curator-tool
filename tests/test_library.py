"""Tests for source_curator.library. Always uses a temporary SQLite file,
never the real data/sources.db."""

import tempfile
import unittest
from pathlib import Path

from source_curator.library import (
    DEFAULT_STATUS,
    DuplicateSourceError,
    add_source,
    connect,
    find_by_domain,
    find_by_normalized_url,
    get_source,
    list_sources,
    normalize_url,
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


if __name__ == "__main__":
    unittest.main()
