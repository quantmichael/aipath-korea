"""Offline PHASE 4 discovery contract and pipeline tests."""

import unittest
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from agent_collect.collectors.planner import CollectionPlanner
from agent_collect.discovery.evaluation import RuleBasedEvaluator
from agent_collect.discovery.filtering import CandidateFilter
from agent_collect.discovery.models import (
    DiscoveryQuery, EvaluationDecision, QueryStatus, SearchResultStatus,
    SourceCandidate, SourceCandidateStatus, SourceEvaluation,
)
from agent_collect.discovery.providers import (
    FakeSearchProvider, SearchHit, SearchRateLimitError, SearchRequestPolicy, normalize_hit,
)
from agent_collect.discovery.runner import DiscoveryRunner, PromotionGate
from agent_collect.registry.service import SourceRegistryService
from agent_collect.repositories.discovery import InMemoryDiscoveryRepository
from agent_collect.repositories.discovery_supabase import SupabaseDiscoveryRepository
from agent_collect.repositories.memory import InMemorySourceRepository
from agent_collect.schemas.models import CollectionMethod
from agent_collect.schemas.status import SourceStatus


NOW = datetime(2026, 10, 3, 9, tzinfo=timezone.utc)


def query() -> DiscoveryQuery:
    return DiscoveryQuery(uuid4(), "AI conference", "fake", NOW)


def candidate() -> SourceCandidate:
    return SourceCandidate(
        id=uuid4(), query_id=uuid4(), search_result_id=uuid4(),
        original_url="https://conf.example.org/ai-conference",
        candidate_url="https://conf.example.org/ai-conference",
        canonical_url="https://conf.example.org/ai-conference",
        domain="conf.example.org", title="AI Conference", snippet="2026 event",
        discovered_at=NOW,
    )


class DiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sources = InMemorySourceRepository()
        self.registry = SourceRegistryService(self.sources)
        self.repo = InMemoryDiscoveryRepository()

    def test_query_validation(self) -> None:
        with self.assertRaises(ValueError):
            DiscoveryQuery(uuid4(), "", "fake", NOW)
        with self.assertRaises(ValueError):
            replace(query(), promoted_count=1)
        with self.assertRaises(ValueError):
            replace(query(), created_at=datetime(2026, 10, 3))

    def test_search_hit_normalization_and_bad_url_retention(self) -> None:
        item = normalize_hit(SearchHit("AI Conference", "HTTPS://EXAMPLE.ORG:443/events/?utm_x=1#part"),
                             query(), rank=1, provider="fake", discovered_at=NOW)
        self.assertEqual(item.canonical_url, "https://example.org/events")
        self.assertEqual(item.domain, "example.org")
        bad = normalize_hit(SearchHit("Bad", "file:///tmp/test"), query(), rank=2,
                            provider="fake", discovered_at=NOW)
        self.assertIsNone(bad.canonical_url)
        self.assertEqual(bad.original_url, "file:///tmp/test")
        empty = normalize_hit(SearchHit("", ""), query(), rank=3, provider="fake")
        self.assertFalse(CandidateFilter(self.sources).evaluate(empty).accepted)

    def test_filter_malformed_scheme_local_private_and_file(self) -> None:
        filt = CandidateFilter(self.sources)
        for url in ("file:///tmp/a", "http://localhost/a", "http://127.0.0.1/a",
                    "https://example.org/guide.pdf", "https://user:pass@example.org/a"):
            result = normalize_hit(SearchHit("Test", url), query(), rank=1, provider="fake")
            self.assertFalse(filt.evaluate(result).accepted, url)

    def test_filter_duplicate_url_domain_and_existing_source(self) -> None:
        q = query()
        filt = CandidateFilter(self.sources)
        first = normalize_hit(SearchHit("One", "https://example.org/a"), q, rank=1, provider="fake")
        self.assertTrue(filt.evaluate(first).accepted)
        second = normalize_hit(SearchHit("Two", "https://example.org/b"), q, rank=2, provider="fake")
        same_domain = filt.evaluate(second)
        self.assertTrue(same_domain.accepted)
        self.assertIn("same_domain_as_prior_result", same_domain.reasons)
        self.assertIn("duplicate_url_in_query", filt.evaluate(first).reasons)
        self.registry.register(name="Existing", url="https://other.org/a")
        existing = normalize_hit(SearchHit("Three", "https://other.org/a"), q, rank=3, provider="fake")
        self.assertIn("existing_source", filt.evaluate(existing).reasons)

    def test_candidate_requires_valid_status_and_promotion_fields(self) -> None:
        with self.assertRaises(ValueError):
            replace(candidate(), status=SourceCandidateStatus.PROMOTED)
        with self.assertRaises(ValueError):
            replace(candidate(), confidence=1.1)

    def test_rule_evaluator_requires_configured_official_hint(self) -> None:
        item = candidate()
        self.assertEqual(RuleBasedEvaluator().evaluate(item).decision, EvaluationDecision.REVIEW)
        accepted = RuleBasedEvaluator(official_domain_hints=("example.org",)).evaluate(item)
        self.assertEqual(accepted.decision, EvaluationDecision.ACCEPT)
        self.assertEqual(accepted.confidence, 0.9)
        self.assertIn("configured_official_domain_hint", accepted.reasons)
        irrelevant = replace(item, title="Cooking", snippet="Recipe", candidate_url="https://conf.example.org/recipe",
                             canonical_url="https://conf.example.org/recipe")
        self.assertEqual(RuleBasedEvaluator().evaluate(irrelevant).decision, EvaluationDecision.REJECT)

    def test_promotion_gate_threshold_and_rejection(self) -> None:
        item = candidate()
        evaluation = RuleBasedEvaluator(official_domain_hints=("example.org",)).evaluate(item)
        accepted = replace(item, status=SourceCandidateStatus.ACCEPTED,
                           evaluation_decision=evaluation.decision, confidence=evaluation.confidence)
        self.assertIsNone(PromotionGate(self.registry, threshold=0.95).promote(accepted, evaluation))
        self.assertIsNone(PromotionGate(self.registry, threshold=0.8).promote(item, evaluation))
        self.assertIsNone(PromotionGate(self.registry, threshold=0.8).promote(
            replace(accepted, evaluation_decision=EvaluationDecision.REVIEW), evaluation))
        self.assertIsNone(PromotionGate(self.registry, threshold=0.8).promote(
            replace(accepted, canonical_url="https://conf.example.org/other"), evaluation))
        self.assertIsNone(PromotionGate(self.registry, threshold=0.8).promote(
            accepted, replace(evaluation, reasons=("ai_related",))))
        source = PromotionGate(self.registry, threshold=0.8).promote(accepted, evaluation)
        self.assertEqual(source.status, SourceStatus.CANDIDATE)
        self.assertFalse(source.is_active)
        self.assertIsNone(PromotionGate(self.registry, threshold=0.8).promote(accepted, evaluation))

    def test_fake_provider_runner_e2e_and_collection_compatibility(self) -> None:
        q = query()
        provider = FakeSearchProvider([
            SearchHit("AI Conference", "https://conf.example.org/ai-conference", "Conference"),
            SearchHit("AI Conference duplicate", "https://conf.example.org/another", "Conference"),
            SearchHit("Bad URL", "http://localhost/ai-conference"),
            SearchHit("Cooking", "https://food.example.net/recipe"),
        ])
        runner = DiscoveryRunner(
            provider=provider, repository=self.repo, registry=self.registry,
            evaluator=RuleBasedEvaluator(official_domain_hints=("example.org",)),
            gate=PromotionGate(self.registry, threshold=0.85),
        )
        outcome = runner.run(q)
        self.assertEqual((outcome.query.result_count, outcome.query.candidate_count,
                          outcome.query.promoted_count), (4, 3, 1))
        self.assertEqual(outcome.query.status, QueryStatus.SUCCEEDED)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len(self.repo.results), 4)
        self.assertEqual(len(self.repo.candidates), 3)
        self.assertEqual(sum(row.status is SearchResultStatus.FILTERED
                             for row in self.repo.results.values()), 1)
        promoted = self.sources.get(next(iter(self.sources._rows)))
        self.assertEqual(promoted.id, UUID(outcome.promoted_source_ids[0]))
        self.assertEqual(promoted.collection_method, CollectionMethod.HTTP_HTML)
        self.assertEqual(promoted.provenance["discovery_query_id"], str(q.id))
        self.assertEqual(promoted.provenance["source_candidate_id"], outcome.candidate_ids[0])
        self.assertIn("configured_official_domain_hint",
                      promoted.provenance["discovery_officiality_signals"])
        self.assertFalse(promoted.provenance["discovery_signal_is_approval"])
        self.assertEqual(promoted.status, SourceStatus.CANDIDATE)
        self.assertFalse(promoted.is_active)
        self.assertIsNotNone(CollectionPlanner().plan(promoted))
        persisted = self.repo.get_candidate(UUID(outcome.candidate_ids[0]))
        self.assertEqual(persisted.status, SourceCandidateStatus.PROMOTED)
        self.assertEqual(persisted.promoted_source_id, promoted.id)
        again = runner.run(query())
        self.assertEqual(again.query.promoted_count, 0)
        self.assertEqual(len(self.sources._rows), 1)

    def test_provider_failure_is_persisted(self) -> None:
        class BrokenProvider:
            name = "fake"

            def search(self, query, *, policy):
                raise RuntimeError("provider offline")

        q = query()
        runner = DiscoveryRunner(
            provider=BrokenProvider(), repository=self.repo, registry=self.registry,
            evaluator=RuleBasedEvaluator(), gate=PromotionGate(self.registry, threshold=0.8),
        )
        with self.assertRaises(RuntimeError):
            runner.run(q)
        self.assertEqual(self.repo.get_query(q.id).status, QueryStatus.FAILED)

    def test_search_policy_caps_results_and_exposes_rate_limit(self) -> None:
        provider = FakeSearchProvider([SearchHit("One", "https://one.example.org"),
                                       SearchHit("Two", "https://two.example.org")])
        self.assertEqual(len(provider.search(query(), policy=SearchRequestPolicy(max_results=1))), 1)
        with self.assertRaises(ValueError):
            SearchRequestPolicy(max_results=21)
        error = SearchRateLimitError(retry_after_seconds=30)
        self.assertTrue(error.retryable)
        self.assertEqual((error.code, error.status_code, error.retry_after_seconds),
                         ("rate_limited", 429, 30))

    def test_repository_relationship_guard(self) -> None:
        with self.assertRaises(ValueError):
            self.repo.save_candidate(candidate())

    def test_supabase_repository_row_roundtrip_with_fake_client(self) -> None:
        class Query:
            def __init__(self, store, table):
                self.store, self.table, self.filters, self.record = store, table, {}, None

            def select(self, columns):
                return self

            def eq(self, key, value):
                self.filters[key] = value
                return self

            def limit(self, count):
                return self

            def upsert(self, record, *, on_conflict):
                self.record = record
                return self

            def execute(self):
                if self.record is not None:
                    self.store.setdefault(self.table, {})[self.record["id"]] = self.record
                    data = [self.record]
                else:
                    data = [row for row in self.store.get(self.table, {}).values()
                            if all(row.get(key) == value for key, value in self.filters.items())]
                return type("Result", (), {"data": data})()

        class Client:
            def __init__(self):
                self.rows = {}

            def table(self, name):
                return Query(self.rows, name)

        client = Client()
        repo = SupabaseDiscoveryRepository(client)
        q = query()
        repo.save_query(q)
        result = normalize_hit(SearchHit("AI Conference", "https://conf.example.org/ai-conference"),
                               q, rank=1, provider="fake", discovered_at=NOW)
        repo.save_result(result)
        item = replace(candidate(), query_id=q.id, search_result_id=result.id)
        repo.save_candidate(item)
        self.assertEqual(repo.get_query(q.id), q)
        self.assertEqual(repo.get_result(result.id), result)
        self.assertEqual(repo.get_candidate(item.id), item)
        self.assertEqual(repo.find_candidate_by_canonical_url(item.canonical_url), item)
        self.assertEqual(set(client.rows), {"agent_discovery_queries", "agent_search_results",
                                            "agent_source_candidates"})

    def test_migration_003_is_agent_only_and_non_destructive(self) -> None:
        path = Path(__file__).resolve().parents[1] / "agent_collect/migrations/003_discovery_foundation.sql"
        sql = path.read_text()
        self.assertEqual(len(re.findall(r"(?im)^create table public\.agent_", sql)), 3)
        self.assertNotRegex(sql, r"(?im)^\s*(drop\s+table|truncate|delete\s+from)\b")
        self.assertNotRegex(sql, r"(?im)^\s*alter table public\.(?!agent_)\w+")
        self.assertNotIn("references public.sources", sql)
