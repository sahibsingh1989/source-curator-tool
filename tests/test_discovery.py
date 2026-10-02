"""Tests for source_curator.discovery. The ddgs provider is always mocked --
no live internet dependency in this suite."""

import unittest
from unittest.mock import patch

from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

from source_curator.discovery import (
    Candidate,
    SearchError,
    SearchResult,
    build_candidates,
    dedupe_by_url,
    group_by_domain,
    search,
)


def make_raw(title, href, body="A snippet."):
    return {"title": title, "href": href, "body": body}


class SearchTests(unittest.TestCase):
    @patch("source_curator.discovery.DDGS")
    def test_returns_parsed_results(self, mock_ddgs_class):
        mock_ddgs_class.return_value.text.return_value = [
            make_raw("Example Title", "https://example.com/a", "Example snippet"),
        ]

        results = search("some topic")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Example Title")
        self.assertEqual(results[0].url, "https://example.com/a")
        self.assertEqual(results[0].snippet, "Example snippet")
        self.assertEqual(results[0].domain, "example.com")

    @patch("source_curator.discovery.DDGS")
    def test_passes_query_and_limit_through(self, mock_ddgs_class):
        mock_ddgs_class.return_value.text.return_value = []

        search("stablecoin regulation", limit=10)

        mock_ddgs_class.return_value.text.assert_called_once_with(
            "stablecoin regulation", max_results=10
        )

    @patch("source_curator.discovery.DDGS")
    def test_zero_results_returns_empty_list(self, mock_ddgs_class):
        mock_ddgs_class.return_value.text.return_value = []

        self.assertEqual(search("nothing matches this"), [])

    @patch("source_curator.discovery.DDGS")
    def test_results_missing_href_are_skipped(self, mock_ddgs_class):
        mock_ddgs_class.return_value.text.return_value = [
            {"title": "No URL here", "body": "..."},
            make_raw("Has URL", "https://example.com/"),
        ]

        results = search("topic")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url, "https://example.com/")

    @patch("source_curator.discovery.DDGS")
    def test_missing_title_falls_back_to_url(self, mock_ddgs_class):
        mock_ddgs_class.return_value.text.return_value = [{"href": "https://example.com/"}]

        results = search("topic")

        self.assertEqual(results[0].title, "https://example.com/")
        self.assertIsNone(results[0].snippet)

    def test_empty_query_raises_cleanly(self):
        with self.assertRaises(SearchError):
            search("")

    def test_whitespace_only_query_raises_cleanly(self):
        with self.assertRaises(SearchError):
            search("   ")

    def test_non_positive_limit_raises_cleanly(self):
        with self.assertRaises(SearchError):
            search("topic", limit=0)

    @patch("source_curator.discovery.DDGS")
    def test_unexpected_provider_failure_is_handled_cleanly(self, mock_ddgs_class):
        mock_ddgs_class.return_value.text.side_effect = RuntimeError("boom")

        with self.assertRaises(SearchError) as ctx:
            search("topic")
        self.assertIn("Web search failed", str(ctx.exception))

    @patch("source_curator.discovery.DDGS")
    def test_ratelimit_exception_is_handled_cleanly(self, mock_ddgs_class):
        mock_ddgs_class.return_value.text.side_effect = RatelimitException("429")

        with self.assertRaises(SearchError) as ctx:
            search("topic")
        self.assertIn("rate-limited", str(ctx.exception).lower())

    @patch("source_curator.discovery.DDGS")
    def test_timeout_exception_is_handled_cleanly(self, mock_ddgs_class):
        mock_ddgs_class.return_value.text.side_effect = TimeoutException("timed out")

        with self.assertRaises(SearchError) as ctx:
            search("topic")
        self.assertIn("timed out", str(ctx.exception).lower())

    @patch("source_curator.discovery.DDGS")
    def test_generic_provider_exception_is_handled_cleanly(self, mock_ddgs_class):
        mock_ddgs_class.return_value.text.side_effect = DDGSException("some provider error")

        with self.assertRaises(SearchError) as ctx:
            search("topic")
        self.assertIn("Web search failed", str(ctx.exception))

    @patch("source_curator.discovery.DDGS")
    def test_ddgs_no_results_exception_becomes_empty_list(self, mock_ddgs_class):
        # ddgs raises DDGSException("No results found.") instead of returning
        # an empty list when a query genuinely has nothing to return -- confirmed
        # by reading its _search_sync source via Python introspection.
        mock_ddgs_class.return_value.text.side_effect = DDGSException("No results found.")

        self.assertEqual(search("an extremely obscure topic"), [])

    @patch("source_curator.discovery.DDGS")
    def test_search_never_hits_network_when_query_is_empty(self, mock_ddgs_class):
        with self.assertRaises(SearchError):
            search("")
        mock_ddgs_class.assert_not_called()


class DedupeByUrlTests(unittest.TestCase):
    def test_removes_exact_duplicate_urls(self):
        results = [
            SearchResult("A", "https://example.com/page", None, "example.com"),
            SearchResult("A again", "https://example.com/page", None, "example.com"),
        ]
        deduped, removed = dedupe_by_url(results)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(removed, 1)

    def test_removes_trivially_equivalent_urls(self):
        results = [
            SearchResult("A", "https://example.com/page", None, "example.com"),
            SearchResult("A again", "https://example.com/page/", None, "example.com"),
        ]
        deduped, removed = dedupe_by_url(results)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(removed, 1)

    def test_keeps_distinct_urls(self):
        results = [
            SearchResult("A", "https://example.com/a", None, "example.com"),
            SearchResult("B", "https://example.com/b", None, "example.com"),
        ]
        deduped, removed = dedupe_by_url(results)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(removed, 0)

    def test_empty_input(self):
        deduped, removed = dedupe_by_url([])
        self.assertEqual(deduped, [])
        self.assertEqual(removed, 0)


class GroupByDomainTests(unittest.TestCase):
    def test_keeps_one_representative_per_domain(self):
        results = [
            SearchResult("First", "https://example.com/a", None, "example.com"),
            SearchResult("Second", "https://example.com/b", None, "example.com"),
            SearchResult("Third", "https://other.example.org/x", None, "other.example.org"),
        ]
        grouped = group_by_domain(results)
        self.assertEqual(len(grouped), 2)
        self.assertEqual(grouped[0].title, "First")
        self.assertEqual(grouped[1].title, "Third")

    def test_no_grouping_needed_when_domains_differ(self):
        results = [
            SearchResult("A", "https://a.example.com/", None, "a.example.com"),
            SearchResult("B", "https://b.example.com/", None, "b.example.com"),
        ]
        self.assertEqual(len(group_by_domain(results)), 2)


class BuildCandidatesTests(unittest.TestCase):
    def test_full_pipeline_stats_and_numbering(self):
        results = [
            SearchResult("A", "https://example.com/page", "snippet A", "example.com"),
            SearchResult("A dup", "https://example.com/page/", "snippet A dup", "example.com"),
            SearchResult("B", "https://example.com/other", "snippet B", "example.com"),
            SearchResult("C", "https://other.example.org/x", "snippet C", "other.example.org"),
        ]

        candidates, stats = build_candidates(results)

        # one exact/normalized duplicate removed, then one more result folded
        # away by same-domain grouping -- two unique domains remain.
        self.assertEqual(stats.raw_count, 4)
        self.assertEqual(stats.duplicate_count, 1)
        self.assertEqual(stats.unique_domain_count, 2)
        self.assertEqual(len(candidates), 2)
        self.assertEqual([c.rank for c in candidates], [1, 2])
        self.assertEqual(candidates[0].domain, "example.com")
        self.assertEqual(candidates[1].domain, "other.example.org")

    def test_marks_already_saved_domains(self):
        results = [
            SearchResult("A", "https://saved.example.com/", "s", "saved.example.com"),
            SearchResult("B", "https://new.example.org/", "s", "new.example.org"),
        ]

        candidates, _ = build_candidates(results, saved_domains={"saved.example.com"})

        by_domain = {c.domain: c for c in candidates}
        self.assertTrue(by_domain["saved.example.com"].already_saved)
        self.assertFalse(by_domain["new.example.org"].already_saved)

    def test_no_saved_domains_marks_everything_unsaved(self):
        results = [SearchResult("A", "https://example.com/", "s", "example.com")]
        candidates, _ = build_candidates(results)
        self.assertFalse(candidates[0].already_saved)

    def test_empty_results_produce_empty_candidates_and_zero_stats(self):
        candidates, stats = build_candidates([])
        self.assertEqual(candidates, [])
        self.assertEqual(stats.raw_count, 0)
        self.assertEqual(stats.duplicate_count, 0)
        self.assertEqual(stats.unique_domain_count, 0)


if __name__ == "__main__":
    unittest.main()
