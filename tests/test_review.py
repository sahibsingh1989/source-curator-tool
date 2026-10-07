"""Tests for source_curator.review. Writes to a temp directory, never a real
reviews/ folder."""

import tempfile
import unittest
from pathlib import Path

from source_curator.library import connect, add_source, set_notes, set_tags
from source_curator.review import (
    ReviewExistsError,
    build_usefulness_notes,
    render_review,
    review_filename,
    slugify,
    write_review,
)


class SlugifyTests(unittest.TestCase):
    def test_lowercases_and_hyphenates(self):
        self.assertEqual(slugify("Example.com"), "example-com")

    def test_collapses_runs_of_punctuation(self):
        self.assertEqual(slugify("www.example---co.uk"), "www-example-co-uk")

    def test_strips_leading_trailing_hyphens(self):
        self.assertEqual(slugify("-example-"), "example")

    def test_empty_falls_back_to_source(self):
        self.assertEqual(slugify(""), "source")
        self.assertEqual(slugify(None), "source")


class ReviewTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "sources.db"
        self.reviews_dir = Path(self._tmpdir.name) / "reviews"
        self.conn = connect(self.db_path)

    def tearDown(self):
        self.conn.close()
        self._tmpdir.cleanup()

    def add(self, **overrides):
        defaults = dict(
            requested_url="https://blog.example.com/",
            final_url="https://blog.example.com/",
            domain="blog.example.com",
            title="Example Blog",
            content_type="text/html; charset=utf-8",
            feed_url=None,
        )
        defaults.update(overrides)
        saved, _ = add_source(self.conn, **defaults)
        return saved


class ReviewFilenameTests(ReviewTestCase):
    def test_filename_includes_id_and_domain_slug(self):
        row = self.add()
        self.assertEqual(review_filename(row), f"{row['id']}-blog-example-com.md")


class BuildUsefulnessNotesTests(ReviewTestCase):
    def test_no_metadata_gives_honest_fallback(self):
        row = self.add(feed_url=None, content_type=None)
        notes = build_usefulness_notes(row)
        self.assertEqual(notes, ["No additional stored metadata is available to assess usefulness."])

    def test_tags_are_mentioned(self):
        row = self.add()
        set_tags(self.conn, row["id"], ["stablecoins", "regulation"])
        row = self.conn.execute("SELECT * FROM sources WHERE id = ?", (row["id"],)).fetchone()
        notes = build_usefulness_notes(row)
        self.assertTrue(any("regulation, stablecoins" in n for n in notes))

    def test_feed_is_mentioned(self):
        row = self.add(feed_url="https://blog.example.com/feed.xml")
        notes = build_usefulness_notes(row)
        self.assertTrue(any("feed" in n.lower() for n in notes))

    def test_notes_presence_is_mentioned_without_leaking_content(self):
        row = self.add()
        set_notes(self.conn, row["id"], "Secret curator opinion")
        row = self.conn.execute("SELECT * FROM sources WHERE id = ?", (row["id"],)).fetchone()
        notes = build_usefulness_notes(row)
        joined = " ".join(notes)
        self.assertIn("curator", joined.lower())
        self.assertNotIn("Secret curator opinion", joined)

    def test_never_mentions_trust_or_authority(self):
        row = self.add(feed_url="https://blog.example.com/feed.xml")
        set_tags(self.conn, row["id"], ["news"])
        row = self.conn.execute("SELECT * FROM sources WHERE id = ?", (row["id"],)).fetchone()
        notes = " ".join(build_usefulness_notes(row)).lower()
        for banned in ("trust", "authoritative", "reliable", "credible"):
            self.assertNotIn(banned, notes)


class RenderReviewTests(ReviewTestCase):
    def test_includes_all_required_fields(self):
        row = self.add(feed_url="https://blog.example.com/feed.xml")
        set_tags(self.conn, row["id"], ["stablecoins"])
        set_notes(self.conn, row["id"], "Good primary source for issuer disclosures.")
        row = self.conn.execute("SELECT * FROM sources WHERE id = ?", (row["id"],)).fetchone()

        text = render_review(row)

        self.assertIn("Example Blog", text)
        self.assertIn("https://blog.example.com/", text)
        self.assertIn("blog.example.com", text)
        self.assertIn("pending", text)
        self.assertIn("stablecoins", text)
        self.assertIn("Good primary source for issuer disclosures.", text)
        self.assertIn("text/html", text)
        self.assertIn("https://blog.example.com/feed.xml", text)
        self.assertIn(row["added_at"], text)

    def test_distinguishes_curator_notes_from_automatic_metadata(self):
        row = self.add()
        set_notes(self.conn, row["id"], "My own opinion of this source.")
        row = self.conn.execute("SELECT * FROM sources WHERE id = ?", (row["id"],)).fetchone()

        text = render_review(row)

        self.assertIn("## Curator notes", text)
        self.assertIn("## Automatically collected metadata", text)
        curator_section_index = text.index("## Curator notes")
        auto_section_index = text.index("## Automatically collected metadata")
        self.assertLess(curator_section_index, auto_section_index)
        self.assertIn("not automatically generated", text)
        self.assertIn("not a curator judgment", text)

    def test_never_claims_trustworthiness(self):
        row = self.add()
        text = render_review(row).lower()
        self.assertIn("not a trustworthiness", text)
        for banned in ("is trustworthy", "is authoritative", "is reliable", "is credible"):
            self.assertNotIn(banned, text)

    def test_missing_notes_shown_honestly(self):
        row = self.add()
        text = render_review(row)
        self.assertIn("_No curator notes recorded._", text)


class WriteReviewTests(ReviewTestCase):
    def test_writes_file_and_returns_path(self):
        row = self.add()
        path = write_review(row, reviews_dir=self.reviews_dir)
        self.assertTrue(path.exists())
        self.assertEqual(path.read_text(encoding="utf-8"), render_review(row))

    def test_creates_reviews_directory(self):
        row = self.add()
        self.assertFalse(self.reviews_dir.exists())
        write_review(row, reviews_dir=self.reviews_dir)
        self.assertTrue(self.reviews_dir.exists())

    def test_overwrite_protection_blocks_second_write(self):
        row = self.add()
        write_review(row, reviews_dir=self.reviews_dir)
        with self.assertRaises(ReviewExistsError):
            write_review(row, reviews_dir=self.reviews_dir)

    def test_overwrite_flag_allows_replacing(self):
        row = self.add()
        write_review(row, reviews_dir=self.reviews_dir)
        set_notes(self.conn, row["id"], "Updated note")
        row = self.conn.execute("SELECT * FROM sources WHERE id = ?", (row["id"],)).fetchone()

        path = write_review(row, reviews_dir=self.reviews_dir, overwrite=True)

        self.assertIn("Updated note", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
