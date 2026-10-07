"""Deterministic technical health checks for a saved source.

"Health" here means technical availability and feed freshness -- whether the
page responds and whether its feed (if any) is reachable and recently
updated. It is NOT a trust, quality, or credibility judgment: a perfectly
healthy source can still be unreliable, and a temporarily-broken one isn't
necessarily a bad source.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .inspector import DEFAULT_TIMEOUT, InspectionError
from .inspector import inspect as inspect_url
from .scoring import FRESH_OLD, classify_freshness, newest_feed_date

HEALTH_HEALTHY = "healthy"
HEALTH_STALE = "stale"
HEALTH_BROKEN = "broken"


def classify_health(
    *, reachable: bool, feed_found: bool, feed_reachable: bool | None, freshness: str
) -> str:
    """BROKEN if the page itself is unreachable, or a feed was found but
    couldn't be fetched. STALE if reachable with a feed whose newest usable
    item is >90 days old (freshness == "Old"). HEALTHY otherwise -- reachable,
    with no obvious feed failure, and freshness Recent/Active/Unknown.
    """
    if not reachable:
        return HEALTH_BROKEN
    if feed_found and feed_reachable is False:
        return HEALTH_BROKEN
    if feed_found and freshness == FRESH_OLD:
        return HEALTH_STALE
    return HEALTH_HEALTHY


@dataclass
class HealthCheckResult:
    """Everything one `check`/`check-all` run learns about a source."""

    checked_at: str
    reachable: bool
    http_status: int | None
    final_url: str | None
    feed_found: bool
    feed_reachable: bool | None
    feed_url: str | None
    latest_feed_date: str | None
    freshness: str
    health_status: str
    error: str | None


def check_source(
    row, *, timeout: float = DEFAULT_TIMEOUT, inspect_fn=inspect_url
) -> HealthCheckResult:
    """Re-fetch a saved source's final_url and classify its current health.
    Never raises -- an unreachable page or broken feed is a result, not an
    exception, so one failure never has to stop a caller checking many rows.
    """
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    try:
        result = inspect_fn(row["final_url"], timeout=timeout)
    except InspectionError as exc:
        freshness = classify_freshness(None)
        return HealthCheckResult(
            checked_at=checked_at,
            reachable=False,
            http_status=None,
            final_url=None,
            feed_found=False,
            feed_reachable=None,
            feed_url=None,
            latest_feed_date=None,
            freshness=freshness,
            health_status=classify_health(
                reachable=False, feed_found=False, feed_reachable=None, freshness=freshness
            ),
            error=str(exc),
        )

    feed_found = result.feed.found
    feed_reachable = None
    feed_url = None
    latest_feed_date = None
    freshness = classify_freshness(None)
    error = None

    if feed_found:
        feed_url = result.feed.feed_url
        feed_reachable = result.feed.error is None
        if not feed_reachable:
            error = result.feed.error
        elif result.feed.items:
            newest = newest_feed_date([item.published for item in result.feed.items])
            freshness = classify_freshness(newest)
            if newest is not None:
                latest_feed_date = newest.isoformat()

    health_status = classify_health(
        reachable=True, feed_found=feed_found, feed_reachable=feed_reachable, freshness=freshness
    )

    return HealthCheckResult(
        checked_at=checked_at,
        reachable=True,
        http_status=result.status_code,
        final_url=result.final_url,
        feed_found=feed_found,
        feed_reachable=feed_reachable,
        feed_url=feed_url,
        latest_feed_date=latest_feed_date,
        freshness=freshness,
        health_status=health_status,
        error=error,
    )
