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

    def __init__(
        self, status_code=200, url="https://example.com/", headers=None, text="", content=None
    ):
        self.status_code = status_code
        self.url = url
        self.headers = headers if headers is not None else {"Content-Type": "text/html"}
        self.text = text
        self.content = content if content is not None else text.encode("utf-8")


class FakeFeedResponse:
    """Just enough of requests.Response for the feed fetch."""

    def __init__(self, content=b"", status_code=200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} Client Error")


HTML_WITH_FEED = """<html><head><title>Example Blog</title>
<link rel="alternate" type="application/rss+xml" title="RSS" href="/feed.xml">
</head><body></body></html>"""

RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Example Blog</title>
<item><title>First Post</title><link>https://blog.example.com/first</link>
<pubDate>Wed, 01 Oct 2025 12:00:00 GMT</pubDate></item>
<item><title>Second Post</title><link>https://blog.example.com/second</link>
<pubDate>Tue, 30 Sep 2025 09:00:00 GMT</pubDate></item>
</channel></rss>"""
ATOM_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Example Atom Feed</title>
<entry><title>Atom Entry One</title><link href="https://blog.example.com/atom-one" />
<updated>2025-10-01T12:00:00Z</updated></entry>
</feed>"""

SITEMAP_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://example.com/</loc></url></urlset>"""

HTML_WITH_RSS_ANCHOR = """<html><head><title>RSS Feeds</title></head><body>
<a href="/rss/news.xml">News Releases</a>
</body></html>"""


def xml_response(content, url, content_type="text/xml"):
    return FakeResponse(
        url=url, headers={"Content-Type": content_type}, text=content.decode(), content=content
    )


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


class InspectFeedIntegrationTests(unittest.TestCase):
    """source_curator.feeds and source_curator.inspector both call the same shared
    `requests.get`, so one patch with a side_effect sequence stands in for both the
    page fetch and the (optional) feed fetch, in call order."""

    @patch("source_curator.inspector.requests.get")
    def test_detects_and_previews_feed(self, mock_get):
        mock_get.side_effect = [
            FakeResponse(url="https://blog.example.com/", text=HTML_WITH_FEED),
            FakeFeedResponse(content=RSS_XML),
        ]

        result = inspect("https://blog.example.com")

        self.assertTrue(result.feed.found)
        self.assertEqual(result.feed.feed_url, "https://blog.example.com/feed.xml")
        self.assertEqual(result.feed.title, "Example Blog")
        self.assertEqual(len(result.feed.items), 2)
        self.assertEqual(result.feed.items[0].title, "First Post")
        self.assertEqual(result.feed.items[0].link, "https://blog.example.com/first")
        self.assertIsNone(result.feed.error)

    @patch("source_curator.inspector.requests.get")
    def test_no_feed_link_reports_not_found_without_extra_request(self, mock_get):
        mock_get.return_value = FakeResponse(
            url="https://example.com/",
            text="<html><head><title>No Feed</title></head></html>",
        )

        result = inspect("https://example.com")

        self.assertFalse(result.feed.found)
        self.assertEqual(result.feed.items, [])
        self.assertEqual(mock_get.call_count, 1)

    @patch("source_curator.inspector.requests.get")
    def test_unreachable_feed_handled_cleanly(self, mock_get):
        mock_get.side_effect = [
            FakeResponse(url="https://blog.example.com/", text=HTML_WITH_FEED),
            requests.exceptions.ConnectionError("nope"),
        ]

        result = inspect("https://blog.example.com")

        self.assertTrue(result.feed.found)
        self.assertEqual(result.feed.feed_url, "https://blog.example.com/feed.xml")
        self.assertIsNotNone(result.feed.error)
        self.assertEqual(result.feed.items, [])

    @patch("source_curator.inspector.requests.get")
    def test_malformed_feed_handled_cleanly(self, mock_get):
        mock_get.side_effect = [
            FakeResponse(url="https://blog.example.com/", text=HTML_WITH_FEED),
            FakeFeedResponse(content=b"this is not xml at all"),
        ]

        result = inspect("https://blog.example.com")

        self.assertTrue(result.feed.found)
        self.assertIsNotNone(result.feed.error)
        self.assertEqual(result.feed.items, [])

    @patch("source_curator.inspector.requests.get")
    def test_non_html_content_skips_feed_detection(self, mock_get):
        mock_get.return_value = FakeResponse(
            headers={"Content-Type": "application/json"}, text='{"ok": true}'
        )

        result = inspect("https://example.com/data.json")

        self.assertFalse(result.feed.found)
        self.assertEqual(mock_get.call_count, 1)

    @patch("source_curator.inspector.requests.get")
    def test_detects_feed_from_plain_anchor_link(self, mock_get):
        mock_get.side_effect = [
            FakeResponse(url="https://example.com/rss/index.html", text=HTML_WITH_RSS_ANCHOR),
            FakeFeedResponse(content=RSS_XML),
        ]

        result = inspect("https://example.com/rss/index.html")

        self.assertEqual(result.title, "RSS Feeds")
        self.assertTrue(result.feed.found)
        self.assertEqual(result.feed.feed_url, "https://example.com/rss/news.xml")
        self.assertEqual(result.feed.title, "Example Blog")
        self.assertEqual(len(result.feed.items), 2)

    @patch("source_curator.inspector.requests.get")
    def test_anchor_to_non_feed_xml_reports_not_found(self, mock_get):
        mock_get.side_effect = [
            FakeResponse(url="https://example.com/", text='<a href="/sitemap.xml">Sitemap</a>'),
            FakeFeedResponse(content=SITEMAP_XML),
        ]

        result = inspect("https://example.com/")

        self.assertFalse(result.feed.found)
        self.assertIsNone(result.feed.error)


class InspectDirectFeedTests(unittest.TestCase):
    """Inspecting a URL that is itself an RSS/Atom feed."""

    @patch("source_curator.inspector.requests.get")
    def test_direct_rss_url(self, mock_get):
        mock_get.return_value = xml_response(RSS_XML, "https://blog.example.com/feed.xml")

        result = inspect("https://blog.example.com/feed.xml")

        self.assertTrue(result.feed.found)
        self.assertEqual(result.feed.feed_url, "https://blog.example.com/feed.xml")
        self.assertEqual(result.feed.title, "Example Blog")
        self.assertEqual(result.title, "Example Blog")
        self.assertEqual(len(result.feed.items), 2)
        self.assertEqual(result.feed.items[0].published, "Wed, 01 Oct 2025 12:00:00 GMT")
        self.assertIsNone(result.feed.error)
        self.assertEqual(mock_get.call_count, 1)

    @patch("source_curator.inspector.requests.get")
    def test_direct_atom_url(self, mock_get):
        mock_get.return_value = xml_response(
            ATOM_XML, "https://blog.example.com/atom", content_type="application/atom+xml"
        )

        result = inspect("https://blog.example.com/atom")

        self.assertTrue(result.feed.found)
        self.assertEqual(result.feed.feed_url, "https://blog.example.com/atom")
        self.assertEqual(result.feed.title, "Example Atom Feed")
        self.assertEqual(result.feed.items[0].published, "2025-10-01T12:00:00Z")

    @patch("source_curator.inspector.requests.get")
    def test_direct_feed_uses_final_url_after_redirect(self, mock_get):
        mock_get.return_value = xml_response(RSS_XML, "https://blog.example.com/feed.xml")

        result = inspect("http://blog.example.com/rss")

        self.assertEqual(result.feed.feed_url, "https://blog.example.com/feed.xml")

    @patch("source_curator.inspector.requests.get")
    def test_feed_served_as_text_html_is_still_recognized(self, mock_get):
        mock_get.return_value = xml_response(
            RSS_XML, "https://example.com/feed", content_type="text/html; charset=utf-8"
        )

        result = inspect("https://example.com/feed")

        self.assertTrue(result.feed.found)
        self.assertEqual(result.feed.title, "Example Blog")

    @patch("source_curator.inspector.requests.get")
    def test_direct_malformed_feed_is_not_reported(self, mock_get):
        mock_get.return_value = xml_response(b"<not><valid#$ feed", "https://example.com/f.xml")

        result = inspect("https://example.com/f.xml")

        self.assertFalse(result.feed.found)
        self.assertEqual(mock_get.call_count, 1)

    @patch("source_curator.inspector.requests.get")
    def test_direct_non_feed_xml_is_not_reported(self, mock_get):
        mock_get.return_value = xml_response(SITEMAP_XML, "https://example.com/sitemap.xml")

        result = inspect("https://example.com/sitemap.xml")

        self.assertFalse(result.feed.found)
        self.assertIsNone(result.title)
        self.assertEqual(mock_get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
