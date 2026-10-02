"""Tests for source_curator.feeds. No network access: requests.get is mocked."""

import unittest
from unittest.mock import patch

import requests

from source_curator.feeds import discover_feed_links, find_feed, parse_feed

RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Blog</title>
    <link>https://blog.example.com/</link>
    <item>
      <title>First Post</title>
      <link>https://blog.example.com/first-post</link>
      <pubDate>Wed, 01 Oct 2025 12:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Second Post</title>
      <link>https://blog.example.com/second-post</link>
      <pubDate>Tue, 30 Sep 2025 09:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""

ATOM_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Atom Feed</title>
  <entry>
    <title>Atom Entry One</title>
    <link href="https://blog.example.com/atom-one" />
    <updated>2025-10-01T12:00:00Z</updated>
  </entry>
</feed>
"""

HTML_WITH_RSS_LINK = """
<html><head>
<title>Example Blog</title>
<link rel="alternate" type="application/rss+xml" title="Example Blog RSS" href="/feed.xml">
</head><body></body></html>
"""

HTML_WITHOUT_FEED = "<html><head><title>No Feed Here</title></head><body></body></html>"


class FakeFeedResponse:
    def __init__(self, content=b"", status_code=200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} Client Error")


class DiscoverFeedLinksTests(unittest.TestCase):
    def test_finds_rss_link_and_resolves_relative_href(self):
        links = discover_feed_links(HTML_WITH_RSS_LINK, "https://blog.example.com/posts/")
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["href"], "https://blog.example.com/feed.xml")
        self.assertEqual(links[0]["type"], "application/rss+xml")
        self.assertEqual(links[0]["title"], "Example Blog RSS")

    def test_finds_atom_link(self):
        html = '<link rel="alternate" type="application/atom+xml" href="https://blog.example.com/atom.xml">'
        links = discover_feed_links(html, "https://blog.example.com/")
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["type"], "application/atom+xml")

    def test_case_insensitive_rel_and_type(self):
        html = '<LINK REL="ALTERNATE" TYPE="APPLICATION/RSS+XML" HREF="/feed.xml">'
        links = discover_feed_links(html, "https://blog.example.com/")
        self.assertEqual(len(links), 1)

    def test_ignores_non_feed_alternate_links(self):
        html = '<link rel="alternate" type="text/css" href="/print.css">'
        self.assertEqual(discover_feed_links(html, "https://example.com/"), [])

    def test_ignores_feed_type_without_alternate_rel(self):
        html = '<link rel="stylesheet" type="application/rss+xml" href="/feed.xml">'
        self.assertEqual(discover_feed_links(html, "https://example.com/"), [])

    def test_no_links_in_plain_page(self):
        self.assertEqual(discover_feed_links(HTML_WITHOUT_FEED, "https://example.com/"), [])

    def test_missing_href_is_skipped(self):
        html = '<link rel="alternate" type="application/rss+xml">'
        self.assertEqual(discover_feed_links(html, "https://example.com/"), [])


class ParseFeedTests(unittest.TestCase):
    def test_parses_rss_title_and_items(self):
        title, items = parse_feed(RSS_XML)
        self.assertEqual(title, "Example Blog")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].title, "First Post")
        self.assertEqual(items[0].link, "https://blog.example.com/first-post")
        self.assertEqual(items[0].published, "Wed, 01 Oct 2025 12:00:00 GMT")

    def test_parses_atom_title_and_items(self):
        title, items = parse_feed(ATOM_XML)
        self.assertEqual(title, "Example Atom Feed")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Atom Entry One")
        self.assertEqual(items[0].link, "https://blog.example.com/atom-one")
        self.assertEqual(items[0].published, "2025-10-01T12:00:00Z")

    def test_limits_to_five_items(self):
        entries = "".join(
            f"<item><title>Item {i}</title><link>https://example.com/{i}</link></item>"
            for i in range(7)
        )
        xml = f"<rss><channel><title>Many Items</title>{entries}</channel></rss>".encode()

        _, items = parse_feed(xml)

        self.assertEqual(len(items), 5)
        self.assertEqual(items[0].title, "Item 0")

    def test_malformed_content_returns_no_title_or_items(self):
        title, items = parse_feed(b"this is not xml at all")
        self.assertIsNone(title)
        self.assertEqual(items, [])


class FindFeedTests(unittest.TestCase):
    def test_no_feed_link_found(self):
        result = find_feed(HTML_WITHOUT_FEED, "https://example.com/")
        self.assertFalse(result.found)
        self.assertIsNone(result.feed_url)
        self.assertEqual(result.items, [])

    @patch("source_curator.feeds.requests.get")
    def test_feed_found_and_fetched_successfully(self, mock_get):
        mock_get.return_value = FakeFeedResponse(content=RSS_XML)

        result = find_feed(HTML_WITH_RSS_LINK, "https://blog.example.com/posts/")

        self.assertTrue(result.found)
        self.assertEqual(result.feed_url, "https://blog.example.com/feed.xml")
        self.assertEqual(result.title, "Example Blog")
        self.assertEqual(len(result.items), 2)
        self.assertIsNone(result.error)

    @patch("source_curator.feeds.requests.get")
    def test_sends_user_agent_and_timeout(self, mock_get):
        mock_get.return_value = FakeFeedResponse(content=RSS_XML)

        find_feed(HTML_WITH_RSS_LINK, "https://blog.example.com/", timeout=4.0)

        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["timeout"], 4.0)
        self.assertIn("source-curator-tool", kwargs["headers"]["User-Agent"])
        self.assertTrue(kwargs["allow_redirects"])

    @patch("source_curator.feeds.requests.get")
    def test_connection_error_is_handled_cleanly(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError("nope")

        result = find_feed(HTML_WITH_RSS_LINK, "https://blog.example.com/")

        self.assertTrue(result.found)
        self.assertEqual(result.feed_url, "https://blog.example.com/feed.xml")
        self.assertIn("Could not connect", result.error)
        self.assertEqual(result.items, [])

    @patch("source_curator.feeds.requests.get")
    def test_timeout_is_handled_cleanly(self, mock_get):
        mock_get.side_effect = requests.exceptions.Timeout()

        result = find_feed(HTML_WITH_RSS_LINK, "https://blog.example.com/", timeout=3)

        self.assertTrue(result.found)
        self.assertIn("timed out", result.error)

    @patch("source_curator.feeds.requests.get")
    def test_http_error_status_is_handled_cleanly(self, mock_get):
        mock_get.return_value = FakeFeedResponse(status_code=404)

        result = find_feed(HTML_WITH_RSS_LINK, "https://blog.example.com/")

        self.assertTrue(result.found)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.items, [])

    @patch("source_curator.feeds.requests.get")
    def test_malformed_feed_body_is_handled_cleanly(self, mock_get):
        mock_get.return_value = FakeFeedResponse(content=b"<not><valid#$ feed")

        result = find_feed(HTML_WITH_RSS_LINK, "https://blog.example.com/")

        self.assertTrue(result.found)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.items, [])


if __name__ == "__main__":
    unittest.main()
