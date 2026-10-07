"""Tests for source_curator.health. The page inspector is always a fake
function passed in -- no network in this suite."""

import unittest

from source_curator.feeds import FeedInfo, FeedItem
from source_curator.inspector import Inspection, InspectionError
from source_curator.health import (
    HEALTH_BROKEN,
    HEALTH_HEALTHY,
    HEALTH_STALE,
    check_source,
    classify_health,
)
from source_curator.scoring import FRESH_ACTIVE, FRESH_OLD, FRESH_RECENT, FRESH_UNKNOWN


def make_row(final_url="https://example.com/", title="Example", domain="example.com"):
    return {"final_url": final_url, "title": title, "domain": domain}


def make_inspection(
    *,
    url="https://example.com/",
    status_code=200,
    title="Example",
    content_type="text/html",
    feed=None,
):
    return Inspection(
        requested_url=url,
        final_url=url,
        status_code=status_code,
        domain="example.com",
        content_type=content_type,
        title=title,
        feed=feed or FeedInfo(found=False),
    )


class ClassifyHealthTests(unittest.TestCase):
    def test_unreachable_is_broken(self):
        self.assertEqual(
            classify_health(reachable=False, feed_found=False, feed_reachable=None, freshness=FRESH_UNKNOWN),
            HEALTH_BROKEN,
        )

    def test_unreachable_overrides_everything_else(self):
        self.assertEqual(
            classify_health(reachable=False, feed_found=True, feed_reachable=True, freshness=FRESH_RECENT),
            HEALTH_BROKEN,
        )

    def test_feed_found_but_unreachable_is_broken(self):
        self.assertEqual(
            classify_health(reachable=True, feed_found=True, feed_reachable=False, freshness=FRESH_UNKNOWN),
            HEALTH_BROKEN,
        )

    def test_reachable_no_feed_is_healthy(self):
        self.assertEqual(
            classify_health(reachable=True, feed_found=False, feed_reachable=None, freshness=FRESH_UNKNOWN),
            HEALTH_HEALTHY,
        )

    def test_reachable_feed_recent_is_healthy(self):
        self.assertEqual(
            classify_health(reachable=True, feed_found=True, feed_reachable=True, freshness=FRESH_RECENT),
            HEALTH_HEALTHY,
        )

    def test_reachable_feed_active_is_healthy(self):
        self.assertEqual(
            classify_health(reachable=True, feed_found=True, feed_reachable=True, freshness=FRESH_ACTIVE),
            HEALTH_HEALTHY,
        )

    def test_reachable_feed_unknown_freshness_is_healthy(self):
        self.assertEqual(
            classify_health(reachable=True, feed_found=True, feed_reachable=True, freshness=FRESH_UNKNOWN),
            HEALTH_HEALTHY,
        )

    def test_reachable_feed_old_is_stale(self):
        self.assertEqual(
            classify_health(reachable=True, feed_found=True, feed_reachable=True, freshness=FRESH_OLD),
            HEALTH_STALE,
        )

    def test_old_freshness_without_a_feed_is_still_healthy(self):
        # freshness shouldn't be "Old" without a feed in practice, but the
        # classifier must still behave sanely if it somehow is.
        self.assertEqual(
            classify_health(reachable=True, feed_found=False, feed_reachable=None, freshness=FRESH_OLD),
            HEALTH_HEALTHY,
        )


class CheckSourceTests(unittest.TestCase):
    def test_healthy_source_no_feed(self):
        row = make_row()

        def fake_inspect(url, timeout):
            return make_inspection(url=url, feed=FeedInfo(found=False))

        result = check_source(row, inspect_fn=fake_inspect)

        self.assertTrue(result.reachable)
        self.assertEqual(result.http_status, 200)
        self.assertFalse(result.feed_found)
        self.assertIsNone(result.feed_reachable)
        self.assertEqual(result.freshness, FRESH_UNKNOWN)
        self.assertEqual(result.health_status, HEALTH_HEALTHY)
        self.assertIsNone(result.error)
        self.assertTrue(result.checked_at)

    def test_healthy_source_with_recent_feed(self):
        row = make_row()

        def fake_inspect(url, timeout):
            return make_inspection(
                url=url,
                feed=FeedInfo(
                    found=True,
                    feed_url="https://example.com/feed",
                    items=[FeedItem(title="x", link="y", published="2026-10-06T00:00:00Z")],
                ),
            )

        result = check_source(row, inspect_fn=fake_inspect)

        self.assertTrue(result.feed_found)
        self.assertTrue(result.feed_reachable)
        self.assertEqual(result.freshness, FRESH_RECENT)
        self.assertEqual(result.health_status, HEALTH_HEALTHY)
        self.assertEqual(result.latest_feed_date, "2026-10-06T00:00:00+00:00")

    def test_stale_source_with_old_feed(self):
        row = make_row()

        def fake_inspect(url, timeout):
            return make_inspection(
                url=url,
                feed=FeedInfo(
                    found=True,
                    feed_url="https://example.com/feed",
                    items=[FeedItem(title="x", link="y", published="Wed, 01 Jan 2020 00:00:00 GMT")],
                ),
            )

        result = check_source(row, inspect_fn=fake_inspect)

        self.assertEqual(result.freshness, FRESH_OLD)
        self.assertEqual(result.health_status, HEALTH_STALE)

    def test_broken_source_unreachable_page(self):
        row = make_row()

        def fake_inspect(url, timeout):
            raise InspectionError(f"Could not connect to {url}")

        result = check_source(row, inspect_fn=fake_inspect)

        self.assertFalse(result.reachable)
        self.assertIsNone(result.http_status)
        self.assertIsNone(result.final_url)
        self.assertFalse(result.feed_found)
        self.assertEqual(result.health_status, HEALTH_BROKEN)
        self.assertIn("Could not connect", result.error)

    def test_broken_source_feed_fetch_failure(self):
        row = make_row()

        def fake_inspect(url, timeout):
            return make_inspection(
                url=url,
                feed=FeedInfo(
                    found=True,
                    feed_url="https://example.com/feed",
                    error="Could not connect to feed URL",
                ),
            )

        result = check_source(row, inspect_fn=fake_inspect)

        self.assertTrue(result.reachable)
        self.assertTrue(result.feed_found)
        self.assertFalse(result.feed_reachable)
        self.assertEqual(result.health_status, HEALTH_BROKEN)
        self.assertEqual(result.error, "Could not connect to feed URL")

    def test_malformed_feed_dates_do_not_crash_and_yield_unknown_freshness(self):
        row = make_row()

        def fake_inspect(url, timeout):
            return make_inspection(
                url=url,
                feed=FeedInfo(
                    found=True,
                    feed_url="https://example.com/feed",
                    items=[FeedItem(title="x", link="y", published="not a real date")],
                ),
            )

        result = check_source(row, inspect_fn=fake_inspect)

        self.assertTrue(result.feed_reachable)
        self.assertEqual(result.freshness, FRESH_UNKNOWN)
        self.assertEqual(result.health_status, HEALTH_HEALTHY)
        self.assertIsNone(result.latest_feed_date)

    def test_feed_found_with_no_items_is_unknown_freshness(self):
        row = make_row()

        def fake_inspect(url, timeout):
            return make_inspection(
                url=url, feed=FeedInfo(found=True, feed_url="https://example.com/feed", items=[])
            )

        result = check_source(row, inspect_fn=fake_inspect)

        self.assertEqual(result.freshness, FRESH_UNKNOWN)
        self.assertEqual(result.health_status, HEALTH_HEALTHY)

    def test_passes_timeout_through(self):
        row = make_row()
        seen = []

        def fake_inspect(url, timeout):
            seen.append(timeout)
            return make_inspection(url=url)

        check_source(row, timeout=3.5, inspect_fn=fake_inspect)

        self.assertEqual(seen, [3.5])

    def test_checks_the_stored_final_url(self):
        row = make_row(final_url="https://saved.example.com/path")
        seen = []

        def fake_inspect(url, timeout):
            seen.append(url)
            return make_inspection(url=url)

        check_source(row, inspect_fn=fake_inspect)

        self.assertEqual(seen, ["https://saved.example.com/path"])


if __name__ == "__main__":
    unittest.main()
