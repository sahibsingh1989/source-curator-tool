"""Tests for source_curator.evaluation. Both the search provider and the page
inspector are replaced with fakes -- no network in this suite."""

import unittest

from source_curator.discovery import Candidate, SearchError, SearchResult
from source_curator.evaluation import evaluate_candidates, run_evaluation
from source_curator.feeds import FeedInfo, FeedItem
from source_curator.inspector import Inspection, InspectionError


def make_inspection(
    *,
    url="https://example.com/",
    status_code=200,
    title="A Title",
    content_type="text/html; charset=utf-8",
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


def make_candidate(rank=1, title="A Title", url="https://example.com/", domain="example.com", snippet="A snippet"):
    return Candidate(rank=rank, title=title, url=url, domain=domain, snippet=snippet, already_saved=False)


class EvaluateCandidatesTests(unittest.TestCase):
    def test_sorts_best_candidate_first(self):
        good = make_candidate(title="Stablecoin Regulation News", url="https://good.example.com/", domain="good.example.com")
        bad = make_candidate(title="Unrelated", url="https://bad.example.com/", domain="bad.example.com")

        def fake_inspect(url, timeout):
            if "good" in url:
                return make_inspection(
                    url=url,
                    feed=FeedInfo(
                        found=True,
                        feed_url="https://good.example.com/feed",
                        items=[FeedItem(title="x", link="y", published="2026-10-01T00:00:00Z")],
                    ),
                )
            raise InspectionError(f"Could not connect to {url}")

        results = evaluate_candidates([bad, good], "stablecoin regulation", inspect_fn=fake_inspect)

        self.assertEqual(results[0].domain, "good.example.com")
        self.assertEqual(results[0].rank, 1)
        self.assertEqual(results[1].domain, "bad.example.com")
        self.assertEqual(results[1].rank, 2)
        self.assertGreater(results[0].score, results[1].score)

    def test_unreachable_candidate_is_included_with_low_score_and_reason(self):
        candidate = make_candidate(url="https://down.example.com/", domain="down.example.com")

        def fake_inspect(url, timeout):
            raise InspectionError(f"Could not connect to {url}")

        results = evaluate_candidates([candidate], "topic", inspect_fn=fake_inspect)

        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertFalse(item.reachable)
        self.assertIsNone(item.status_code)
        self.assertIn("Could not connect to https://down.example.com/", item.explanation)
        self.assertLess(item.score, 50)

    def test_one_broken_candidate_does_not_abort_the_rest(self):
        broken = make_candidate(url="https://broken.example.com/", domain="broken.example.com")
        fine = make_candidate(url="https://fine.example.com/", domain="fine.example.com")

        def fake_inspect(url, timeout):
            if "broken" in url:
                raise InspectionError("boom")
            return make_inspection(url=url)

        results = evaluate_candidates([broken, fine], "topic", inspect_fn=fake_inspect)

        self.assertEqual(len(results), 2)
        domains = {item.domain for item in results}
        self.assertEqual(domains, {"broken.example.com", "fine.example.com"})

    def test_candidate_with_feed_gets_marked_found(self):
        candidate = make_candidate()

        def fake_inspect(url, timeout):
            return make_inspection(
                url=url, feed=FeedInfo(found=True, feed_url="https://example.com/feed.xml")
            )

        [item] = evaluate_candidates([candidate], "topic", inspect_fn=fake_inspect)
        self.assertTrue(item.feed_found)
        self.assertEqual(item.feed_url, "https://example.com/feed.xml")

    def test_candidate_without_feed_has_no_feed_url(self):
        candidate = make_candidate()

        def fake_inspect(url, timeout):
            return make_inspection(url=url, feed=FeedInfo(found=False))

        [item] = evaluate_candidates([candidate], "topic", inspect_fn=fake_inspect)
        self.assertFalse(item.feed_found)
        self.assertIsNone(item.feed_url)

    def test_freshness_recent_from_feed_items(self):
        import datetime

        candidate = make_candidate()
        recent = datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")

        def fake_inspect(url, timeout):
            return make_inspection(
                url=url,
                feed=FeedInfo(
                    found=True,
                    feed_url="https://example.com/feed",
                    items=[FeedItem(title="x", link="y", published=recent)],
                ),
            )

        [item] = evaluate_candidates([candidate], "topic", inspect_fn=fake_inspect)
        self.assertEqual(item.freshness, "Recent")

    def test_freshness_old_from_feed_items(self):
        candidate = make_candidate()

        def fake_inspect(url, timeout):
            return make_inspection(
                url=url,
                feed=FeedInfo(
                    found=True,
                    feed_url="https://example.com/feed",
                    items=[FeedItem(title="x", link="y", published="Wed, 01 Jan 2020 00:00:00 GMT")],
                ),
            )

        [item] = evaluate_candidates([candidate], "topic", inspect_fn=fake_inspect)
        self.assertEqual(item.freshness, "Old")

    def test_freshness_unknown_with_malformed_dates_does_not_crash(self):
        candidate = make_candidate()

        def fake_inspect(url, timeout):
            return make_inspection(
                url=url,
                feed=FeedInfo(
                    found=True,
                    feed_url="https://example.com/feed",
                    items=[FeedItem(title="x", link="y", published="not a real date")],
                ),
            )

        [item] = evaluate_candidates([candidate], "topic", inspect_fn=fake_inspect)
        self.assertEqual(item.freshness, "Unknown")

    def test_freshness_unknown_when_no_feed(self):
        candidate = make_candidate()

        def fake_inspect(url, timeout):
            return make_inspection(url=url, feed=FeedInfo(found=False))

        [item] = evaluate_candidates([candidate], "topic", inspect_fn=fake_inspect)
        self.assertEqual(item.freshness, "Unknown")

    def test_already_saved_domain_is_marked(self):
        saved = make_candidate(url="https://saved.example.com/", domain="saved.example.com")
        fresh = make_candidate(url="https://fresh.example.com/", domain="fresh.example.com")

        def fake_inspect(url, timeout):
            return make_inspection(url=url)

        results = evaluate_candidates(
            [saved, fresh], "topic", saved_domains={"saved.example.com"}, inspect_fn=fake_inspect
        )

        by_domain = {item.domain: item for item in results}
        self.assertTrue(by_domain["saved.example.com"].already_saved)
        self.assertFalse(by_domain["fresh.example.com"].already_saved)

    def test_empty_candidate_list(self):
        self.assertEqual(evaluate_candidates([], "topic", inspect_fn=lambda u, timeout: None), [])


class RunEvaluationTests(unittest.TestCase):
    def test_zero_search_results_returns_empty_without_inspecting(self):
        calls = []

        def fake_inspect(url, timeout):
            calls.append(url)
            return make_inspection(url=url)

        evaluated, stats = run_evaluation(
            "topic", search_fn=lambda q, limit: [], inspect_fn=fake_inspect
        )

        self.assertEqual(evaluated, [])
        self.assertEqual(stats.raw_count, 0)
        self.assertEqual(calls, [])

    def test_search_error_propagates_to_caller(self):
        def failing_search(query, limit):
            raise SearchError("rate limited")

        with self.assertRaises(SearchError):
            run_evaluation("topic", search_fn=failing_search, inspect_fn=lambda u, timeout: None)

    def test_never_inspects_more_than_the_limit(self):
        raw = [
            SearchResult(f"Title {i}", f"https://site{i}.example.com/", "snippet", f"site{i}.example.com")
            for i in range(8)
        ]
        inspected_urls = []

        def fake_inspect(url, timeout):
            inspected_urls.append(url)
            return make_inspection(url=url)

        evaluated, stats = run_evaluation(
            "topic", limit=3, search_fn=lambda q, limit: raw[:limit], inspect_fn=fake_inspect
        )

        self.assertLessEqual(len(evaluated), 3)
        self.assertLessEqual(len(inspected_urls), 3)

    def test_passes_timeout_through_to_inspect(self):
        raw = [SearchResult("A", "https://example.com/", "s", "example.com")]
        seen_timeouts = []

        def fake_inspect(url, timeout):
            seen_timeouts.append(timeout)
            return make_inspection(url=url)

        run_evaluation("topic", timeout=3.5, search_fn=lambda q, limit: raw, inspect_fn=fake_inspect)

        self.assertEqual(seen_timeouts, [3.5])


if __name__ == "__main__":
    unittest.main()
