"""Tests for source_curator.scoring. Pure functions, no network/DB involved."""

import unittest
from datetime import datetime, timezone

from source_curator.scoring import (
    FRESH_ACTIVE,
    FRESH_OLD,
    FRESH_RECENT,
    FRESH_UNKNOWN,
    RelevanceResult,
    classify_freshness,
    compute_relevance,
    newest_feed_date,
    parse_feed_date,
    relevance_label,
    score_candidate,
    tokenize,
)


class TokenizeTests(unittest.TestCase):
    def test_lowercases_and_splits_on_punctuation(self):
        self.assertEqual(tokenize("Stablecoin Regulation!"), {"stablecoin", "regulation"})

    def test_empty_text(self):
        self.assertEqual(tokenize(""), set())
        self.assertEqual(tokenize(None), set())


class ComputeRelevanceTests(unittest.TestCase):
    def test_matches_each_field_independently(self):
        relevance = compute_relevance(
            "stablecoin regulation",
            search_title="Stablecoin News",
            snippet="unrelated snippet text",
            domain="example.com",
            page_title=None,
        )
        self.assertTrue(relevance.matched_search_title)
        self.assertFalse(relevance.matched_snippet)
        self.assertFalse(relevance.matched_domain_or_page_title)
        self.assertEqual(relevance.hit_count, 1)

    def test_all_three_fields_match(self):
        relevance = compute_relevance(
            "stablecoin regulation",
            search_title="Stablecoin Regulation Guide",
            snippet="All about stablecoin regulation",
            domain="stablecoin-regulation.example.com",
            page_title=None,
        )
        self.assertEqual(relevance.hit_count, 3)
        self.assertEqual(relevance.score, 1.0)

    def test_page_title_counts_toward_domain_field(self):
        relevance = compute_relevance(
            "stablecoin",
            search_title="unrelated",
            snippet=None,
            domain="example.com",
            page_title="All about Stablecoin News",
        )
        self.assertTrue(relevance.matched_domain_or_page_title)

    def test_no_match_anywhere(self):
        relevance = compute_relevance(
            "stablecoin regulation",
            search_title="Cooking recipes",
            snippet="How to bake bread",
            domain="recipes.example.com",
            page_title="Bread Recipes",
        )
        self.assertEqual(relevance.hit_count, 0)
        self.assertEqual(relevance.score, 0.0)

    def test_empty_query_never_matches(self):
        relevance = compute_relevance(
            "", search_title="Anything", snippet="Anything", domain="example.com", page_title="Anything"
        )
        self.assertEqual(relevance.hit_count, 0)

    def test_matching_is_case_insensitive(self):
        relevance = compute_relevance(
            "STABLECOIN", search_title="stablecoin news", snippet=None, domain="example.com", page_title=None
        )
        self.assertTrue(relevance.matched_search_title)


class RelevanceLabelTests(unittest.TestCase):
    def test_labels_by_hit_count(self):
        self.assertEqual(relevance_label(RelevanceResult(False, False, False)), "None")
        self.assertEqual(relevance_label(RelevanceResult(True, False, False)), "Low")
        self.assertEqual(relevance_label(RelevanceResult(True, True, False)), "Medium")
        self.assertEqual(relevance_label(RelevanceResult(True, True, True)), "High")


class ParseFeedDateTests(unittest.TestCase):
    def test_parses_rfc822_rss_date(self):
        dt = parse_feed_date("Wed, 01 Oct 2025 12:00:00 GMT")
        self.assertEqual(dt, datetime(2025, 10, 1, 12, 0, 0, tzinfo=timezone.utc))

    def test_parses_iso8601_atom_date_with_z(self):
        dt = parse_feed_date("2025-10-01T12:00:00Z")
        self.assertEqual(dt, datetime(2025, 10, 1, 12, 0, 0, tzinfo=timezone.utc))

    def test_parses_iso8601_with_offset_converts_to_utc(self):
        dt = parse_feed_date("2025-10-01T12:00:00+05:00")
        self.assertEqual(dt, datetime(2025, 10, 1, 7, 0, 0, tzinfo=timezone.utc))

    def test_naive_rfc822_date_assumed_utc(self):
        dt = parse_feed_date("Wed, 01 Oct 2025 12:00:00")
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_malformed_date_returns_none_without_raising(self):
        self.assertIsNone(parse_feed_date("not a date at all"))
        self.assertIsNone(parse_feed_date("Garbled, 99 Blah 2025 99:99:99 ZZZ"))
        self.assertIsNone(parse_feed_date(""))
        self.assertIsNone(parse_feed_date(None))


class NewestFeedDateTests(unittest.TestCase):
    def test_picks_the_latest_parseable_date(self):
        newest = newest_feed_date(
            ["Wed, 01 Oct 2025 12:00:00 GMT", "2025-10-05T00:00:00Z", "2025-09-01T00:00:00Z"]
        )
        self.assertEqual(newest, datetime(2025, 10, 5, tzinfo=timezone.utc))

    def test_ignores_malformed_entries(self):
        newest = newest_feed_date(["garbage", None, "2025-10-05T00:00:00Z"])
        self.assertEqual(newest, datetime(2025, 10, 5, tzinfo=timezone.utc))

    def test_all_malformed_returns_none(self):
        self.assertIsNone(newest_feed_date(["garbage", None, ""]))

    def test_empty_list_returns_none(self):
        self.assertIsNone(newest_feed_date([]))


class ClassifyFreshnessTests(unittest.TestCase):
    NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)

    def test_recent_within_30_days(self):
        self.assertEqual(
            classify_freshness(datetime(2026, 9, 20, tzinfo=timezone.utc), now=self.NOW), FRESH_RECENT
        )

    def test_exactly_30_days_is_recent(self):
        self.assertEqual(
            classify_freshness(datetime(2026, 9, 2, tzinfo=timezone.utc), now=self.NOW), FRESH_RECENT
        )

    def test_active_between_30_and_90_days(self):
        self.assertEqual(
            classify_freshness(datetime(2026, 7, 20, tzinfo=timezone.utc), now=self.NOW), FRESH_ACTIVE
        )

    def test_old_beyond_90_days(self):
        self.assertEqual(
            classify_freshness(datetime(2026, 1, 1, tzinfo=timezone.utc), now=self.NOW), FRESH_OLD
        )

    def test_unknown_when_no_date(self):
        self.assertEqual(classify_freshness(None, now=self.NOW), FRESH_UNKNOWN)

    def test_future_dated_item_treated_as_recent_not_negative(self):
        self.assertEqual(
            classify_freshness(datetime(2026, 10, 10, tzinfo=timezone.utc), now=self.NOW), FRESH_RECENT
        )


class ScoreCandidateTests(unittest.TestCase):
    HIGH_RELEVANCE = RelevanceResult(True, True, True)
    NO_RELEVANCE = RelevanceResult(False, False, False)

    def test_best_case_scores_100_with_expected_explanation(self):
        breakdown = score_candidate(
            relevance=self.HIGH_RELEVANCE,
            reachable=True,
            status_code=200,
            feed_found=True,
            feed_error=None,
            freshness=FRESH_RECENT,
            content_type="text/html; charset=utf-8",
        )
        self.assertEqual(breakdown.total, 100)
        self.assertEqual(
            breakdown.explanation,
            "High query relevance; reachable; RSS feed detected; recent feed activity.",
        )

    def test_worst_case_scores_zero_with_clear_reason(self):
        breakdown = score_candidate(
            relevance=self.NO_RELEVANCE,
            reachable=False,
            status_code=None,
            feed_found=False,
            feed_error=None,
            freshness=FRESH_UNKNOWN,
            content_type=None,
            unreachable_reason="Could not connect to https://nope.example.com",
        )
        self.assertEqual(breakdown.total, 0)
        self.assertIn("Could not connect to https://nope.example.com", breakdown.explanation)
        self.assertIn("No query relevance detected", breakdown.explanation)
        self.assertIn("no feed detected", breakdown.explanation)

    def test_unreachable_without_explicit_reason_falls_back_to_generic_text(self):
        breakdown = score_candidate(
            relevance=self.NO_RELEVANCE,
            reachable=False,
            status_code=None,
            feed_found=False,
            feed_error=None,
            freshness=FRESH_UNKNOWN,
            content_type=None,
        )
        self.assertIn("unreachable", breakdown.explanation)

    def test_reachable_with_bad_status_gets_no_reachable_points(self):
        breakdown = score_candidate(
            relevance=self.NO_RELEVANCE,
            reachable=True,
            status_code=404,
            feed_found=False,
            feed_error=None,
            freshness=FRESH_UNKNOWN,
            content_type="text/html",
        )
        self.assertEqual(breakdown.reachable_points, 0)
        self.assertIn("status 404", breakdown.explanation)

    def test_feed_found_but_unreachable_gets_partial_credit(self):
        full = score_candidate(
            relevance=self.NO_RELEVANCE,
            reachable=True,
            status_code=200,
            feed_found=True,
            feed_error=None,
            freshness=FRESH_UNKNOWN,
            content_type="text/html",
        )
        partial = score_candidate(
            relevance=self.NO_RELEVANCE,
            reachable=True,
            status_code=200,
            feed_found=True,
            feed_error="Could not connect to feed URL",
            freshness=FRESH_UNKNOWN,
            content_type="text/html",
        )
        none_ = score_candidate(
            relevance=self.NO_RELEVANCE,
            reachable=True,
            status_code=200,
            feed_found=False,
            feed_error=None,
            freshness=FRESH_UNKNOWN,
            content_type="text/html",
        )
        self.assertGreater(full.feed_points, partial.feed_points)
        self.assertGreater(partial.feed_points, none_.feed_points)
        self.assertIn("could not be fetched", partial.explanation)
        self.assertIn("no feed detected", none_.explanation)

    def test_freshness_contributes_monotonically(self):
        def points_for(freshness):
            return score_candidate(
                relevance=self.NO_RELEVANCE,
                reachable=True,
                status_code=200,
                feed_found=True,
                feed_error=None,
                freshness=freshness,
                content_type="text/html",
            ).freshness_points

        self.assertGreater(points_for(FRESH_RECENT), points_for(FRESH_ACTIVE))
        self.assertGreater(points_for(FRESH_ACTIVE), points_for(FRESH_OLD))
        self.assertGreater(points_for(FRESH_OLD), points_for(FRESH_UNKNOWN))

    def test_non_html_content_type_gets_no_content_type_points(self):
        html = score_candidate(
            relevance=self.NO_RELEVANCE,
            reachable=True,
            status_code=200,
            feed_found=False,
            feed_error=None,
            freshness=FRESH_UNKNOWN,
            content_type="text/html",
        )
        pdf = score_candidate(
            relevance=self.NO_RELEVANCE,
            reachable=True,
            status_code=200,
            feed_found=False,
            feed_error=None,
            freshness=FRESH_UNKNOWN,
            content_type="application/pdf",
        )
        self.assertGreater(html.content_type_points, pdf.content_type_points)
        self.assertIn("non-HTML content type", pdf.explanation)

    def test_unreachable_never_awards_content_type_points(self):
        breakdown = score_candidate(
            relevance=self.NO_RELEVANCE,
            reachable=False,
            status_code=None,
            feed_found=False,
            feed_error=None,
            freshness=FRESH_UNKNOWN,
            content_type="text/html",  # should be impossible in practice, but must not score
        )
        self.assertEqual(breakdown.content_type_points, 0)

    def test_score_is_deterministic_for_same_inputs(self):
        kwargs = dict(
            relevance=self.HIGH_RELEVANCE,
            reachable=True,
            status_code=200,
            feed_found=True,
            feed_error=None,
            freshness=FRESH_ACTIVE,
            content_type="text/html",
        )
        first = score_candidate(**kwargs)
        second = score_candidate(**kwargs)
        self.assertEqual(first.total, second.total)
        self.assertEqual(first.explanation, second.explanation)

    def test_score_is_always_within_0_to_100(self):
        breakdown = score_candidate(
            relevance=self.HIGH_RELEVANCE,
            reachable=True,
            status_code=200,
            feed_found=True,
            feed_error=None,
            freshness=FRESH_RECENT,
            content_type="text/html",
        )
        self.assertGreaterEqual(breakdown.total, 0)
        self.assertLessEqual(breakdown.total, 100)


if __name__ == "__main__":
    unittest.main()
