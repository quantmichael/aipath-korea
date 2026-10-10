"""Offline Tavily and persistent search-budget tests; no real API calls."""

import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from urllib.error import URLError

from agent_collect.discovery.budget import (
    BudgetBlockReason, BudgetLevel, SearchBudgetGuard, SearchBudgetPolicy,
    normalize_query,
)
from agent_collect.discovery.evaluation import RuleBasedEvaluator
from agent_collect.discovery.models import DiscoveryQuery, QueryStatus
from agent_collect.discovery.providers import SearchProviderError, SearchRequestPolicy
from agent_collect.discovery.runner import DiscoveryRunner, PromotionGate
from agent_collect.discovery.tavily import TavilySearchProvider
from agent_collect.registry.service import SourceRegistryService
from agent_collect.repositories.discovery import InMemoryDiscoveryRepository
from agent_collect.repositories.memory import InMemorySourceRepository


NOW = datetime(2026, 10, 3, 9, tzinfo=timezone.utc)


def query(text="AI conference Korea", *, when=NOW):
    return DiscoveryQuery(uuid4(), text, "tavily", when)


def attempted(repo, *, text="other query", when=NOW):
    repo.save_query(replace(query(text, when=when), status=QueryStatus.SUCCEEDED,
                            started_at=when, completed_at=when))


def guard(repo, *, daily=30, monthly=1000, cooldown=24,
          warning=800, critical=950, enabled=True, credential=True):
    return SearchBudgetGuard(repo, policy=SearchBudgetPolicy(
        daily_limit=daily, monthly_limit=monthly, cooldown_hours=cooldown,
        warning_threshold=warning, critical_threshold=critical,
    ), provider_enabled=enabled, credential_present=credential)


class TavilyProviderTests(unittest.TestCase):
    def test_success_normalizes_minimal_fields_and_caps_request(self):
        calls = []
        def transport(payload, headers, timeout):
            calls.append((json.loads(payload), headers, timeout))
            return 200, {}, json.dumps({"results": [{"title": "AI Conf", "url": "https://EXAMPLE.org/ai?utm_x=1",
                "content": "a" * 900, "raw_content": "never-save-this"}]}).encode()
        results = TavilySearchProvider("test-token", transport=transport).search(
            query(), policy=SearchRequestPolicy(max_results=5, timeout_seconds=10))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0]["max_results"], 5)
        self.assertFalse(calls[0][0]["include_raw_content"])
        self.assertEqual(results[0].provider, "tavily")
        self.assertEqual(results[0].canonical_url, "https://example.org/ai")
        self.assertEqual(len(results[0].snippet), 500)
        self.assertNotIn("never-save-this", repr(results[0]))

    def test_empty_result(self):
        provider = TavilySearchProvider("test", transport=lambda *_: (200, {}, b'{"results":[]}'))
        self.assertEqual(provider.search(query(), policy=SearchRequestPolicy()), [])

    def test_errors_are_safe_and_classified(self):
        cases = [
            ((401, {}, b"secret"), "authentication_failure", False),
            ((429, {"Retry-After": "9"}, b"secret"), "rate_limited", True),
            ((500, {}, b"secret"), "http_error", True),
            ((200, {}, b'{"results":{}}'), "malformed_response", False),
            ((200, {}, b'{"results":[1]}'), "malformed_response", False),
            ((200, {}, json.dumps({"results": [{}] * 6}).encode()), "malformed_response", False),
        ]
        for response, code, retryable in cases:
            with self.subTest(code=code):
                provider = TavilySearchProvider("test", transport=lambda *_: response)
                with self.assertRaises(SearchProviderError) as context:
                    provider.search(query(), policy=SearchRequestPolicy())
                self.assertEqual((context.exception.code, context.exception.retryable), (code, retryable))
                self.assertNotIn("secret", str(context.exception))
                if code == "rate_limited":
                    self.assertEqual(context.exception.retry_after_seconds, 9)
        for error, code in ((TimeoutError(), "timeout"), (URLError("offline"), "network_failure")):
            def broken(*_):
                raise error
            with self.assertRaises(SearchProviderError) as context:
                TavilySearchProvider("test", transport=broken).search(query(), policy=SearchRequestPolicy())
            self.assertEqual(context.exception.code, code)


class SearchBudgetTests(unittest.TestCase):
    def setUp(self):
        self.repo = InMemoryDiscoveryRepository()

    def test_limits_and_restart_from_persisted_rows(self):
        for index in range(2):
            attempted(self.repo, text=f"different {index}")
        self.assertTrue(guard(self.repo, daily=3).check(query(), now=NOW).allowed)
        self.assertEqual(guard(self.repo, daily=2).check(query(), now=NOW).reason,
                         BudgetBlockReason.DAILY_LIMIT_REACHED)
        self.assertTrue(guard(self.repo, monthly=3, warning=1, critical=2).check(query(), now=NOW).allowed)
        self.assertEqual(guard(self.repo, monthly=2, warning=1, critical=2).check(query(), now=NOW).reason,
                         BudgetBlockReason.MONTHLY_LIMIT_REACHED)
        self.assertEqual(guard(self.repo, monthly=2, warning=1, critical=2).check(query(), now=NOW).level,
                         BudgetLevel.HARD_STOP)

    def test_cooldown_normalization_and_expiry(self):
        self.assertEqual(normalize_query("  AI   CONFERENCE\tKorea "), "ai conference korea")
        attempted(self.repo, text="  AI  CONFERENCE Korea ", when=NOW - timedelta(hours=23))
        self.assertEqual(guard(self.repo).check(query(), now=NOW).reason, BudgetBlockReason.QUERY_COOLDOWN)
        self.assertTrue(guard(self.repo).check(query(), now=NOW + timedelta(hours=2)).allowed)

    def test_disabled_missing_credential_and_warning_levels(self):
        self.assertEqual(guard(self.repo, enabled=False).check(query(), now=NOW).reason,
                         BudgetBlockReason.PROVIDER_DISABLED)
        self.assertEqual(guard(self.repo, credential=False).check(query(), now=NOW).reason,
                         BudgetBlockReason.MISSING_CREDENTIAL)
        for index in range(1000):
            attempted(self.repo, text=f"distinct {index}")
        for count, expected in ((799, BudgetLevel.NORMAL), (800, BudgetLevel.WARNING),
                                (950, BudgetLevel.CRITICAL), (1000, BudgetLevel.HARD_STOP)):
            limited = InMemoryDiscoveryRepository()
            for row in list(self.repo.queries.values())[:count]:
                limited.save_query(row)
            self.assertEqual(guard(limited, daily=2000).check(query(), now=NOW).level, expected)

    def test_seoul_day_boundary_and_blocked_request_never_calls_provider(self):
        attempted(self.repo, when=datetime(2026, 10, 2, 14, tzinfo=timezone.utc))
        self.assertEqual(guard(self.repo, daily=1).check(query(), now=NOW).daily_usage, 0)
        calls = []
        def transport(*args):
            calls.append(args)
            return 200, {}, b'{"results":[]}'
        provider = TavilySearchProvider("test", transport=transport)
        sources = InMemorySourceRepository()
        registry = SourceRegistryService(sources)
        runner = DiscoveryRunner(provider=provider, repository=self.repo, registry=registry,
                                 evaluator=RuleBasedEvaluator(), gate=PromotionGate(registry, threshold=0.9),
                                 budget_guard=guard(self.repo, credential=False))
        result = runner.run(query())
        self.assertEqual(result.query.status, QueryStatus.FAILED)
        self.assertTrue(result.query.provenance["budget_blocked"])
        self.assertIsNone(result.query.started_at)
        self.assertEqual(calls, [])

    def test_allowed_run_records_budget_snapshot_without_network(self):
        calls = []
        def transport(*args):
            calls.append(1)
            return 200, {}, b'{"results":[]}'
        provider = TavilySearchProvider("test", transport=transport)
        registry = SourceRegistryService(InMemorySourceRepository())
        runner = DiscoveryRunner(provider=provider, repository=self.repo, registry=registry,
                                 evaluator=RuleBasedEvaluator(), gate=PromotionGate(registry, threshold=0.9),
                                 budget_guard=guard(self.repo))
        outcome = runner.run(query())
        self.assertEqual(outcome.query.status, QueryStatus.SUCCEEDED)
        self.assertEqual(outcome.query.provenance["budget_level_before"], "NORMAL")
        self.assertEqual(calls, [1])


if __name__ == "__main__":
    unittest.main()
