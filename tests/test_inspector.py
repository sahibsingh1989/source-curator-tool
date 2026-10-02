"""Tests for source_curator.inspector. No network access: requests.get is mocked."""

import unittest
from unittest.mock import patch

import requests

from source_curator.inspector import (
    InspectionError,
    extract_title,
    inspect,
    validate_url,
)


class FakeResponse:
    """Just enough of requests.Response for the inspector."""

    def __init__(self, status_code=200, url="https://example.com/", headers=None, text=""):
        self.status_code = status_code
        self.url = url
        self.headers = headers if headers is not None else {"Content-Type": "text/html"}
        self.text = text


class ValidateUrlTests(unittest.TestCase):
    def test_accepts_http_and_https(self):
        self.assertEqual(validate_url("http://example.com"), "http://example.com")
        self.assertEqual(validate_url("https://example.com/a?b=1"), "https://example.com/a?b=1")

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(validate_url("  https://example.com  "), "https://example.com")

    def test_rejects_other_schemes(self):
        for bad in ("ftp://example.com", "file:///etc/hosts", "javascript:alert(1)"):
            with self.subTest(url=bad):
                with self.assertRaises(InspectionError):
                    validate_url(bad)

    def test_rejects_missing_scheme(self):
        with self.assertRaises(InspectionError):
            validate_url("example.com")

    def test_rejects_missing_host(self):
        with self.assertRaises(InspectionError):
            validate_url("http://")

    def test_rejects_empty(self):
        with self.assertRaises(InspectionError):
            validate_url("")


class ExtractTitleTests(unittest.TestCase):
    def test_simple_title(self):
        self.assertEqual(extract_title("<html><head><title>Hello</title></head></html>"), "Hello")

    def test_collapses_whitespace(self):
        html = "<title>\n  Spread   over\tlines\n</title>"
        self.assertEqual(extract_title(html), "Spread over lines")

    def test_decodes_entities(self):
        self.assertEqual(extract_title("<title>Tips &amp; Tricks</title>"), "Tips & Tricks")

    def test_uppercase_tag(self):
        self.assertEqual(extract_title("<TITLE>Shouting</TITLE>"), "Shouting")

    def test_first_title_wins(self):
        self.assertEqual(extract_title("<title>One</title><title>Two</title>"), "One")

    def test_missing_title_is_none(self):
        self.assertIsNone(extract_title("<html><body>No title here</body></html>"))

    def test_empty_title_is_none(self):
        self.assertIsNone(extract_title("<title>   </title>"))

    def test_unclosed_title_still_read(self):
        self.assertEqual(extract_title("<html><head><title>Truncated"), "Truncated")


class InspectTests(unittest.TestCase):
    @patch("source_curator.inspector.requests.get")
    def test_reports_response_details(self, mock_get):
        mock_get.return_value = FakeResponse(
            status_code=200,
            url="https://example.com/",
            headers={"Content-Type": "text/html; charset=UTF-8"},
            text="<html><head><title>Example Domain</title></head></html>",
        )

        result = inspect("https://example.com")

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.final_url, "https://example.com/")
        self.assertEqual(result.domain, "example.com")
        self.assertEqual(result.title, "Example Domain")
        self.assertEqual(result.content_type, "text/html; charset=UTF-8")
        self.assertEqual(result.requested_url, "https://example.com")

    @patch("source_curator.inspector.requests.get")
    def test_sends_user_agent_timeout_and_follows_redirects(self, mock_get):
        mock_get.return_value = FakeResponse()

        inspect("https://example.com", timeout=3.5)

        _, kwargs = mock_get.call_args
        self.assertTrue(kwargs["allow_redirects"])
        self.assertEqual(kwargs["timeout"], 3.5)
        self.assertIn("source-curator-tool", kwargs["headers"]["User-Agent"])

    @patch("source_curator.inspector.requests.get")
    def test_uses_final_url_after_redirect(self, mock_get):
        mock_get.return_value = FakeResponse(
            url="https://www.final.example.org/landing", text="<title>Landed</title>"
        )

        result = inspect("http://start.example.com")

        self.assertEqual(result.final_url, "https://www.final.example.org/landing")
        self.assertEqual(result.domain, "www.final.example.org")

    @patch("source_curator.inspector.requests.get")
    def test_reports_error_status_without_raising(self, mock_get):
        mock_get.return_value = FakeResponse(status_code=404, text="<title>Not Found</title>")

        result = inspect("https://example.com/missing")

        self.assertEqual(result.status_code, 404)
        self.assertEqual(result.title, "Not Found")

    @patch("source_curator.inspector.requests.get")
    def test_skips_title_for_non_html(self, mock_get):
        mock_get.return_value = FakeResponse(
            headers={"Content-Type": "application/json"}, text='{"title": "not html"}'
        )

        result = inspect("https://example.com/data.json")

        self.assertIsNone(result.title)
        self.assertEqual(result.content_type, "application/json")

    @patch("source_curator.inspector.requests.get")
    def test_missing_content_type(self, mock_get):
        mock_get.return_value = FakeResponse(headers={}, text="<title>Bare</title>")

        result = inspect("https://example.com")

        self.assertIsNone(result.content_type)
        self.assertEqual(result.title, "Bare")

    @patch("source_curator.inspector.requests.get")
    def test_connection_error(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError("nope")

        with self.assertRaises(InspectionError) as ctx:
            inspect("https://nothing.invalid")
        self.assertIn("Could not connect", str(ctx.exception))

    @patch("source_curator.inspector.requests.get")
    def test_timeout_error(self, mock_get):
        mock_get.side_effect = requests.exceptions.Timeout()

        with self.assertRaises(InspectionError) as ctx:
            inspect("https://slow.example.com", timeout=2)
        self.assertIn("timed out", str(ctx.exception))

    @patch("source_curator.inspector.requests.get")
    def test_too_many_redirects(self, mock_get):
        mock_get.side_effect = requests.exceptions.TooManyRedirects()

        with self.assertRaises(InspectionError) as ctx:
            inspect("https://loop.example.com")
        self.assertIn("Too many redirects", str(ctx.exception))

    @patch("source_curator.inspector.requests.get")
    def test_other_request_exception(self, mock_get):
        mock_get.side_effect = requests.exceptions.RequestException("boom")

        with self.assertRaises(InspectionError) as ctx:
            inspect("https://example.com")
        self.assertIn("Request failed", str(ctx.exception))

    @patch("source_curator.inspector.requests.get")
    def test_invalid_url_never_hits_network(self, mock_get):
        with self.assertRaises(InspectionError):
            inspect("ftp://example.com")
        mock_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
