"""Fetch a URL and report basic facts about the page that came back."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlparse

import requests

USER_AGENT = "source-curator-tool/0.1 (+https://example.invalid/source-curator-tool)"
DEFAULT_TIMEOUT = 10.0


class InspectionError(Exception):
    """A URL could not be inspected (bad URL, or the request failed)."""


@dataclass
class Inspection:
    """What we learned about a single URL."""

    requested_url: str
    final_url: str
    status_code: int
    domain: str
    content_type: str | None
    title: str | None


def validate_url(url: str) -> str:
    """Return the URL unchanged, or raise InspectionError if it is not HTTP(S)."""
    url = (url or "").strip()
    if not url:
        raise InspectionError("No URL given.")

    parts = urlparse(url)
    if parts.scheme not in ("http", "https"):
        raise InspectionError(
            f"Unsupported URL scheme {parts.scheme or '(none)'!r}: only http and https are allowed."
        )
    if not parts.netloc:
        raise InspectionError(f"URL is missing a host: {url!r}")
    return url


class _TitleParser(HTMLParser):
    """Collect the text of the first <title> element."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.chunks: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title" and self.title is None:
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title" and self._in_title:
            self._in_title = False
            self.title = "".join(self.chunks).strip()

    def handle_data(self, data):
        if self._in_title:
            self.chunks.append(data)


def extract_title(html: str) -> str | None:
    """Return the page title, or None if there isn't a usable one."""
    parser = _TitleParser()
    parser.feed(html)
    parser.close()
    # An unclosed <title> still leaves text worth reporting.
    title = parser.title or "".join(parser.chunks).strip()
    return " ".join(title.split()) or None


def _looks_like_html(content_type: str | None) -> bool:
    return content_type is None or "html" in content_type.lower()


def inspect(url: str, timeout: float = DEFAULT_TIMEOUT) -> Inspection:
    """Fetch `url` (following redirects) and summarize the response."""
    url = validate_url(url)
    headers = {"User-Agent": USER_AGENT}

    try:
        response = requests.get(
            url, headers=headers, timeout=timeout, allow_redirects=True
        )
    except requests.exceptions.Timeout:
        raise InspectionError(f"Request timed out after {timeout:g}s: {url}") from None
    except requests.exceptions.TooManyRedirects:
        raise InspectionError(f"Too many redirects: {url}") from None
    except requests.exceptions.ConnectionError:
        raise InspectionError(f"Could not connect to {url}") from None
    except requests.exceptions.RequestException as exc:
        raise InspectionError(f"Request failed for {url}: {exc}") from None

    final_url = response.url or url
    content_type = response.headers.get("Content-Type")

    title = None
    if _looks_like_html(content_type):
        title = extract_title(response.text)

    return Inspection(
        requested_url=url,
        final_url=final_url,
        status_code=response.status_code,
        domain=urlparse(final_url).hostname or "",
        content_type=content_type,
        title=title,
    )
