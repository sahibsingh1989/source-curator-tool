"""Discover candidate sources for a topic, inspect each one, and rank them.

Orchestration only: web search comes from `discovery`, page/feed fetching from
`inspector`/`feeds` (via `inspector.inspect`), and the actual scoring math from
`scoring`. One candidate's inspection failing never aborts the rest -- it's
recorded and scored low with a clear reason instead.
"""

from __future__ import annotations

from dataclasses import dataclass

from .discovery import Candidate, DiscoveryStats, build_candidates
from .discovery import search as discovery_search
from .inspector import DEFAULT_TIMEOUT, InspectionError
from .inspector import inspect as inspect_url
from .scoring import classify_freshness, compute_relevance, newest_feed_date, relevance_label, score_candidate

DEFAULT_EVALUATE_LIMIT = 10


@dataclass
class EvaluatedCandidate:
    """One candidate, inspected and scored."""

    rank: int
    title: str
    domain: str
    url: str
    score: int
    relevance_label: str
    reachable: bool
    status_code: int | None
    feed_found: bool
    feed_url: str | None
    freshness: str
    already_saved: bool
    explanation: str


def evaluate_candidates(
    candidates: list[Candidate],
    query: str,
    *,
    saved_domains: set[str] = frozenset(),
    timeout: float = DEFAULT_TIMEOUT,
    inspect_fn=inspect_url,
) -> list[EvaluatedCandidate]:
    """Inspect and score each candidate, then return them sorted best-first."""
    scored: list[EvaluatedCandidate] = []

    for candidate in candidates:
        reachable = False
        status_code = None
        page_title = None
        content_type = None
        feed_found = False
        feed_url = None
        feed_error = None
        freshness = "Unknown"
        unreachable_reason = None

        try:
            result = inspect_fn(candidate.url, timeout=timeout)
        except InspectionError as exc:
            unreachable_reason = str(exc)
        else:
            reachable = True
            status_code = result.status_code
            page_title = result.title
            content_type = result.content_type
            feed_found = result.feed.found
            feed_error = result.feed.error
            if feed_found:
                feed_url = result.feed.feed_url
                if result.feed.items:
                    newest = newest_feed_date([item.published for item in result.feed.items])
                    freshness = classify_freshness(newest)

        relevance = compute_relevance(
            query,
            search_title=candidate.title,
            snippet=candidate.snippet,
            domain=candidate.domain,
            page_title=page_title,
        )

        breakdown = score_candidate(
            relevance=relevance,
            reachable=reachable,
            status_code=status_code,
            feed_found=feed_found,
            feed_error=feed_error,
            freshness=freshness,
            content_type=content_type,
            unreachable_reason=unreachable_reason,
        )

        scored.append(
            EvaluatedCandidate(
                rank=0,
                title=candidate.title,
                domain=candidate.domain,
                url=candidate.url,
                score=breakdown.total,
                relevance_label=relevance_label(relevance),
                reachable=reachable,
                status_code=status_code,
                feed_found=feed_found,
                feed_url=feed_url,
                freshness=freshness,
                already_saved=candidate.domain in saved_domains,
                explanation=breakdown.explanation,
            )
        )

    scored.sort(key=lambda item: item.score, reverse=True)
    for index, item in enumerate(scored, start=1):
        item.rank = index
    return scored


def run_evaluation(
    query: str,
    *,
    limit: int = DEFAULT_EVALUATE_LIMIT,
    saved_domains: set[str] = frozenset(),
    timeout: float = DEFAULT_TIMEOUT,
    search_fn=discovery_search,
    inspect_fn=inspect_url,
) -> tuple[list[EvaluatedCandidate], DiscoveryStats]:
    """Discover up to `limit` unique-domain candidates for `query`, inspect and
    score each, and return them sorted best-first along with discovery stats.
    """
    raw_results = search_fn(query, limit=limit)
    if not raw_results:
        return [], DiscoveryStats(raw_count=0, duplicate_count=0, unique_domain_count=0)

    candidates, stats = build_candidates(raw_results, saved_domains)
    # Never inspect more than the requested limit, even if build_candidates
    # somehow returned more (it can't today, since raw_results is already
    # capped at `limit`, but this keeps the contract explicit either way).
    candidates = candidates[:limit]

    evaluated = evaluate_candidates(
        candidates, query, saved_domains=saved_domains, timeout=timeout, inspect_fn=inspect_fn
    )
    return evaluated, stats
