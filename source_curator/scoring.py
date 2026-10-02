"""Transparent, deterministic scoring for discovered candidate sources.

Every piece here is a small, pure, independently-testable function or
dataclass -- no AI, no randomness, no hidden inputs. The output is a
prioritization heuristic ("curation score"), not a claim that a source is
trustworthy or authoritative.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

# --- Relevance ---------------------------------------------------------

_WORD_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> set[str]:
    """Lower-case word tokens from `text`, for simple overlap matching."""
    return set(_WORD_RE.findall((text or "").lower()))


@dataclass
class RelevanceResult:
    """Whether the query's words showed up in each of three signal fields."""

    matched_search_title: bool
    matched_snippet: bool
    matched_domain_or_page_title: bool

    @property
    def hit_count(self) -> int:
        return sum((self.matched_search_title, self.matched_snippet, self.matched_domain_or_page_title))

    @property
    def score(self) -> float:
        """0.0-1.0: fraction of the three signal fields that matched."""
        return self.hit_count / 3


def compute_relevance(
    query: str,
    *,
    search_title: str,
    snippet: str | None,
    domain: str,
    page_title: str | None,
) -> RelevanceResult:
    """Deterministic relevance: does any query word appear in the search
    result's title, its snippet, or the candidate's domain/page title?"""
    terms = tokenize(query)
    if not terms:
        return RelevanceResult(False, False, False)

    return RelevanceResult(
        matched_search_title=bool(terms & tokenize(search_title)),
        matched_snippet=bool(terms & tokenize(snippet or "")),
        matched_domain_or_page_title=bool(terms & (tokenize(domain) | tokenize(page_title or ""))),
    )


def relevance_label(relevance: RelevanceResult) -> str:
    hits = relevance.hit_count
    if hits == 3:
        return "High"
    if hits == 2:
        return "Medium"
    if hits == 1:
        return "Low"
    return "None"


# --- Freshness -----------------------------------------------------------

RECENT_DAYS = 30
ACTIVE_DAYS = 90

FRESH_RECENT = "Recent"
FRESH_ACTIVE = "Active"
FRESH_OLD = "Old"
FRESH_UNKNOWN = "Unknown"


def parse_feed_date(raw: str | None) -> datetime | None:
    """Normalize a feed item's date string to a UTC-aware datetime.

    Understands RSS's RFC 822/2822 `pubDate` ("Wed, 01 Oct 2025 12:00:00 GMT")
    and Atom's ISO 8601 `updated` ("2025-10-01T12:00:00Z"). Returns None for
    anything else -- malformed or unrecognized dates are never an error.
    """
    if not raw:
        return None
    raw = raw.strip()

    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        parsed = None
    if parsed is not None:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def newest_feed_date(published_dates: list[str | None]) -> datetime | None:
    """The most recent parseable date among a feed's item date strings."""
    parsed = [d for d in (parse_feed_date(raw) for raw in published_dates) if d is not None]
    return max(parsed) if parsed else None


def classify_freshness(newest_item_date: datetime | None, now: datetime | None = None) -> str:
    """Recent (<=30d), Active (<=90d), Old (>90d), or Unknown (no usable date)."""
    if newest_item_date is None:
        return FRESH_UNKNOWN
    now = now or datetime.now(timezone.utc)
    age_days = max((now - newest_item_date).days, 0)
    if age_days <= RECENT_DAYS:
        return FRESH_RECENT
    if age_days <= ACTIVE_DAYS:
        return FRESH_ACTIVE
    return FRESH_OLD


# --- Curation score --------------------------------------------------------

MAX_RELEVANCE_POINTS = 40
REACHABLE_POINTS = 20
FEED_FOUND_POINTS = 15
FEED_DETECTED_BUT_UNREACHABLE_POINTS = 5
CONTENT_TYPE_POINTS = 10
FRESHNESS_POINTS = {
    FRESH_RECENT: 15,
    FRESH_ACTIVE: 8,
    FRESH_OLD: 2,
    FRESH_UNKNOWN: 0,
}
MAX_SCORE = (
    MAX_RELEVANCE_POINTS + REACHABLE_POINTS + FEED_FOUND_POINTS + CONTENT_TYPE_POINTS + max(FRESHNESS_POINTS.values())
)
assert MAX_SCORE == 100  # the whole point of this module is a 0-100 scale

_FRESHNESS_REASONS = {
    FRESH_RECENT: "recent feed activity",
    FRESH_ACTIVE: "moderately active feed",
    FRESH_OLD: "stale feed activity",
}


@dataclass
class ScoreBreakdown:
    """Every point contribution, plus the plain-language reasons behind them."""

    relevance_points: float
    reachable_points: int
    feed_points: int
    freshness_points: int
    content_type_points: int
    total: int
    reasons: list[str]

    @property
    def explanation(self) -> str:
        return "; ".join(self.reasons) + "."


def score_candidate(
    *,
    relevance: RelevanceResult,
    reachable: bool,
    status_code: int | None,
    feed_found: bool,
    feed_error: str | None,
    freshness: str,
    content_type: str | None,
    unreachable_reason: str | None = None,
) -> ScoreBreakdown:
    """Combine transparent factors into one 0-100 curation score.

    This is a prioritization heuristic for *where to look first*, not a
    trust/authority judgment -- it only reflects whether the page was
    reachable, relevant-looking, offers a feed, looks recently active, and
    returned a machine-readable content type.
    """
    reasons: list[str] = []

    relevance_points = relevance.score * MAX_RELEVANCE_POINTS
    label = relevance_label(relevance)
    reasons.append("No query relevance detected" if label == "None" else f"{label} query relevance")

    valid_response = reachable and status_code is not None and 200 <= status_code < 400
    if valid_response:
        reachable_points = REACHABLE_POINTS
        reasons.append("reachable")
    elif reachable:
        reachable_points = 0
        reasons.append(f"reachable but returned status {status_code}")
    else:
        reachable_points = 0
        reasons.append(unreachable_reason or "unreachable")

    if feed_found and not feed_error:
        feed_points = FEED_FOUND_POINTS
        reasons.append("RSS feed detected")
    elif feed_found:
        feed_points = FEED_DETECTED_BUT_UNREACHABLE_POINTS
        reasons.append("feed link found but could not be fetched")
    else:
        feed_points = 0
        reasons.append("no feed detected")

    freshness_points = FRESHNESS_POINTS.get(freshness, 0)
    if freshness in _FRESHNESS_REASONS:
        reasons.append(_FRESHNESS_REASONS[freshness])

    if reachable:
        is_machine_readable = bool(content_type) and "html" in content_type.lower()
        content_type_points = CONTENT_TYPE_POINTS if is_machine_readable else 0
        if not is_machine_readable:
            reasons.append("content type unknown" if not content_type else "non-HTML content type")
    else:
        content_type_points = 0

    total = round(relevance_points + reachable_points + feed_points + freshness_points + content_type_points)
    total = max(0, min(100, total))

    return ScoreBreakdown(
        relevance_points=round(relevance_points, 1),
        reachable_points=reachable_points,
        feed_points=feed_points,
        freshness_points=freshness_points,
        content_type_points=content_type_points,
        total=total,
        reasons=reasons,
    )
