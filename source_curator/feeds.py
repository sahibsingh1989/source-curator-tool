"""Detect an RSS/Atom feed linked from an HTML page (or given directly) and preview recent items."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlparse

import feedparser
import requests

USER_AGENT = "source-curator-tool/0.1 (+https://example.invalid/source-curator-tool)"
DEFAULT_TIMEOUT = 10.0
MAX_ITEMS = 5
# Cap on how many <a href> feed candidates are fetched when verifying a page's
# plain feed links, so a link-heavy page can't trigger dozens of requests.
MAX_ANCHOR_CANDIDATES = 5

# Path suffixes that strongly suggest an RSS/Atom document.
FEED_SUFFIXES = (".rss", ".xml", ".atom", ".rdf")
# Path segments that suggest a feed endpoint (e.g. /rss, /feed, /blog/feed/).
FEED_PATH_SEGMENTS = {"rss", "feed", "feeds", "atom"}
_FEED_WORDS = re.compile(r"\b(rss|atom)\b", re.IGNORECASE)

# type="" values seen in the wild for RSS/Atom <link rel="alternate"> tags.
FEED_TYPES = {
    "application/rss+xml",
    "application/atom+xml",
    "application/rdf+xml",
    "application/x.atom+xml",
}


@dataclass
class FeedItem:
    """One entry from a feed."""

    title: str | None
    link: str | None
    published: str | None


@dataclass
class FeedInfo:
    """What we learned about a page's feed, if any."""

    found: bool
    feed_url: str | None = None
    title: str | None = None
    items: list[FeedItem] = field(default_factory=list)
    error: str | None = None


class _FeedLinkParser(HTMLParser):
    """Collect <link rel="alternate" type="application/rss+xml" href="..."> tags."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        attrd = {name.lower(): value for name, value in attrs if name}
        rel = (attrd.get("rel") or "").lower().split()
        type_ = (attrd.get("type") or "").lower().strip()
        href = attrd.get("href")
        if href and "alternate" in rel and _is_feed_type(type_):
            self.links.append({"href": href, "type": type_, "title": attrd.get("title")})


def _is_feed_type(content_type: str) -> bool:
    if not content_type:
        return False
    return content_type in FEED_TYPES or "rss" in content_type or "atom" in content_type


class _AnchorParser(HTMLParser):
    """Collect <a href="..."> links with their text and title/type attributes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[dict] = []
        self._current: dict | None = None

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        attrd = {name.lower(): value for name, value in attrs if name}
        href = (attrd.get("href") or "").strip()
        self._current = {
            "href": href,
            "title": attrd.get("title") or "",
            "type": (attrd.get("type") or "").lower().strip(),
            "text": [],
        }
        if href:
            self.anchors.append(self._current)

    def handle_endtag(self, tag):
        if tag == "a":
            self._current = None

    def handle_data(self, data):
        if self._current is not None:
            self._current["text"].append(data)


def _anchor_feed_score(url: str, anchor: dict, text: str) -> int:
    """How strongly an <a> link looks like a feed: 2 strong, 1 weak, 0 not at all."""
    path = urlparse(url).path.lower()
    if path.endswith(FEED_SUFFIXES) or _is_feed_type(anchor["type"]):
        return 2
    segments = {segment for segment in path.split("/") if segment}
    if (
        segments & FEED_PATH_SEGMENTS
        or _FEED_WORDS.search(text)
        or _FEED_WORDS.search(anchor["title"])
    ):
        return 1
    return 0


def discover_anchor_feed_candidates(html: str, base_url: str) -> list[str]:
    """Return absolute URLs of <a href> links that look like RSS/Atom feeds.

    These are only *candidates*: callers must fetch and verify them. Strong
    matches (.rss/.xml/.atom/.rdf paths, feed MIME type) come before weak ones
    (/rss or /feed path segments, "RSS"/"Atom" in the link text or title).
    Duplicates and links back to the page itself are dropped.
    """
    parser = _AnchorParser()
    parser.feed(html)
    parser.close()

    page_url = urldefrag(base_url).url
    seen: set[str] = set()
    scored: list[tuple[int, int, str]] = []
    for index, anchor in enumerate(parser.anchors):
        url = urldefrag(urljoin(base_url, anchor["href"])).url
        if urlparse(url).scheme not in ("http", "https") or url == page_url or url in seen:
            continue
        text = " ".join("".join(anchor["text"]).split())
        score = _anchor_feed_score(url, anchor, text)
        if score:
            seen.add(url)
            scored.append((-score, index, url))
    return [url for _, _, url in sorted(scored)]


def discover_feed_links(html: str, base_url: str) -> list[dict]:
    """Return feed links declared in `html` via <link rel="alternate">, resolved against base_url."""
    parser = _FeedLinkParser()
    parser.feed(html)
    parser.close()
    return [
        {"href": urljoin(base_url, link["href"]), "type": link["type"], "title": link["title"]}
        for link in parser.links
    ]


def _fetch_feed_bytes(url: str, timeout: float) -> bytes:
    """Fetch `url` and return the response body, or raise RuntimeError with a clean message."""
    headers = {"User-Agent": USER_AGENT}
    try:
        response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        response.raise_for_status()
    except requests.exceptions.Timeout:
        raise RuntimeError(f"Feed request timed out after {timeout:g}s") from None
    except requests.exceptions.ConnectionError:
        raise RuntimeError("Could not connect to feed URL") from None
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Feed request failed: {exc}") from None
    return response.content


def _entry_published(entry) -> str | None:
    return entry.get("published") or entry.get("updated") or None


def _parse(content: bytes):
    try:
        return feedparser.parse(content)
    except Exception:
        # feedparser is documented not to raise on malformed XML, but a third-party
        # parser bug here must never crash the CLI.
        return None


def parse_feed(content: bytes) -> tuple[str | None, list[FeedItem]]:
    """Parse feed bytes with feedparser. Never raises; returns (title, items)."""
    return _summarize(_parse(content))


def _summarize(parsed) -> tuple[str | None, list[FeedItem]]:
    if parsed is None:
        return None, []
    title = parsed.feed.get("title") if getattr(parsed, "feed", None) else None
    items = [
        FeedItem(
            title=entry.get("title"),
            link=entry.get("link"),
            published=_entry_published(entry),
        )
        for entry in parsed.entries[:MAX_ITEMS]
    ]
    return title, items


def verified_feed(content: bytes, feed_url: str) -> FeedInfo | None:
    """Return a FeedInfo if `content` really is an RSS/Atom document, else None.

    feedparser will happily "parse" any XML (or HTML), so we require it to have
    recognized an actual feed format and to have found a title or entries.
    """
    parsed = _parse(content)
    if parsed is None or not parsed.get("version"):
        return None
    title, items = _summarize(parsed)
    if title is None and not items:
        return None
    return FeedInfo(found=True, feed_url=feed_url, title=title, items=items)


def _find_anchor_feed(html: str, base_url: str, timeout: float) -> FeedInfo:
    """Fetch the page's feed-looking <a href> links and return the first real feed."""
    for url in discover_anchor_feed_candidates(html, base_url)[:MAX_ANCHOR_CANDIDATES]:
        try:
            content = _fetch_feed_bytes(url, timeout)
        except RuntimeError:
            continue
        feed = verified_feed(content, url)
        if feed is not None:
            return feed
    return FeedInfo(found=False)


def find_feed(html: str, base_url: str, timeout: float = DEFAULT_TIMEOUT) -> FeedInfo:
    """Look for a feed link in `html`; if one exists, fetch and preview it.

    A declared <link rel="alternate"> feed wins and is reported even if it
    fails to fetch. Otherwise, plain <a href> links that look like feeds are
    tried in order, and only one that actually parses as RSS/Atom is reported.
    """
    links = discover_feed_links(html, base_url)
    if not links:
        return _find_anchor_feed(html, base_url, timeout)

    feed_url = links[0]["href"]

    try:
        content = _fetch_feed_bytes(feed_url, timeout)
    except RuntimeError as exc:
        return FeedInfo(found=True, feed_url=feed_url, error=str(exc))

    title, items = parse_feed(content)
    if title is None and not items:
        return FeedInfo(
            found=True,
            feed_url=feed_url,
            error="Feed could not be parsed (empty or malformed).",
        )
    return FeedInfo(found=True, feed_url=feed_url, title=title, items=items)
