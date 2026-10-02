"""Detect an RSS/Atom feed linked from an HTML page and preview recent items."""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin

import feedparser
import requests

USER_AGENT = "source-curator-tool/0.1 (+https://example.invalid/source-curator-tool)"
DEFAULT_TIMEOUT = 10.0
MAX_ITEMS = 5

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


def parse_feed(content: bytes) -> tuple[str | None, list[FeedItem]]:
    """Parse feed bytes with feedparser. Never raises; returns (title, items)."""
    try:
        parsed = feedparser.parse(content)
    except Exception:
        # feedparser is documented not to raise on malformed XML, but a third-party
        # parser bug here must never crash the CLI.
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


def find_feed(html: str, base_url: str, timeout: float = DEFAULT_TIMEOUT) -> FeedInfo:
    """Look for a feed link in `html`; if one exists, fetch and preview it."""
    links = discover_feed_links(html, base_url)
    if not links:
        return FeedInfo(found=False)

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
