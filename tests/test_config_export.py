"""Tests for source_curator.config_export. Writes to temp directories/files,
never a real config/ folder."""

import tempfile
import unittest
from pathlib import Path

import yaml

from source_curator.library import add_source, connect, set_status, set_tags
from source_curator.library import STATUS_APPROVED, STATUS_PENDING, STATUS_REJECTED
from source_curator.config_export import (
    DuplicateConfigIdError,
    append_entry,
    build_entry,
    entry_to_yaml,
    export_approved,
    is_valid_url,
    load_entries,
    validate_config,
)


class ConfigTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "sources.db"
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


class IsValidUrlTests(unittest.TestCase):
    def test_valid_http_and_https(self):
        self.assertTrue(is_valid_url("http://example.com"))
        self.assertTrue(is_valid_url("https://example.com/path"))

    def test_rejects_other_schemes(self):
        self.assertFalse(is_valid_url("ftp://example.com"))

    def test_rejects_missing_host(self):
        self.assertFalse(is_valid_url("https://"))

    def test_rejects_non_string(self):
        self.assertFalse(is_valid_url(None))
        self.assertFalse(is_valid_url(123))

    def test_rejects_empty_string(self):
        self.assertFalse(is_valid_url(""))


class BuildEntryTests(ConfigTestCase):
    def test_basic_fields(self):
        row = self.add()
        entry = build_entry(row)
        self.assertEqual(entry["id"], row["id"])
        self.assertEqual(entry["name"], "Example Blog")
        self.assertEqual(entry["url"], "https://blog.example.com/")
        self.assertEqual(entry["domain"], "blog.example.com")
        self.assertEqual(entry["topics"], [])
        self.assertEqual(entry["approved"], True)

    def test_type_is_feed_when_feed_url_present(self):
        row = self.add(feed_url="https://blog.example.com/feed.xml")
        entry = build_entry(row)
        self.assertEqual(entry["type"], "feed")
        self.assertEqual(entry["feed_url"], "https://blog.example.com/feed.xml")

    def test_type_is_page_without_feed(self):
        row = self.add(feed_url=None)
        entry = build_entry(row)
        self.assertEqual(entry["type"], "page")
        self.assertNotIn("feed_url", entry)

    def test_tags_become_topics(self):
        row = self.add()
        set_tags(self.conn, row["id"], ["Stablecoins", "Regulation"])
        row = self.conn.execute("SELECT * FROM sources WHERE id = ?", (row["id"],)).fetchone()
        entry = build_entry(row)
        self.assertEqual(entry["topics"], ["regulation", "stablecoins"])

    def test_name_falls_back_to_domain_when_no_title(self):
        row = self.add(title=None)
        entry = build_entry(row)
        self.assertEqual(entry["name"], "blog.example.com")


class EntryToYamlTests(ConfigTestCase):
    def test_produces_valid_yaml_list(self):
        row = self.add()
        text = entry_to_yaml(row)
        data = yaml.safe_load(text)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], row["id"])

    def test_field_order_is_deterministic(self):
        row = self.add(feed_url="https://blog.example.com/feed.xml")
        text = entry_to_yaml(row)
        order = [line.split(":")[0].strip("- ") for line in text.splitlines() if ":" in line]
        self.assertEqual(
            order, ["id", "name", "url", "domain", "type", "topics", "approved", "feed_url"]
        )


class AppendEntryTests(ConfigTestCase):
    def test_creates_file_and_parent_directory(self):
        row = self.add()
        output = Path(self._tmpdir.name) / "nested" / "config.yaml"
        self.assertFalse(output.parent.exists())

        path = append_entry(row, output)

        self.assertTrue(path.exists())
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], row["id"])

    def test_appends_to_existing_file_without_disturbing_other_entries(self):
        first = self.add()
        second = self.add(final_url="https://other.example.com/", domain="other.example.com")
        output = Path(self._tmpdir.name) / "config.yaml"

        append_entry(first, output)
        append_entry(second, output)

        entries = load_entries(output)
        self.assertEqual({e["id"] for e in entries}, {first["id"], second["id"]})

    def test_duplicate_id_is_rejected(self):
        row = self.add()
        output = Path(self._tmpdir.name) / "config.yaml"
        append_entry(row, output)

        with self.assertRaises(DuplicateConfigIdError):
            append_entry(row, output)

        # the file must be unchanged -- still exactly one entry
        self.assertEqual(len(load_entries(output)), 1)

    def test_output_is_always_valid_yaml(self):
        first = self.add()
        second = self.add(final_url="https://other.example.com/", domain="other.example.com")
        output = Path(self._tmpdir.name) / "config.yaml"

        append_entry(first, output)
        append_entry(second, output)

        text = output.read_text(encoding="utf-8")
        data = yaml.safe_load(text)

        # Not just "parses without raising" -- the re-serialized file must
        # actually be a two-entry list matching what was appended, with the
        # expected keys and values on each entry.
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 2)
        self.assertEqual({entry["id"] for entry in data}, {first["id"], second["id"]})
        for entry in data:
            self.assertEqual(
                set(entry.keys()), {"id", "name", "url", "domain", "type", "topics", "approved"}
            )
            self.assertIs(entry["approved"], True)


class LoadEntriesTests(unittest.TestCase):
    def test_missing_file_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_entries(Path(tmp) / "nope.yaml"), [])

    def test_empty_file_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.yaml"
            path.write_text("", encoding="utf-8")
            self.assertEqual(load_entries(path), [])

    def test_non_list_yaml_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.yaml"
            path.write_text("just: a mapping\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_entries(path)


class ExportApprovedTests(ConfigTestCase):
    def test_exports_only_approved_sources(self):
        a = self.add(final_url="https://a.example.com/", domain="a.example.com")
        b = self.add(final_url="https://b.example.com/", domain="b.example.com")
        c = self.add(final_url="https://c.example.com/", domain="c.example.com")
        set_status(self.conn, a["id"], STATUS_APPROVED)
        set_status(self.conn, b["id"], STATUS_REJECTED)
        # c stays pending

        output = Path(self._tmpdir.name) / "approved.yaml"
        path, count = export_approved(self.conn, output)

        entries = load_entries(path)
        self.assertEqual(count, 1)
        self.assertEqual([e["id"] for e in entries], [a["id"]])

    def test_sorted_by_id(self):
        c = self.add(final_url="https://c.example.com/", domain="c.example.com")
        a = self.add(final_url="https://a.example.com/", domain="a.example.com")
        for row in (c, a):
            set_status(self.conn, row["id"], STATUS_APPROVED)

        output = Path(self._tmpdir.name) / "approved.yaml"
        path, _ = export_approved(self.conn, output)

        entries = load_entries(path)
        self.assertEqual([e["id"] for e in entries], sorted(e["id"] for e in entries))
        self.assertEqual([e["id"] for e in entries], [c["id"], a["id"]])  # c has lower id

    def test_regenerates_file_from_scratch_each_run(self):
        row = self.add()
        set_status(self.conn, row["id"], STATUS_APPROVED)
        output = Path(self._tmpdir.name) / "approved.yaml"

        export_approved(self.conn, output)
        set_status(self.conn, row["id"], STATUS_REJECTED)
        path, count = export_approved(self.conn, output)

        self.assertEqual(count, 0)
        self.assertEqual(load_entries(path), [])

    def test_no_approved_sources_writes_empty_list(self):
        self.add()  # stays pending
        output = Path(self._tmpdir.name) / "approved.yaml"
        path, count = export_approved(self.conn, output)
        self.assertEqual(count, 0)
        self.assertEqual(load_entries(path), [])

    def test_creates_parent_directory(self):
        row = self.add()
        set_status(self.conn, row["id"], STATUS_APPROVED)
        output = Path(self._tmpdir.name) / "nested" / "dir" / "approved.yaml"
        self.assertFalse(output.parent.exists())
        export_approved(self.conn, output)
        self.assertTrue(output.exists())


class ValidateConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmpdir.cleanup()

    def write(self, text: str) -> Path:
        path = Path(self._tmpdir.name) / "config.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_valid_config_has_no_issues(self):
        path = self.write(
            """
- id: 1
  name: Example Blog
  url: https://blog.example.com/
  domain: blog.example.com
  type: page
  topics: []
  approved: true
"""
        )
        self.assertEqual(validate_config(path), [])

    def test_missing_file_reports_one_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            issues = validate_config(Path(tmp) / "missing.yaml")
        self.assertEqual(len(issues), 1)

    def test_malformed_yaml_reported_cleanly(self):
        path = self.write("id: [1, 2\nname: broken")
        issues = validate_config(path)
        self.assertEqual(len(issues), 1)
        self.assertIn("Malformed YAML", issues[0].message)

    def test_non_list_top_level_is_invalid(self):
        path = self.write("just: a mapping\n")
        issues = validate_config(path)
        self.assertEqual(len(issues), 1)
        self.assertIn("list", issues[0].message)

    def test_empty_file_is_valid_with_zero_entries(self):
        path = self.write("")
        self.assertEqual(validate_config(path), [])

    def test_missing_required_field_detected(self):
        path = self.write(
            """
- id: 1
  name: Example
  domain: example.com
  type: page
  topics: []
  approved: true
"""
        )
        issues = validate_config(path)
        self.assertTrue(any("url" in issue.message for issue in issues))

    def test_duplicate_ids_detected(self):
        path = self.write(
            """
- id: 1
  name: A
  url: https://a.example.com/
  domain: a.example.com
  type: page
  topics: []
  approved: true
- id: 1
  name: B
  url: https://b.example.com/
  domain: b.example.com
  type: page
  topics: []
  approved: true
"""
        )
        issues = validate_config(path)
        self.assertTrue(any("duplicate" in issue.message.lower() for issue in issues))

    def test_malformed_url_detected(self):
        path = self.write(
            """
- id: 1
  name: A
  url: "not a url"
  domain: a.example.com
  type: page
  topics: []
  approved: true
"""
        )
        issues = validate_config(path)
        self.assertTrue(any("malformed url" in issue.message.lower() for issue in issues))

    def test_invalid_approved_value_detected(self):
        path = self.write(
            """
- id: 1
  name: A
  url: https://a.example.com/
  domain: a.example.com
  type: page
  topics: []
  approved: "yes"
"""
        )
        issues = validate_config(path)
        self.assertTrue(any("approved" in issue.message.lower() for issue in issues))

    def test_approved_false_is_invalid_for_this_schema(self):
        path = self.write(
            """
- id: 1
  name: A
  url: https://a.example.com/
  domain: a.example.com
  type: page
  topics: []
  approved: false
"""
        )
        issues = validate_config(path)
        self.assertTrue(any("approved" in issue.message.lower() for issue in issues))

    def test_entry_not_a_mapping_detected(self):
        path = self.write("- just a string\n- 42\n")
        issues = validate_config(path)
        self.assertEqual(len(issues), 2)

    def test_multiple_issues_all_reported(self):
        path = self.write(
            """
- id: 1
  name: A
  domain: a.example.com
  type: page
  topics: []
  approved: true
- id: 1
  name: B
  url: "not a url"
  domain: b.example.com
  type: page
  topics: []
  approved: true
"""
        )
        issues = validate_config(path)
        messages = " ".join(i.message for i in issues)
        self.assertIn("url", messages)
        self.assertIn("duplicate", messages.lower())


if __name__ == "__main__":
    unittest.main()
