"""Tests for the CLI layer. The inspector is patched, so no network access."""

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from source_curator.cli import format_inspection, main
from source_curator.feeds import FeedInfo, FeedItem
from source_curator.inspector import Inspection, InspectionError

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


if __name__ == "__main__":
    unittest.main()
