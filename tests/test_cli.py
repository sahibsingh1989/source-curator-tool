"""Tests for the CLI layer. The inspector is patched, so no network access.
Library-backed commands (add/list/show) use a temporary SQLite file, never the
real data/sources.db."""

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import yaml

from source_curator.cli import format_inspection, main
from source_curator.feeds import FeedInfo, FeedItem
from source_curator.inspector import Inspection, InspectionError
from source_curator.library import connect, get_source

SAMPLE = Inspection(
    requested_url="https://example.com",
    final_url="https://example.com/",
    status_code=200,
    domain="example.com",
    content_type="text/html; charset=UTF-8",
    title="Example Domain",
    feed=FeedInfo(found=False),
)

SAMPLE_WITH_FEED = Inspection(
    requested_url="https://blog.example.com",
    final_url="https://blog.example.com/",
    status_code=200,
    domain="blog.example.com",
    content_type="text/html; charset=UTF-8",
    title="Example Blog",
    feed=FeedInfo(
        found=True,
        feed_url="https://blog.example.com/feed.xml",
        title="Example Blog Feed",
        items=[
            FeedItem(title="First Post", link="https://blog.example.com/first", published="2026-09-30"),
            FeedItem(title="Second Post", link="https://blog.example.com/second", published="2026-09-29"),
        ],
    ),
)


class FormatTests(unittest.TestCase):
    def test_includes_every_field(self):
        text = format_inspection(SAMPLE)
        self.assertIn("Status:", text)
        self.assertIn("200", text)
        self.assertIn("https://example.com/", text)
        self.assertIn("example.com", text)
        self.assertIn("Example Domain", text)
        self.assertIn("text/html; charset=UTF-8", text)

    def test_placeholders_for_missing_values(self):
        text = format_inspection(
            Inspection(
                requested_url="https://example.com",
                final_url="https://example.com/",
                status_code=204,
                domain="example.com",
                content_type=None,
                title=None,
                feed=FeedInfo(found=False),
            )
        )
        self.assertIn("(no title found)", text)
        self.assertIn("(not reported)", text)

    def test_no_feed_reported_clearly(self):
        text = format_inspection(SAMPLE)
        self.assertIn("Feed found:  No", text)
        self.assertNotIn("Feed URL", text)

    def test_feed_with_items_reported(self):
        text = format_inspection(SAMPLE_WITH_FEED)
        self.assertIn("Feed found:  Yes", text)
        self.assertIn("https://blog.example.com/feed.xml", text)
        self.assertIn("Example Blog Feed", text)
        self.assertIn("First Post", text)
        self.assertIn("https://blog.example.com/first", text)
        self.assertIn("2026-09-30", text)
        self.assertIn("Second Post", text)

    def test_feed_error_reported_without_items(self):
        text = format_inspection(
            Inspection(
                requested_url="https://blog.example.com",
                final_url="https://blog.example.com/",
                status_code=200,
                domain="blog.example.com",
                content_type="text/html",
                title="Example Blog",
                feed=FeedInfo(
                    found=True,
                    feed_url="https://blog.example.com/feed.xml",
                    error="Could not connect to feed URL",
                ),
            )
        )
        self.assertIn("Feed found:  Yes", text)
        self.assertIn("Feed error:  Could not connect to feed URL", text)
        self.assertNotIn("Items", text)


class MainTests(unittest.TestCase):
    @patch("source_curator.cli.inspect")
    def test_success_prints_report_and_returns_zero(self, mock_inspect):
        mock_inspect.return_value = SAMPLE
        out = io.StringIO()

        with redirect_stdout(out):
            code = main(["inspect", "https://example.com"])

        self.assertEqual(code, 0)
        self.assertIn("Example Domain", out.getvalue())
        mock_inspect.assert_called_once_with("https://example.com", timeout=10.0)

    @patch("source_curator.cli.inspect")
    def test_timeout_flag_is_passed_through(self, mock_inspect):
        mock_inspect.return_value = SAMPLE

        with redirect_stdout(io.StringIO()):
            main(["inspect", "https://example.com", "--timeout", "2.5"])

        mock_inspect.assert_called_once_with("https://example.com", timeout=2.5)

    @patch("source_curator.cli.inspect")
    def test_failure_writes_stderr_and_returns_one(self, mock_inspect):
        mock_inspect.side_effect = InspectionError("Could not connect to https://bad.invalid")
        err = io.StringIO()

        with redirect_stderr(err):
            code = main(["inspect", "https://bad.invalid"])

        self.assertEqual(code, 1)
        self.assertIn("Could not connect", err.getvalue())

    @patch("source_curator.cli.inspect")
    def test_feed_items_printed_to_stdout(self, mock_inspect):
        mock_inspect.return_value = SAMPLE_WITH_FEED
        out = io.StringIO()

        with redirect_stdout(out):
            code = main(["inspect", "https://blog.example.com"])

        self.assertEqual(code, 0)
        self.assertIn("First Post", out.getvalue())

    def test_missing_command_exits_with_usage_error(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                main([])
        self.assertEqual(ctx.exception.code, 2)


class LibraryCommandTests(unittest.TestCase):
    """Tests for add/list/show. Each test gets its own temp DB file."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "sources.db"

    def tearDown(self):
        self._tmpdir.cleanup()

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv + ["--db", str(self.db_path)])
        return code, out.getvalue(), err.getvalue()

    @patch("source_curator.cli.inspect")
    def test_add_saves_and_prints_summary(self, mock_inspect):
        mock_inspect.return_value = SAMPLE

        code, out, _ = self.run_main(["add", "https://example.com"])

        self.assertEqual(code, 0)
        self.assertIn("Saved source #1", out)
        self.assertIn("example.com", out)

    @patch("source_curator.cli.inspect")
    def test_add_stores_feed_url_when_detected(self, mock_inspect):
        mock_inspect.return_value = SAMPLE_WITH_FEED

        code, _, _ = self.run_main(["add", "https://blog.example.com"])

        self.assertEqual(code, 0)
        conn = connect(self.db_path)
        try:
            row = get_source(conn, 1)
        finally:
            conn.close()
        self.assertEqual(row["feed_url"], "https://blog.example.com/feed.xml")

    @patch("source_curator.cli.inspect")
    def test_add_propagates_inspection_errors(self, mock_inspect):
        mock_inspect.side_effect = InspectionError("Could not connect to https://bad.invalid")

        code, _, err = self.run_main(["add", "https://bad.invalid"])

        self.assertEqual(code, 1)
        self.assertIn("Could not connect", err)

    @patch("source_curator.cli.inspect")
    def test_add_duplicate_is_blocked_and_not_double_saved(self, mock_inspect):
        mock_inspect.return_value = SAMPLE

        first_code, _, _ = self.run_main(["add", "https://example.com"])
        second_code, _, second_err = self.run_main(["add", "https://example.com/"])

        self.assertEqual(first_code, 0)
        self.assertEqual(second_code, 1)
        self.assertIn("already saved", second_err)

        conn = connect(self.db_path)
        try:
            from source_curator.library import list_sources

            self.assertEqual(len(list_sources(conn)), 1)
        finally:
            conn.close()

    @patch("source_curator.cli.inspect")
    def test_add_same_domain_is_allowed_with_warning(self, mock_inspect):
        second = replace(
            SAMPLE,
            requested_url="https://example.com/other",
            final_url="https://example.com/other",
            title="Another Page",
        )
        mock_inspect.side_effect = [SAMPLE, second]

        first_code, first_out, _ = self.run_main(["add", "https://example.com"])
        second_code, second_out, _ = self.run_main(["add", "https://example.com/other"])

        self.assertEqual(first_code, 0)
        self.assertEqual(second_code, 0)
        self.assertNotIn("Note:", first_out)
        self.assertIn("Note:", second_out)
        self.assertIn("example.com", second_out)

    def test_list_with_no_sources(self):
        code, out, _ = self.run_main(["list"])
        self.assertEqual(code, 0)
        self.assertIn("No sources saved yet.", out)

    @patch("source_curator.cli.inspect")
    def test_list_shows_saved_sources(self, mock_inspect):
        mock_inspect.return_value = SAMPLE
        self.run_main(["add", "https://example.com"])

        code, out, _ = self.run_main(["list"])

        self.assertEqual(code, 0)
        self.assertIn("#1", out)
        self.assertIn("Example Domain", out)
        self.assertIn("example.com", out)
        self.assertIn("pending", out)
        self.assertIn("Feed:", out)

    @patch("source_curator.cli.inspect")
    def test_show_found(self, mock_inspect):
        mock_inspect.return_value = SAMPLE_WITH_FEED
        self.run_main(["add", "https://blog.example.com"])

        code, out, _ = self.run_main(["show", "1"])

        self.assertEqual(code, 0)
        self.assertIn("Requested URL", out)
        self.assertIn("https://blog.example.com/", out)
        self.assertIn("Example Blog", out)
        self.assertIn("https://blog.example.com/feed.xml", out)
        self.assertIn("pending", out)

    def test_show_missing_id_reports_clean_error(self):
        code, _, err = self.run_main(["show", "999"])

        self.assertEqual(code, 1)
        self.assertIn("no saved source with ID 999", err)


class DiscoverCommandTests(unittest.TestCase):
    """Tests for `discover`. search() is always mocked; already-saved marking
    uses a temp DB so it never touches the real data/sources.db."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "sources.db"

    def tearDown(self):
        self._tmpdir.cleanup()

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv + ["--db", str(self.db_path)])
        return code, out.getvalue(), err.getvalue()

    @patch("source_curator.cli.discovery.search")
    def test_prints_candidates_and_summary(self, mock_search):
        from source_curator.discovery import SearchResult

        mock_search.return_value = [
            SearchResult("Reg News", "https://reg.example.com/a", "Snippet one", "reg.example.com"),
            SearchResult("Reg News Dup", "https://reg.example.com/a/", "dup", "reg.example.com"),
            SearchResult("Other Site", "https://other.example.org/x", "Snippet two", "other.example.org"),
        ]

        code, out, _ = self.run_main(["discover", "stablecoin regulation"])

        self.assertEqual(code, 0)
        self.assertIn("1. Reg News", out)
        self.assertIn("reg.example.com", out)
        self.assertIn("https://reg.example.com/a", out)
        self.assertIn("Snippet one", out)
        self.assertIn("Already saved: No", out)
        self.assertIn("2. Other Site", out)
        self.assertIn("Raw results found:     3", out)
        self.assertIn("Duplicates removed:    1", out)
        self.assertIn("Unique source domains: 2", out)
        mock_search.assert_called_once_with("stablecoin regulation", limit=20)

    @patch("source_curator.cli.discovery.search")
    def test_limit_flag_is_passed_through(self, mock_search):
        mock_search.return_value = []

        self.run_main(["discover", "topic", "--limit", "5"])

        mock_search.assert_called_once_with("topic", limit=5)

    @patch("source_curator.cli.discovery.search")
    def test_zero_results_reported_cleanly(self, mock_search):
        mock_search.return_value = []

        code, out, _ = self.run_main(["discover", "an obscure topic"])

        self.assertEqual(code, 0)
        self.assertIn("No results found", out)

    @patch("source_curator.cli.discovery.search")
    def test_search_error_reported_without_crashing(self, mock_search):
        from source_curator.discovery import SearchError

        mock_search.side_effect = SearchError("Web search failed: rate limited")

        code, _, err = self.run_main(["discover", "topic"])

        self.assertEqual(code, 1)
        self.assertIn("rate limited", err)

    @patch("source_curator.cli.discovery.search")
    def test_empty_query_handled_cleanly(self, mock_search):
        from source_curator.discovery import SearchError

        mock_search.side_effect = SearchError("No search topic given.")

        code, _, err = self.run_main(["discover", ""])

        self.assertEqual(code, 1)
        self.assertIn("No search topic given", err)

    @patch("source_curator.cli.inspect")
    @patch("source_curator.cli.discovery.search")
    def test_already_saved_domain_is_marked(self, mock_search, mock_inspect):
        from source_curator.discovery import SearchResult

        mock_inspect.return_value = SAMPLE  # domain: example.com
        self.run_main(["add", "https://example.com"])

        mock_search.return_value = [
            SearchResult("Example", "https://example.com/other-page", "s", "example.com"),
            SearchResult("Fresh", "https://fresh.example.org/", "s", "fresh.example.org"),
        ]

        code, out, _ = self.run_main(["discover", "topic"])

        self.assertEqual(code, 0)
        lines = out.splitlines()
        example_block = "\n".join(lines[lines.index("1. Example") : lines.index("1. Example") + 5])
        fresh_block = "\n".join(lines[lines.index("2. Fresh") : lines.index("2. Fresh") + 5])
        self.assertIn("Already saved: Yes", example_block)
        self.assertIn("Already saved: No", fresh_block)


class EvaluateCommandTests(unittest.TestCase):
    """Tests for `evaluate`. evaluation.run_evaluation is mocked, so neither
    search nor HTTP calls happen, and --db always points at a temp file."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "sources.db"

    def tearDown(self):
        self._tmpdir.cleanup()

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv + ["--db", str(self.db_path)])
        return code, out.getvalue(), err.getvalue()

    @patch("source_curator.cli.evaluation.run_evaluation")
    def test_prints_ranked_candidates_and_summary(self, mock_run):
        from source_curator.discovery import DiscoveryStats
        from source_curator.evaluation import EvaluatedCandidate

        mock_run.return_value = (
            [
                EvaluatedCandidate(
                    rank=1,
                    title="Great Source",
                    domain="great.example.com",
                    url="https://great.example.com/",
                    score=95,
                    relevance_label="High",
                    reachable=True,
                    status_code=200,
                    feed_found=True,
                    feed_url="https://great.example.com/feed",
                    freshness="Recent",
                    already_saved=False,
                    explanation="High query relevance; reachable; RSS feed detected; recent feed activity.",
                ),
                EvaluatedCandidate(
                    rank=2,
                    title="Weak Source",
                    domain="weak.example.com",
                    url="https://weak.example.com/",
                    score=10,
                    relevance_label="None",
                    reachable=False,
                    status_code=None,
                    feed_found=False,
                    feed_url=None,
                    freshness="Unknown",
                    already_saved=True,
                    explanation="No query relevance detected; unreachable; no feed detected.",
                ),
            ],
            DiscoveryStats(raw_count=2, duplicate_count=0, unique_domain_count=2),
        )

        code, out, _ = self.run_main(["evaluate", "stablecoin regulation"])

        self.assertEqual(code, 0)
        self.assertIn("1. Great Source", out)
        self.assertIn("Curation score: 95/100", out)
        self.assertIn("High query relevance; reachable; RSS feed detected; recent feed activity.", out)
        self.assertIn("2. Weak Source", out)
        self.assertIn("Already saved:  Yes", out)
        self.assertIn("Raw results found:     2", out)
        self.assertIn("Unique source domains: 2", out)
        mock_run.assert_called_once_with(
            "stablecoin regulation", limit=10, saved_domains=set(), timeout=10.0
        )

    @patch("source_curator.cli.evaluation.run_evaluation")
    def test_default_limit_is_ten(self, mock_run):
        from source_curator.discovery import DiscoveryStats

        mock_run.return_value = ([], DiscoveryStats(0, 0, 0))

        self.run_main(["evaluate", "topic"])

        _, kwargs = mock_run.call_args
        self.assertEqual(kwargs["limit"], 10)

    @patch("source_curator.cli.evaluation.run_evaluation")
    def test_limit_flag_is_passed_through(self, mock_run):
        from source_curator.discovery import DiscoveryStats

        mock_run.return_value = ([], DiscoveryStats(0, 0, 0))

        self.run_main(["evaluate", "topic", "--limit", "5"])

        _, kwargs = mock_run.call_args
        self.assertEqual(kwargs["limit"], 5)

    @patch("source_curator.cli.evaluation.run_evaluation")
    def test_zero_results_reported_cleanly(self, mock_run):
        from source_curator.discovery import DiscoveryStats

        mock_run.return_value = ([], DiscoveryStats(0, 0, 0))

        code, out, _ = self.run_main(["evaluate", "an obscure topic"])

        self.assertEqual(code, 0)
        self.assertIn("No results found", out)

    @patch("source_curator.cli.evaluation.run_evaluation")
    def test_search_error_reported_without_crashing(self, mock_run):
        from source_curator.discovery import SearchError

        mock_run.side_effect = SearchError("Web search failed: rate limited")

        code, _, err = self.run_main(["evaluate", "topic"])

        self.assertEqual(code, 1)
        self.assertIn("rate limited", err)

    @patch("source_curator.cli.inspect")
    @patch("source_curator.cli.evaluation.run_evaluation")
    def test_already_saved_domains_passed_from_library(self, mock_run, mock_inspect):
        from source_curator.discovery import DiscoveryStats

        mock_inspect.return_value = SAMPLE  # domain: example.com
        self.run_main(["add", "https://example.com"])

        mock_run.return_value = ([], DiscoveryStats(0, 0, 0))
        self.run_main(["evaluate", "topic"])

        _, kwargs = mock_run.call_args
        self.assertEqual(kwargs["saved_domains"], {"example.com"})


class CurationCommandTests(unittest.TestCase):
    """Tests for approve/reject/pending/note/tag/find and `list --status`.
    Each test gets its own temp DB file."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "sources.db"

    def tearDown(self):
        self._tmpdir.cleanup()

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv + ["--db", str(self.db_path)])
        return code, out.getvalue(), err.getvalue()

    def seed_one_source(self):
        with patch("source_curator.cli.inspect", return_value=SAMPLE):
            self.run_main(["add", "https://example.com"])

    def test_approve_sets_status_and_prints_confirmation(self):
        self.seed_one_source()

        code, out, _ = self.run_main(["approve", "1"])

        self.assertEqual(code, 0)
        self.assertIn("status -> approved", out)
        self.assertIn("Example Domain", out)

    def test_reject_sets_status(self):
        self.seed_one_source()

        code, out, _ = self.run_main(["reject", "1"])

        self.assertEqual(code, 0)
        self.assertIn("status -> rejected", out)

    def test_pending_resets_status(self):
        self.seed_one_source()
        self.run_main(["approve", "1"])

        code, out, _ = self.run_main(["pending", "1"])

        self.assertEqual(code, 0)
        self.assertIn("status -> pending", out)

    def test_approve_missing_id_reports_clean_error(self):
        code, _, err = self.run_main(["approve", "999"])

        self.assertEqual(code, 1)
        self.assertIn("no saved source with ID 999", err)

    def test_reject_missing_id_reports_clean_error(self):
        code, _, err = self.run_main(["reject", "999"])

        self.assertEqual(code, 1)
        self.assertIn("no saved source with ID 999", err)

    def test_pending_missing_id_reports_clean_error(self):
        code, _, err = self.run_main(["pending", "999"])

        self.assertEqual(code, 1)
        self.assertIn("no saved source with ID 999", err)

    def test_note_sets_notes_and_prints_confirmation(self):
        self.seed_one_source()

        code, out, _ = self.run_main(["note", "1", "Useful for stablecoin regulation"])

        self.assertEqual(code, 0)
        self.assertIn("notes updated", out)
        self.assertIn("Useful for stablecoin regulation", out)

        code, out, _ = self.run_main(["show", "1"])
        self.assertIn("Useful for stablecoin regulation", out)

    def test_note_missing_id_reports_clean_error(self):
        code, _, err = self.run_main(["note", "999", "text"])

        self.assertEqual(code, 1)
        self.assertIn("no saved source with ID 999", err)

    def test_tag_sets_lowercased_deduped_tags(self):
        self.seed_one_source()

        code, out, _ = self.run_main(["tag", "1", "Stablecoins", "Regulation", "stablecoins"])

        self.assertEqual(code, 0)
        self.assertIn("regulation, stablecoins", out)

        code, out, _ = self.run_main(["show", "1"])
        self.assertIn("regulation, stablecoins", out)

    def test_running_tag_again_replaces_tags(self):
        self.seed_one_source()
        self.run_main(["tag", "1", "old", "tags"])

        code, out, _ = self.run_main(["tag", "1", "new"])

        self.assertEqual(code, 0)
        self.assertIn("tags -> new", out)
        self.assertNotIn("old", out)

    def test_tag_missing_id_reports_clean_error(self):
        code, _, err = self.run_main(["tag", "999", "tag"])

        self.assertEqual(code, 1)
        self.assertIn("no saved source with ID 999", err)

    def test_find_returns_match_with_score(self):
        self.seed_one_source()
        self.run_main(["tag", "1", "stablecoins"])
        self.run_main(["note", "1", "Covers stablecoin regulation"])

        code, out, _ = self.run_main(["find", "stablecoin"])

        self.assertEqual(code, 0)
        self.assertIn("#1 Example Domain", out)
        self.assertIn("Score:", out)
        self.assertIn("Matches found: 1", out)

    def test_find_no_matches(self):
        self.seed_one_source()

        code, out, _ = self.run_main(["find", "nonexistentwordzzz"])

        self.assertEqual(code, 0)
        self.assertIn("No matches found", out)

    def test_find_never_touches_the_network(self):
        # find is local-only: there is nothing to mock/patch for network or
        # search calls, and this must still work correctly.
        self.seed_one_source()

        code, out, _ = self.run_main(["find", "example"])

        self.assertEqual(code, 0)
        self.assertIn("#1 Example Domain", out)

    def test_find_status_filter(self):
        self.seed_one_source()  # #1, pending
        with patch(
            "source_curator.cli.inspect",
            return_value=replace(
                SAMPLE,
                requested_url="https://other.example.com",
                final_url="https://other.example.com/",
                domain="other.example.com",
                title="Other Example",
            ),
        ):
            self.run_main(["add", "https://other.example.com"])  # #2, pending
        self.run_main(["approve", "2"])

        code, out, _ = self.run_main(["find", "example", "--status", "approved"])

        self.assertEqual(code, 0)
        self.assertIn("Other Example", out)
        self.assertNotIn("#1 Example Domain", out)

    def test_list_status_filter(self):
        self.seed_one_source()  # #1, pending
        self.run_main(["approve", "1"])

        code, out, _ = self.run_main(["list", "--status", "approved"])
        self.assertIn("#1", out)

        code, out, _ = self.run_main(["list", "--status", "pending"])
        self.assertIn("No sources with status 'pending'.", out)

    def test_list_status_filter_invalid_choice_rejected_by_argparse(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                main(["list", "--status", "bogus", "--db", str(self.db_path)])
        self.assertEqual(ctx.exception.code, 2)


class ReviewConfigCommandTests(unittest.TestCase):
    """Tests for review/config/export-approved/validate-config. Each test gets
    its own temp DB file plus temp reviews/config directories."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "sources.db"
        self.reviews_dir = Path(self._tmpdir.name) / "reviews"
        self.config_path = Path(self._tmpdir.name) / "config" / "generated_sources.yaml"
        self.export_path = Path(self._tmpdir.name) / "config" / "approved_sources.yaml"

    def tearDown(self):
        self._tmpdir.cleanup()

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed_one_source(self):
        with patch("source_curator.cli.inspect", return_value=SAMPLE):
            self.run_main(["add", "https://example.com", "--db", str(self.db_path)])

    # -- review --------------------------------------------------------

    def test_review_generates_markdown_file(self):
        self.seed_one_source()

        code, out, _ = self.run_main(
            ["review", "1", "--db", str(self.db_path), "--dir", str(self.reviews_dir)]
        )

        self.assertEqual(code, 0)
        self.assertIn("Wrote review for #1", out)
        files = list(self.reviews_dir.glob("*.md"))
        self.assertEqual(len(files), 1)
        self.assertIn("Example Domain", files[0].read_text(encoding="utf-8"))

    def test_review_missing_id_reports_clean_error(self):
        code, _, err = self.run_main(
            ["review", "999", "--db", str(self.db_path), "--dir", str(self.reviews_dir)]
        )
        self.assertEqual(code, 1)
        self.assertIn("no saved source with ID 999", err)

    def test_review_overwrite_protection(self):
        self.seed_one_source()
        self.run_main(["review", "1", "--db", str(self.db_path), "--dir", str(self.reviews_dir)])

        code, _, err = self.run_main(
            ["review", "1", "--db", str(self.db_path), "--dir", str(self.reviews_dir)]
        )

        self.assertEqual(code, 1)
        self.assertIn("already exists", err)
        self.assertIn("--overwrite", err)

    def test_review_overwrite_flag_replaces_file(self):
        self.seed_one_source()
        self.run_main(["review", "1", "--db", str(self.db_path), "--dir", str(self.reviews_dir)])
        self.run_main(["note", "1", "New note", "--db", str(self.db_path)])

        code, _, _ = self.run_main(
            ["review", "1", "--overwrite", "--db", str(self.db_path), "--dir", str(self.reviews_dir)]
        )

        self.assertEqual(code, 0)
        files = list(self.reviews_dir.glob("*.md"))
        self.assertIn("New note", files[0].read_text(encoding="utf-8"))

    # -- config ----------------------------------------------------------

    def test_config_requires_approved_status(self):
        self.seed_one_source()  # pending by default

        code, _, err = self.run_main(["config", "1", "--db", str(self.db_path)])

        self.assertEqual(code, 1)
        self.assertIn("not approved", err)

    def test_config_prints_yaml_to_stdout_when_approved(self):
        self.seed_one_source()
        self.run_main(["approve", "1", "--db", str(self.db_path)])

        code, out, _ = self.run_main(["config", "1", "--db", str(self.db_path)])

        self.assertEqual(code, 0)
        data = yaml.safe_load(out)
        self.assertEqual(data[0]["id"], 1)
        self.assertEqual(data[0]["approved"], True)
        self.assertEqual(data[0]["domain"], "example.com")

    def test_config_missing_id_reports_clean_error(self):
        code, _, err = self.run_main(["config", "999", "--db", str(self.db_path)])
        self.assertEqual(code, 1)
        self.assertIn("no saved source with ID 999", err)

    def test_config_output_appends_to_file(self):
        self.seed_one_source()
        self.run_main(["approve", "1", "--db", str(self.db_path)])

        code, out, _ = self.run_main(
            ["config", "1", "--db", str(self.db_path), "--output", str(self.config_path)]
        )

        self.assertEqual(code, 0)
        self.assertIn("Appended", out)
        data = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], 1)

    def test_config_output_rejects_duplicate_id(self):
        self.seed_one_source()
        self.run_main(["approve", "1", "--db", str(self.db_path)])
        self.run_main(["config", "1", "--db", str(self.db_path), "--output", str(self.config_path)])

        code, _, err = self.run_main(
            ["config", "1", "--db", str(self.db_path), "--output", str(self.config_path)]
        )

        self.assertEqual(code, 1)
        self.assertIn("already present", err)

    # -- export-approved ---------------------------------------------------

    def test_export_approved_only_includes_approved(self):
        self.seed_one_source()
        with patch(
            "source_curator.cli.inspect",
            return_value=replace(
                SAMPLE,
                requested_url="https://other.example.com",
                final_url="https://other.example.com/",
                domain="other.example.com",
                title="Other",
            ),
        ):
            self.run_main(["add", "https://other.example.com", "--db", str(self.db_path)])
        self.run_main(["approve", "1", "--db", str(self.db_path)])
        self.run_main(["reject", "2", "--db", str(self.db_path)])

        code, out, _ = self.run_main(
            ["export-approved", "--db", str(self.db_path), "--output", str(self.export_path)]
        )

        self.assertEqual(code, 0)
        self.assertIn("Exported 1 approved", out)
        data = yaml.safe_load(self.export_path.read_text(encoding="utf-8"))
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], 1)

    def test_export_approved_regenerates_file(self):
        self.seed_one_source()
        self.run_main(["approve", "1", "--db", str(self.db_path)])
        self.run_main(["export-approved", "--db", str(self.db_path), "--output", str(self.export_path)])

        self.run_main(["reject", "1", "--db", str(self.db_path)])
        self.run_main(["export-approved", "--db", str(self.db_path), "--output", str(self.export_path)])

        data = yaml.safe_load(self.export_path.read_text(encoding="utf-8"))
        self.assertEqual(data, [])

    # -- validate-config ---------------------------------------------------

    def test_validate_config_on_generated_export_is_valid(self):
        self.seed_one_source()
        self.run_main(["approve", "1", "--db", str(self.db_path)])
        self.run_main(["export-approved", "--db", str(self.db_path), "--output", str(self.export_path)])

        code, out, _ = self.run_main(["validate-config", str(self.export_path)])

        self.assertEqual(code, 0)
        self.assertIn("OK", out)

    def test_validate_config_reports_issues_and_exits_nonzero(self):
        bad_path = Path(self._tmpdir.name) / "bad.yaml"
        bad_path.write_text("- id: 1\n  name: A\n  approved: true\n", encoding="utf-8")

        code, out, _ = self.run_main(["validate-config", str(bad_path)])

        self.assertEqual(code, 1)
        self.assertIn("Invalid", out)
        self.assertIn("url", out)

    def test_validate_config_malformed_yaml_handled_cleanly(self):
        bad_path = Path(self._tmpdir.name) / "bad.yaml"
        bad_path.write_text("id: [1, 2\nname: broken", encoding="utf-8")

        code, out, _ = self.run_main(["validate-config", str(bad_path)])

        self.assertEqual(code, 1)
        self.assertIn("Malformed YAML", out)


class CheckCommandTests(unittest.TestCase):
    """Tests for check/check-all and the --health filter on list/show.

    `source_curator.cli.health.check_source` is patched directly: cli.py looks
    it up through the `health` module on every call (not a bound default
    parameter), so this reliably intercepts it without any real HTTP or feed
    fetching -- health.check_source's own logic is already covered by
    test_health.py. Each test gets its own temp DB file.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "sources.db"

    def tearDown(self):
        self._tmpdir.cleanup()

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv + ["--db", str(self.db_path)])
        return code, out.getvalue(), err.getvalue()

    def seed_source(self, inspection=SAMPLE):
        with patch("source_curator.cli.inspect", return_value=inspection):
            self.run_main(["add", inspection.requested_url])

    @staticmethod
    def make_result(**overrides):
        from source_curator.health import HEALTH_HEALTHY, HealthCheckResult
        from source_curator.scoring import FRESH_UNKNOWN

        defaults = dict(
            checked_at="2026-10-07T00:00:00+00:00",
            reachable=True,
            http_status=200,
            final_url="https://example.com/",
            feed_found=False,
            feed_reachable=None,
            feed_url=None,
            latest_feed_date=None,
            freshness=FRESH_UNKNOWN,
            health_status=HEALTH_HEALTHY,
            error=None,
        )
        defaults.update(overrides)
        return HealthCheckResult(**defaults)

    def test_check_healthy_source_no_feed(self):
        self.seed_source(SAMPLE)

        with patch("source_curator.cli.health.check_source", return_value=self.make_result()):
            code, out, _ = self.run_main(["check", "1"])

        self.assertEqual(code, 0)
        self.assertIn("Reachable:        Yes", out)
        self.assertIn("HTTP status:      200", out)
        self.assertIn("Health status:    HEALTHY", out)

        code, out, _ = self.run_main(["show", "1"])
        self.assertIn("Health status:", out)
        self.assertIn("HEALTHY", out)
        self.assertIn("Last checked:", out)

    def test_check_stale_source_with_old_feed(self):
        from source_curator.health import HEALTH_STALE
        from source_curator.scoring import FRESH_OLD

        self.seed_source(SAMPLE)
        result = self.make_result(
            feed_found=True,
            feed_reachable=True,
            feed_url="https://example.com/feed.xml",
            freshness=FRESH_OLD,
            health_status=HEALTH_STALE,
        )

        with patch("source_curator.cli.health.check_source", return_value=result):
            code, out, _ = self.run_main(["check", "1"])

        self.assertEqual(code, 0)
        self.assertIn("Freshness:        Old", out)
        self.assertIn("Health status:    STALE", out)

    def test_check_broken_source_unreachable(self):
        from source_curator.health import HEALTH_BROKEN
        from source_curator.scoring import FRESH_UNKNOWN

        self.seed_source(SAMPLE)
        result = self.make_result(
            reachable=False,
            http_status=None,
            final_url=None,
            freshness=FRESH_UNKNOWN,
            health_status=HEALTH_BROKEN,
            error="Could not connect to https://example.com/",
        )

        with patch("source_curator.cli.health.check_source", return_value=result):
            code, out, _ = self.run_main(["check", "1"])

        self.assertEqual(code, 0)  # check succeeds even though the source itself is broken
        self.assertIn("Reachable:        No", out)
        self.assertIn("Health status:    BROKEN", out)
        self.assertIn("Could not connect", out)

    def test_check_broken_source_feed_failure(self):
        from source_curator.health import HEALTH_BROKEN
        from source_curator.scoring import FRESH_UNKNOWN

        self.seed_source(SAMPLE)
        result = self.make_result(
            feed_found=True,
            feed_reachable=False,
            feed_url="https://example.com/feed.xml",
            freshness=FRESH_UNKNOWN,
            health_status=HEALTH_BROKEN,
            error="Could not connect to feed URL",
        )

        with patch("source_curator.cli.health.check_source", return_value=result):
            code, out, _ = self.run_main(["check", "1"])

        self.assertEqual(code, 0)
        self.assertIn("Health status:    BROKEN", out)

    def test_check_missing_id_reports_clean_error(self):
        code, _, err = self.run_main(["check", "999"])
        self.assertEqual(code, 1)
        self.assertIn("no saved source with ID 999", err)

    def test_check_all_one_failure_does_not_stop_the_rest(self):
        from source_curator.health import HEALTH_BROKEN
        from source_curator.scoring import FRESH_UNKNOWN

        self.seed_source(SAMPLE)
        other = replace(
            SAMPLE,
            requested_url="https://other.example.com",
            final_url="https://other.example.com/",
            domain="other.example.com",
            title="Other",
        )
        self.seed_source(other)

        healthy_result = self.make_result()
        broken_result = self.make_result(
            reachable=False,
            http_status=None,
            final_url=None,
            freshness=FRESH_UNKNOWN,
            health_status=HEALTH_BROKEN,
            error="Could not connect to https://other.example.com/",
        )

        def fake_check_source(row, timeout):
            return broken_result if row["domain"] == "other.example.com" else healthy_result

        with patch("source_curator.cli.health.check_source", side_effect=fake_check_source):
            code, out, _ = self.run_main(["check-all"])

        self.assertEqual(code, 0)
        self.assertIn("#1", out)
        self.assertIn("#2", out)
        self.assertIn("HEALTHY", out)
        self.assertIn("BROKEN", out)
        self.assertIn("Checked 2 source(s): 1 healthy, 0 stale, 1 broken.", out)

    def test_check_all_empty_library(self):
        code, out, _ = self.run_main(["check-all"])
        self.assertEqual(code, 0)
        self.assertIn("No sources saved yet.", out)

    def test_list_health_filter(self):
        self.seed_source(SAMPLE)

        with patch("source_curator.cli.health.check_source", return_value=self.make_result()):
            self.run_main(["check", "1"])

        code, out, _ = self.run_main(["list", "--health", "healthy"])
        self.assertIn("#1", out)

        code, out, _ = self.run_main(["list", "--health", "broken"])
        self.assertIn("No sources with health 'broken'.", out)

    def test_list_health_filter_invalid_choice_rejected_by_argparse(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                main(["list", "--health", "bogus", "--db", str(self.db_path)])
        self.assertEqual(ctx.exception.code, 2)

    def test_unchecked_source_shows_never_checked_in_show(self):
        self.seed_source(SAMPLE)
        code, out, _ = self.run_main(["show", "1"])
        self.assertIn("(never checked)", out)
        self.assertIn("(unchecked)", out)


if __name__ == "__main__":
    unittest.main()
