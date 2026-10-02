"""Discover candidate source websites for a research topic via web search.

Provider-specific code (currently `ddgs`) is isolated in `search()` so the
provider can be swapped out later without touching the CLI or the
candidate-cleanup logic below it.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from ddgs import DDGS
from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

from .library import normalize_url

DEFAULT_LIMIT = 20


class SearchError(Exception):
    """A web search could not be completed (bad input, network, rate limit, ...)."""


@dataclass
class SearchResult:
    """One raw search hit."""

    title: str
    url: str
    snippet: str | None
    domain: str


@dataclass
class Candidate:
    """One cleaned-up candidate source, ready to print."""

    rank: int
    title: str
    url: str
    domain: str
    snippet: str | None
    already_saved: bool


@dataclass
class DiscoveryStats:
    raw_count: int
    duplicate_count: int
    unique_domain_count: int


def search(query: str, limit: int = DEFAULT_LIMIT) -> list[SearchResult]:
    """Search the web for `query` and return up to `limit` raw results."""
    query = (query or "").strip()
    if not query:
        raise SearchError("No search topic given.")
    if limit <= 0:
        raise SearchError("--limit must be a positive integer.")

    try:
        raw_results = DDGS().text(query, max_results=limit)
    except RatelimitException as exc:
        raise SearchError(f"Search rate-limited, try again later: {exc}") from None
    except TimeoutException as exc:
        raise SearchError(f"Search timed out: {exc}") from None
    except DDGSException as exc:
        # ddgs raises this same exception (message "No results found.") when a
        # query genuinely has nothing to return, not just on real failures.
        # Treat that one case as zero results rather than an error.
        if "no results found" in str(exc).lower():
            return []
        raise SearchError(f"Web search failed: {exc}") from None
    except Exception as exc:
        # Defensive fallback for anything ddgs's own hierarchy doesn't cover
        # (e.g. a lower-level network exception) -- never crash the CLI.
        raise SearchError(f"Web search failed: {exc}") from None

    results = []
    for item in raw_results or []:
        url = (item.get("href") or "").strip()
        if not url:
            continue
        title = (item.get("title") or "").strip() or url
        snippet = (item.get("body") or "").strip() or None
        results.append(
            SearchResult(title=title, url=url, snippet=snippet, domain=urlparse(url).hostname or "")
        )
    return results


def dedupe_by_url(results: list[SearchResult]) -> tuple[list[SearchResult], int]:
    """Drop results whose normalized URL repeats an earlier one.

    Returns (deduped_results, number_removed).
    """
    seen: set[str] = set()
    deduped = []
    for result in results:
        key = normalize_url(result.url)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(result)
    return deduped, len(results) - len(deduped)


def group_by_domain(results: list[SearchResult]) -> list[SearchResult]:
    """Keep only the first result seen for each domain."""
    seen_domains: set[str] = set()
    grouped = []
    for result in results:
        if result.domain in seen_domains:
            continue
        seen_domains.add(result.domain)
        grouped.append(result)
    return grouped


def build_candidates(
    results: list[SearchResult], saved_domains: set[str] = frozenset()
) -> tuple[list[Candidate], DiscoveryStats]:
    """Clean up raw search results into a numbered candidate list plus stats.

    `saved_domains` is the set of domains already present in the source
    library, used only to mark candidates -- it never filters anything out.
    """
    raw_count = len(results)
    deduped, duplicate_count = dedupe_by_url(results)
    grouped = group_by_domain(deduped)

    candidates = [
        Candidate(
            rank=index,
            title=result.title,
            url=result.url,
            domain=result.domain,
            snippet=result.snippet,
            already_saved=result.domain in saved_domains,
        )
        for index, result in enumerate(grouped, start=1)
    ]

    stats = DiscoveryStats(
        raw_count=raw_count,
        duplicate_count=duplicate_count,
        unique_domain_count=len(candidates),
    )
    return candidates, stats
