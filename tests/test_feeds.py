"""Tests for source_curator.feeds. No network access: requests.get is mocked."""

import unittest
from unittest.mock import patch

import requests

from source_curator.feeds import (
    MAX_ANCHOR_CANDIDATES,
    discover_anchor_feed_candidates,
    discover_feed_links,
    find_feed,
    parse_feed,
    verified_feed,
)

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

SITEMAP_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/</loc></url>
</urlset>
"""

HTML_WITH_RSS_ANCHOR = """
<html><head><title>RSS Feeds</title></head><body>
<a href="/about.html">About</a>
<a href="/rss/news.xml">News Releases</a>
</body></html>
"""


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


class DiscoverAnchorFeedCandidatesTests(unittest.TestCase):
    BASE = "https://example.com/rss/index.html"

    def candidates(self, body):
        return discover_anchor_feed_candidates(f"<html><body>{body}</body></html>", self.BASE)

    def test_xml_rss_and_atom_suffixes(self):
        body = '<a href="a.xml">A</a><a href="/b.rss">B</a><a href="/c.atom">C</a>'
        self.assertEqual(
            self.candidates(body),
            [
                "https://example.com/rss/a.xml",
                "https://example.com/b.rss",
                "https://example.com/c.atom",
            ],
        )

    def test_rss_and_feed_path_segments(self):
        body = '<a href="/rss">One</a><a href="/blog/feed/">Two</a>'
        self.assertEqual(
            self.candidates(body),
            ["https://example.com/rss", "https://example.com/blog/feed/"],
        )

    def test_rss_or_atom_wording_in_text_or_metadata(self):
        body = (
            '<a href="/subscribe">Subscribe via <b>RSS</b></a>'
            '<a href="/updates" title="Atom feed">Updates</a>'
            '<a href="/x" type="application/rss+xml">X</a>'
        )
        self.assertEqual(
            self.candidates(body),
            [
                "https://example.com/x",
                "https://example.com/subscribe",
                "https://example.com/updates",
            ],
        )

    def test_strong_matches_come_before_weak_ones(self):
        body = '<a href="/subscribe">RSS</a><a href="/news.xml">News</a>'
        self.assertEqual(
            self.candidates(body),
            ["https://example.com/news.xml", "https://example.com/subscribe"],
        )

    def test_ordinary_links_are_ignored(self):
        body = '<a href="/about.html">About</a><a href="/news">News</a><a href="/press">Press</a>'
        self.assertEqual(self.candidates(body), [])

    def test_words_merely_containing_rss_or_atom_are_ignored(self):
        body = '<a href="/x">Universal design</a><a href="/y">Atomic</a>'
        self.assertEqual(self.candidates(body), [])

    def test_duplicates_and_fragments_collapse(self):
        body = (
            '<a href="/f.xml">A</a><a href="https://example.com/f.xml">B</a>'
            '<a href="/f.xml#top">C</a>'
        )
        self.assertEqual(self.candidates(body), ["https://example.com/f.xml"])

    def test_link_back_to_the_page_itself_is_skipped(self):
        self.assertEqual(self.candidates('<a href="/rss/index.html">RSS</a>'), [])

    def test_non_http_links_are_skipped(self):
        body = '<a href="mailto:rss@example.com">RSS</a><a href="javascript:void(0)">RSS</a>'
        self.assertEqual(self.candidates(body), [])

    def test_link_rel_alternate_tags_are_not_anchor_candidates(self):
        self.assertEqual(discover_anchor_feed_candidates(HTML_WITH_RSS_LINK, self.BASE), [])


class VerifiedFeedTests(unittest.TestCase):
    def test_rss_is_verified(self):
        feed = verified_feed(RSS_XML, "https://blog.example.com/feed.xml")
        self.assertTrue(feed.found)
        self.assertEqual(feed.feed_url, "https://blog.example.com/feed.xml")
        self.assertEqual(feed.title, "Example Blog")
        self.assertEqual(len(feed.items), 2)

    def test_atom_is_verified(self):
        feed = verified_feed(ATOM_XML, "https://blog.example.com/atom.xml")
        self.assertEqual(feed.title, "Example Atom Feed")

    def test_non_feed_xml_is_rejected(self):
        self.assertIsNone(verified_feed(SITEMAP_XML, "https://example.com/sitemap.xml"))

    def test_html_is_rejected(self):
        self.assertIsNone(verified_feed(HTML_WITHOUT_FEED.encode(), "https://example.com/"))

    def test_malformed_content_is_rejected(self):
        self.assertIsNone(verified_feed(b"<not><valid#$ feed", "https://example.com/f.xml"))
        self.assertIsNone(verified_feed(b"", "https://example.com/f.xml"))


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


class FindFeedAnchorTests(unittest.TestCase):
    """Pages that only expose feeds as plain <a href> links."""

    @patch("source_curator.feeds.requests.get")
    def test_anchor_to_valid_rss_feed_is_found(self, mock_get):
        mock_get.return_value = FakeFeedResponse(content=RSS_XML)

        result = find_feed(HTML_WITH_RSS_ANCHOR, "https://example.com/rss/index.html")

        self.assertTrue(result.found)
        self.assertEqual(result.feed_url, "https://example.com/rss/news.xml")
        self.assertEqual(result.title, "Example Blog")
        self.assertEqual(len(result.items), 2)
        self.assertIsNone(result.error)
        mock_get.assert_called_once()
        self.assertEqual(mock_get.call_args[0][0], "https://example.com/rss/news.xml")

    @patch("source_curator.feeds.requests.get")
    def test_anchor_to_non_feed_xml_is_not_reported(self, mock_get):
        mock_get.return_value = FakeFeedResponse(content=SITEMAP_XML)

        result = find_feed('<a href="/sitemap.xml">Sitemap</a>', "https://example.com/")

        self.assertFalse(result.found)
        self.assertIsNone(result.feed_url)
        self.assertIsNone(result.error)

    @patch("source_curator.feeds.requests.get")
    def test_anchor_to_malformed_feed_is_not_reported(self, mock_get):
        mock_get.return_value = FakeFeedResponse(content=b"<not><valid#$ feed")

        result = find_feed('<a href="/feed.xml">RSS</a>', "https://example.com/")

        self.assertFalse(result.found)
        self.assertIsNone(result.error)

    @patch("source_curator.feeds.requests.get")
    def test_skips_bad_candidates_until_a_real_feed(self, mock_get):
        mock_get.side_effect = [
            requests.exceptions.ConnectionError("nope"),
            FakeFeedResponse(status_code=404),
            FakeFeedResponse(content=SITEMAP_XML),
            FakeFeedResponse(content=ATOM_XML),
        ]
        html = (
            '<a href="/a.xml">A</a><a href="/b.xml">B</a>'
            '<a href="/sitemap.xml">S</a><a href="/atom.xml">Atom</a>'
        )

        result = find_feed(html, "https://example.com/")

        self.assertTrue(result.found)
        self.assertEqual(result.feed_url, "https://example.com/atom.xml")
        self.assertEqual(result.title, "Example Atom Feed")
        self.assertEqual(mock_get.call_count, 4)

    @patch("source_curator.feeds.requests.get")
    def test_duplicate_candidates_are_fetched_once(self, mock_get):
        mock_get.return_value = FakeFeedResponse(content=SITEMAP_XML)
        html = (
            '<a href="/f.xml">RSS</a><a href="https://example.com/f.xml">RSS again</a>'
            '<a href="f.xml">x</a>'
        )

        result = find_feed(html, "https://example.com/")

        self.assertFalse(result.found)
        mock_get.assert_called_once()

    @patch("source_curator.feeds.requests.get")
    def test_candidate_fetches_are_capped(self, mock_get):
        mock_get.return_value = FakeFeedResponse(content=SITEMAP_XML)
        html = "".join(f'<a href="/f{i}.xml">F{i}</a>' for i in range(MAX_ANCHOR_CANDIDATES + 5))

        result = find_feed(html, "https://example.com/")

        self.assertFalse(result.found)
        self.assertEqual(mock_get.call_count, MAX_ANCHOR_CANDIDATES)

    @patch("source_curator.feeds.requests.get")
    def test_rel_alternate_wins_over_anchors(self, mock_get):
        mock_get.return_value = FakeFeedResponse(content=RSS_XML)
        html = HTML_WITH_RSS_LINK + '<a href="/other.xml">Other RSS</a>'

        result = find_feed(html, "https://blog.example.com/")

        self.assertEqual(result.feed_url, "https://blog.example.com/feed.xml")
        mock_get.assert_called_once()

    @patch("source_curator.feeds.requests.get")
    def test_page_with_no_feed_links_makes_no_requests(self, mock_get):
        result = find_feed('<a href="/about">About</a>', "https://example.com/")

        self.assertFalse(result.found)
        mock_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
