"""PHASE 12A planner and THE NEXT AI regression; no network or DB writes."""

import unittest
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from agent_collect.discovery.budget import SearchBudgetPolicy
from agent_collect.discovery.evaluation import RuleBasedEvaluator
from agent_collect.discovery.models import DiscoveryQuery, EvaluationDecision, QueryStatus
from agent_collect.discovery.planner import AutonomousDiscoveryPlanner, QUERY_TEMPLATES
from agent_collect.discovery.providers import FakeSearchProvider, SearchHit
from agent_collect.discovery.runner import DiscoveryRunner, PromotionGate
from agent_collect.discovery.scheduling import DiscoverySchedule
from agent_collect.discovery.scheduling_supabase import SupabaseDiscoverySchedules
from agent_collect.config.settings import load_settings
from agent_collect.runtime import AgentRuntime
from agent_collect.registry.service import SourceRegistryService
from agent_collect.repositories.discovery import InMemoryDiscoveryRepository
from agent_collect.repositories.memory import InMemorySourceRepository
from agent_collect.schemas.models import Vertical
from agent_collect.schemas.status import SourceStatus


KST = ZoneInfo("Asia/Seoul")


def at(year, month=10, day=12, hour=9):
    return datetime(year, month, day, hour, tzinfo=KST)


def attempted(text, when):
    return DiscoveryQuery(uuid4(), text, "tavily", when.astimezone(timezone.utc),
                          status=QueryStatus.SUCCEEDED,
                          started_at=when.astimezone(timezone.utc),
                          completed_at=(when + timedelta(seconds=1)).astimezone(timezone.utc))


class DiscoveryPlannerTests(unittest.TestCase):
    def setUp(self):
        self.planner = AutonomousDiscoveryPlanner()

    def test_dynamic_years_and_evergreen_for_2026_2028_2035(self):
        for year in (2026, 2028, 2035):
            with self.subTest(year=year):
                catalog = self.planner.catalog(now=at(year))
                self.assertEqual(len(catalog), 48)
                self.assertEqual({x[1] for x in catalog}, {year, year + 1, None})
                for category in QUERY_TEMPLATES:
                    entries = [x for x in catalog if x[0] is category]
                    self.assertEqual([x[1] for x in entries].count(year), 2)
                    self.assertEqual([x[1] for x in entries].count(year + 1), 2)
                    self.assertEqual([x[1] for x in entries].count(None), 2)
                plan = self.planner.plan(now=at(year), existing_schedules=(), recent_queries=())
                self.assertEqual({x.query_year for x in plan.items}, {year, year + 1, None})
                self.assertTrue(all(x.status == "DRY_RUN" for x in plan.items))
                self.assertEqual(plan.audit_details()["executed_query_count"], 0)

    def test_kst_december_january_rollover(self):
        before = self.planner.catalog(now=datetime(2026, 12, 31, 14, 59, tzinfo=timezone.utc))
        after = self.planner.catalog(now=datetime(2026, 12, 31, 15, 0, tzinfo=timezone.utc))
        self.assertEqual({x[1] for x in before}, {2026, 2027, None})
        self.assertEqual({x[1] for x in after}, {2027, 2028, None})
        self.assertNotIn("2026", " ".join(x[3] for x in after))

    def test_all_categories_and_languages_rotate(self):
        seen_categories = set()
        seen_languages = set()
        for offset in range(20):
            plan = self.planner.plan(now=at(2026) + timedelta(days=offset),
                                     existing_schedules=(), recent_queries=())
            seen_categories.update(item.category for item in plan.items)
            seen_languages.update(item.language for item in plan.items)
        self.assertEqual(seen_categories, set(QUERY_TEMPLATES))
        self.assertEqual(seen_languages, {"en", "ko"})

    def test_repeated_planning_is_deterministic_and_never_executes(self):
        now = at(2028, 1, 1)
        first = self.planner.plan(now=now, existing_schedules=(), recent_queries=())
        second = self.planner.plan(now=now, existing_schedules=(), recent_queries=())
        self.assertEqual(first, second)
        self.assertTrue(all(item.status == "DRY_RUN" for item in first.items))
        self.assertEqual(first.audit_details()["executed_query_count"], 0)

    def test_existing_schedule_and_cooldown_are_not_replanned(self):
        now = at(2026)
        first = self.planner.plan(now=now, existing_schedules=(), recent_queries=()).items[0]
        schedule = DiscoverySchedule(uuid4(), first.query_text, first.category,
                                     first.language, "KR", 1440, now, False)
        plan = self.planner.plan(now=now, existing_schedules=(schedule,), recent_queries=())
        self.assertNotIn(first.query_text, [item.query_text for item in plan.items])
        self.assertGreaterEqual(plan.duplicate_count, 1)
        second = plan.items[0]
        cooled = self.planner.plan(now=now, existing_schedules=(schedule,),
                                   recent_queries=(attempted(second.query_text, now - timedelta(hours=1)),))
        self.assertNotIn(second.query_text, [item.query_text for item in cooled.items])
        self.assertGreaterEqual(cooled.cooldown_blocked_count, 1)
        self.assertEqual(len({item.query_text for item in cooled.items}), len(cooled.items))

    def test_budget_stops_planning_and_failed_provider_still_counts(self):
        now = at(2026)
        policy = SearchBudgetPolicy(daily_limit=3, monthly_limit=1000,
                                    warning_threshold=800, critical_threshold=950)
        planner = AutonomousDiscoveryPlanner(policy=policy)
        rows = tuple(attempted(f"other query {i}", now - timedelta(minutes=i)) for i in range(3))
        plan = planner.plan(now=now, existing_schedules=(), recent_queries=rows)
        self.assertEqual(plan.items, ())
        self.assertEqual(plan.budget_blocked_count, 3)
        self.assertEqual(plan.daily_usage, 3)
        self.assertEqual(plan.audit_details()["planned_query_count"], 0)

    def test_next_ai_fixture_reaches_review_without_auto_approval(self):
        url = "https://nextaikorea.com/en"
        sources = InMemorySourceRepository()
        registry = SourceRegistryService(sources)
        repo = InMemoryDiscoveryRepository()
        provider = FakeSearchProvider([SearchHit(
            "2026 THE NEXT AI 피지컬 AI & 스마트팩토리 산업전", url,
            "AI exhibition expo 2026-10-14 to 2026-10-16; visitor registration")])
        runner = DiscoveryRunner(
            provider=provider, repository=repo, registry=registry,
            evaluator=RuleBasedEvaluator(), gate=PromotionGate(registry, threshold=0.9))
        query = DiscoveryQuery(uuid4(), "2026 AI exhibition Korea registration",
                               "fake", at(2026), vertical_hint=Vertical.EXHIBITION)
        result = runner.run(query)
        self.assertEqual((result.query.result_count, result.query.candidate_count), (1, 1))
        candidate = next(iter(repo.candidates.values()))
        self.assertEqual(candidate.canonical_url, url)
        self.assertEqual(candidate.evaluation_decision, EvaluationDecision.REVIEW)
        self.assertIn("vertical_related", candidate.reasons)
        self.assertEqual(result.query.promoted_count, 0)
        self.assertEqual(list(sources._rows.values()), [])

    def test_official_hint_does_not_activate_source(self):
        sources = InMemorySourceRepository()
        registry = SourceRegistryService(sources)
        repo = InMemoryDiscoveryRepository()
        runner = DiscoveryRunner(
            provider=FakeSearchProvider([SearchHit(
                "THE NEXT AI exhibition", "https://nextaikorea.com/en", "AI expo registration")]),
            repository=repo, registry=registry,
            evaluator=RuleBasedEvaluator(official_domain_hints=("nextaikorea.com",)),
            gate=PromotionGate(registry, threshold=0.9))
        result = runner.run(DiscoveryQuery(uuid4(), "AI exhibition Korea", "fake", at(2026),
                                           vertical_hint=Vertical.EXHIBITION))
        self.assertEqual(result.query.promoted_count, 1)
        source = next(iter(sources._rows.values()))
        self.assertEqual(source.status, SourceStatus.CANDIDATE)
        self.assertFalse(source.is_active)

    def test_runtime_plan_preview_only_reads_existing_db_state(self):
        class ReadOnlyClient:
            def __init__(self):
                self.tables = []

            def table(self, name):
                self.tables.append(name)
                return self

            def select(self, *args):
                return self

            def eq(self, *args):
                return self

            def gte(self, *args):
                return self

            def order(self, *args):
                return self

            def range(self, *args):
                return self

            def execute(self):
                return SimpleNamespace(data=[])

        client = ReadOnlyClient()
        settings = load_settings(environ={})
        self.assertFalse(settings.discovery_auto_approval_enabled)
        self.assertFalse(settings.publish_enabled)
        self.assertFalse(settings.publish_worker_enabled)
        runtime = AgentRuntime(SimpleNamespace(client=client), None, None, settings)
        plan = runtime.discovery_plan_preview(now=at(2035))
        self.assertEqual(len(plan.items), 3)
        self.assertEqual(client.tables, ["agent_discovery_schedules", "agent_discovery_queries"])

    def test_optional_plan_audit_distinguishes_plan_from_execution(self):
        class AuditClient:
            def table(self, name):
                self.name = name
                return self

            def insert(self, row):
                self.row = row
                return self

            def execute(self):
                return SimpleNamespace(data=[])

        client = AuditClient()
        plan = self.planner.plan(now=at(2026), existing_schedules=(), recent_queries=())
        SupabaseDiscoverySchedules(client).record_plan(plan)
        self.assertEqual(client.name, "agent_logs")
        self.assertEqual(client.row["event_type"], "DISCOVERY_PLAN")
        self.assertEqual(client.row["details"]["planned_query_count"], 3)
        self.assertEqual(client.row["details"]["executed_query_count"], 0)


if __name__ == "__main__":
    unittest.main()
