"""Offline checks for the PHASE 12G one-shot Discovery isolation boundary."""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from agent_collect.budget.reservations import SupabaseBudgetReservations
from agent_collect.config.settings import load_settings
from agent_collect.discovery.evaluation import RuleBasedEvaluator
from agent_collect.discovery.goal_supabase import GoalExecutionWorker, SupabaseGoalPlans
from agent_collect.discovery.models import (
    DiscoveryQuery, EvaluationDecision, QueryStatus, SearchResultStatus,
    SourceCandidate, SourceCandidateStatus,
)
from agent_collect.discovery.providers import FakeSearchProvider, SearchHit, SearchProviderError
from agent_collect.discovery.search_only import (
    ExactPlanSearchWorker, SearchOnlyDiscoveryRunner,
)
from agent_collect.repositories.discovery import InMemoryDiscoveryRepository
from agent_collect.runtime import AgentRuntime


class MockTavily(FakeSearchProvider):
    name = "tavily"
    credential_present = True


class ProtectedSources:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.lookup_count = 0
        self.write_count = 0

    def find_by_canonical_url(self, url):
        self.lookup_count += 1
        return object() if url in self.existing else None

    def save(self, source):
        self.write_count += 1
        raise AssertionError("search-only attempted a Source write")

    def register(self, **kwargs):
        self.write_count += 1
        raise AssertionError("search-only attempted Source registration")


class Guard:
    def __init__(self, allowed=True):
        self.allowed = allowed

    def check(self, query):
        return SimpleNamespace(
            allowed=self.allowed,
            reason=SimpleNamespace(value="DAILY_LIMIT_REACHED"),
            daily_usage=1, monthly_usage=4,
            level=SimpleNamespace(value="NORMAL"),
        )


class Budget:
    def __init__(self, fail=None):
        self.fail = fail
        self.calls = []

    def reserve(self, **kwargs):
        self.calls.append(("reserve", kwargs))
        if self.fail == "reserve":
            raise RuntimeError("reservation failed")
        return uuid4()

    def dispatch(self, reservation_id):
        self.calls.append(("dispatch", reservation_id))
        if self.fail == "dispatch":
            raise RuntimeError("dispatch failed")

    def finish(self, reservation_id, *, status, error_code=None):
        self.calls.append(("finish", status, error_code))


class Plans:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def claim_query(self, claim_id):
        self.calls.append("claim")
        return self.row

    def finish_query(self, claim_id, *, status, query_id=None):
        self.calls.append(("finish", status, query_id))


class ExactPlans(Plans):
    def claim_query(self, claim_id):
        raise AssertionError("unscoped Plan Claim must not be used")

    def claim_query_exact(self, plan_id, claim_id):
        self.calls.append(("claim_exact", plan_id))
        row, self.row = self.row, None
        return row


class SearchOnlyTests(unittest.TestCase):
    def runner(self, hits, *, existing=(), budget=None, guard=None, provider=None):
        sources = ProtectedSources(existing)
        repository = InMemoryDiscoveryRepository()
        provider = provider or MockTavily(hits)
        budget = budget or Budget()
        runner = SearchOnlyDiscoveryRunner(
            provider=provider, repository=repository,
            find_source=sources.find_by_canonical_url,
            evaluator=RuleBasedEvaluator(official_domain_hints=("official.example",)),
            budget_guard=guard or Guard(), reservations=budget,
        )
        return runner, sources, repository, provider, budget

    def test_official_result_is_review_only_with_no_source_or_approval_write(self):
        runner, sources, repository, provider, budget = self.runner([
            SearchHit("AI hackathon 2026 Korea registration",
                      "https://official.example/ai-hackathon",
                      "Official AI hackathon application"),
        ])
        row = {"id": str(uuid4()), "plan_id": str(uuid4()),
               "query_text": "2026 인공지능 해커톤 한국 참가 모집",
               "vertical_hint": "hackathon", "new_category": None}
        plans = Plans(row)
        result = GoalExecutionWorker(plans=plans, runner=runner, enabled=True).run_once()
        self.assertEqual((result["status"], result["candidate_count"]), ("SUCCEEDED", 1))
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(sources.write_count, 0)
        self.assertFalse(hasattr(runner.registry, "register"))
        self.assertIsNone(runner.auto_assessor)
        self.assertIsNone(runner.auto_executor)
        self.assertEqual(repository.queries[provider.calls[0].id].promoted_count, 0)
        self.assertEqual(repository.candidates, {})
        candidate = next(iter(runner.repository.candidates.values()))
        result_row = next(iter(repository.results.values()))
        self.assertEqual(result_row.status, SearchResultStatus.CANDIDATE)
        self.assertFalse(result_row.provider_metadata["quality"]["source_candidate_persisted"])
        self.assertEqual(result_row.provider_metadata["quality"]["classification"], "UNVERIFIED")
        self.assertEqual(runner.last_quality_metrics.verified_new_opportunity_count, 0)
        self.assertEqual(next(iter(repository.queries.values())).provenance["source_candidates_persisted"], 0)
        self.assertEqual(candidate.status, SourceCandidateStatus.REVIEW_REQUIRED)
        self.assertEqual(candidate.evaluation_decision, EvaluationDecision.REVIEW)
        self.assertIn("configured_official_domain_hint", candidate.reasons)
        self.assertIn("search_only_source_review_required", candidate.reasons)
        self.assertEqual(candidate.provenance["execution_mode"], "SEARCH_ONLY")
        self.assertFalse(candidate.provenance["source_approval_eligible"])
        self.assertEqual([call[0] for call in budget.calls],
                         ["reserve", "dispatch", "finish"])
        self.assertEqual(budget.calls[-1][1], "SUCCEEDED")
        self.assertEqual(plans.calls[-1][1], "SUCCEEDED")
        self.assertIn("budget_reservation_id",
                      next(iter(repository.queries.values())).provenance)
        self.assertEqual(next(iter(repository.queries.values())).provenance["execution_mode"],
                         "SEARCH_ONLY")

    def test_existing_source_and_candidate_are_filtered_without_source_write(self):
        url = "https://official.example/ai-hackathon"
        runner, sources, repository, provider, _ = self.runner(
            [SearchHit("AI hackathon", url, "AI hackathon"),
             SearchHit("AI hackathon duplicate", url, "AI hackathon")],
            existing=(url,))
        outcome = runner.run(DiscoveryQuery(
            uuid4(), "2026 인공지능 해커톤 한국 참가 모집", "tavily",
            datetime.now(timezone.utc)))
        self.assertEqual(outcome.query.result_count, 2)
        self.assertEqual(outcome.query.candidate_count, 0)
        self.assertEqual(sources.write_count, 0)
        self.assertTrue(all(row.status is SearchResultStatus.FILTERED
                            for row in repository.results.values()))
        self.assertTrue(all("existing_source" in row.filter_reasons
                            for row in repository.results.values()))

    def test_existing_candidate_is_filtered_without_duplicate_registration(self):
        url = "https://official.example/ai-hackathon"
        runner, sources, repository, provider, _ = self.runner([
            SearchHit("AI hackathon", url, "AI hackathon application")])
        old = SourceCandidate(
            uuid4(), uuid4(), uuid4(), url, url, url, "official.example",
            "AI hackathon", "Prior candidate", datetime.now(timezone.utc),
        )
        repository.candidates[old.id] = old
        outcome = runner.run(DiscoveryQuery(
            uuid4(), "2026 인공지능 해커톤 한국 참가 모집", "tavily",
            datetime.now(timezone.utc)))
        self.assertEqual((outcome.query.result_count, outcome.query.candidate_count), (1, 0))
        self.assertEqual(len(repository.candidates), 1)
        self.assertEqual(runner.repository.candidates, {})
        self.assertEqual(sources.write_count, 0)
        self.assertIn("existing_candidate",
                      next(iter(repository.results.values())).filter_reasons)

    def test_failed_budget_reservation_or_dispatch_never_calls_tavily(self):
        for fail in ("reserve", "dispatch"):
            with self.subTest(fail=fail):
                budget = Budget(fail=fail)
                runner, sources, repository, provider, _ = self.runner(
                    [SearchHit("AI hackathon", "https://official.example/event", "AI")],
                    budget=budget)
                with self.assertRaises(RuntimeError):
                    runner.run(DiscoveryQuery(
                        uuid4(), "2026 인공지능 해커톤 한국 참가 모집", "tavily",
                        datetime.now(timezone.utc)))
                self.assertEqual(provider.calls, [])
                self.assertEqual(sources.write_count, 0)
                if fail == "dispatch":
                    self.assertEqual(next(iter(repository.queries.values())).status,
                                     QueryStatus.FAILED)

    def test_budget_guard_block_never_reserves_or_calls_tavily(self):
        runner, sources, repository, provider, budget = self.runner(
            [SearchHit("AI hackathon", "https://official.example/event", "AI")],
            guard=Guard(allowed=False))
        outcome = runner.run(DiscoveryQuery(
            uuid4(), "2026 인공지능 해커톤 한국 참가 모집", "tavily",
            datetime.now(timezone.utc)))
        self.assertEqual(outcome.query.status, QueryStatus.FAILED)
        self.assertEqual(outcome.query.error_summary, "DAILY_LIMIT_REACHED")
        self.assertEqual(provider.calls, [])
        self.assertEqual(budget.calls, [])
        self.assertEqual(sources.write_count, 0)

    def test_provider_timeout_marks_reservation_unknown_without_source_write(self):
        class TimeoutTavily(MockTavily):
            def search(self, query, *, policy):
                self.calls.append(query)
                raise SearchProviderError("timeout", retryable=True)
        timeout_provider = TimeoutTavily([])
        runner, sources, repository, provider, budget = self.runner(
            [], provider=timeout_provider)
        with self.assertRaises(SearchProviderError):
            runner.run(DiscoveryQuery(
                uuid4(), "2026 인공지능 해커톤 한국 참가 모집", "tavily",
                datetime.now(timezone.utc)))
        self.assertEqual(len(timeout_provider.calls), 1)
        self.assertEqual(next(iter(repository.queries.values())).status, QueryStatus.FAILED)
        self.assertEqual(budget.calls[-1][1:], ("UNKNOWN", "TIMEOUT"))
        self.assertEqual(sources.write_count, 0)

    def test_search_only_requires_guarded_atomic_tavily(self):
        runner, sources, repository, provider, budget = self.runner([])
        for overrides in ({"budget_guard": None}, {"reservations": None},
                          {"provider": FakeSearchProvider([])}):
            with self.subTest(overrides=tuple(overrides)), self.assertRaises(ValueError):
                kwargs = dict(provider=provider, repository=repository,
                              find_source=sources.find_by_canonical_url,
                              evaluator=RuleBasedEvaluator(), budget_guard=Guard(),
                              reservations=budget)
                kwargs.update(overrides)
                SearchOnlyDiscoveryRunner(**kwargs)

    def test_runtime_factory_does_not_enable_automation_flags(self):
        settings = load_settings(environ={"AGENT_DISCOVERY_ENABLED": "true"})
        self.assertFalse(settings.ai_discovery_execution_enabled)
        sources = ProtectedSources()
        sources.client = object()
        runtime = AgentRuntime(sources, None, None, settings)
        runner = runtime.discovery_runner(
            MockTavily([]), repository=InMemoryDiscoveryRepository(), search_only=True)
        self.assertIsInstance(runner, SearchOnlyDiscoveryRunner)
        self.assertIsInstance(runner.reservations, SupabaseBudgetReservations)
        self.assertFalse(settings.ai_discovery_execution_enabled)
        self.assertEqual(sources.write_count, 0)

    def test_exact_plan_worker_uses_scoped_claim_and_limits_attempts(self):
        plan_id = uuid4()
        row = {"id": str(uuid4()), "plan_id": str(plan_id),
               "query_text": "2026 인공지능 해커톤 한국 참가 모집",
               "vertical_hint": "hackathon", "new_category": None}
        plans = ExactPlans(row)
        runner, sources, repository, provider, budget = self.runner([
            SearchHit("AI hackathon", "https://official.example/event",
                      "Official AI hackathon application")])
        worker = ExactPlanSearchWorker(plans=plans, runner=runner, plan_id=plan_id)
        self.assertEqual(worker.run_once()["status"], "DISABLED")
        self.assertEqual(plans.calls, [])
        worker.enabled = True
        self.assertEqual(worker.run_once()["status"], "SUCCEEDED")
        self.assertEqual(plans.calls[0], ("claim_exact", plan_id))
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(worker.run_once()["status"], "EMPTY")
        self.assertEqual(worker.run_once()["status"], "LIMIT_REACHED")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(sources.write_count, 0)

    def test_failed_or_wrong_plan_claim_never_calls_tavily(self):
        plan_id = uuid4()
        runner, sources, repository, provider, budget = self.runner([
            SearchHit("AI hackathon", "https://official.example/event", "AI")])
        plans = ExactPlans(None)
        worker = ExactPlanSearchWorker(plans=plans, runner=runner,
                                       plan_id=plan_id, enabled=True)
        self.assertEqual(worker.run_once()["status"], "EMPTY")
        self.assertEqual(provider.calls, [])
        wrong_row = {"id": str(uuid4()), "plan_id": str(uuid4()),
                     "query_text": "2026 인공지능 해커톤 한국 참가 모집",
                     "vertical_hint": "hackathon", "new_category": None}
        plans.row = wrong_row
        with self.assertRaisesRegex(RuntimeError, "different plan"):
            worker.run_once()
        self.assertEqual(provider.calls, [])
        self.assertEqual(budget.calls, [])
        self.assertEqual(sources.write_count, 0)

    def test_exact_claim_adapter_uses_only_scoped_rpc(self):
        class RpcClient:
            def __init__(self):
                self.calls = []

            def rpc(self, name, args):
                self.calls.append((name, args))
                return SimpleNamespace(execute=lambda: SimpleNamespace(data=[]))

        client = RpcClient()
        plan_id, claim_id = uuid4(), uuid4()
        self.assertIsNone(SupabaseGoalPlans(client).claim_query_exact(plan_id, claim_id))
        self.assertEqual(client.calls, [
            ("agent_claim_ai_plan_query_exact",
             {"p_plan_id": str(plan_id), "p_claim_id": str(claim_id)})
        ])

    def test_exact_claim_rpc_failure_never_calls_tavily(self):
        class RpcClient:
            def rpc(self, name, args):
                raise RuntimeError("claim RPC unavailable")

        runner, sources, repository, provider, budget = self.runner([])
        worker = ExactPlanSearchWorker(
            plans=SupabaseGoalPlans(RpcClient()), runner=runner,
            plan_id=uuid4(), enabled=True,
        )
        with self.assertRaisesRegex(RuntimeError, "claim RPC unavailable"):
            worker.run_once()
        self.assertEqual(provider.calls, [])
        self.assertEqual(budget.calls, [])
        self.assertEqual(sources.write_count, 0)


if __name__ == "__main__":
    unittest.main()
