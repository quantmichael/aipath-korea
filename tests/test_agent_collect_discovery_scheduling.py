"""PHASE 10 Discovery/Automation tests: no network or Agent DB writes."""

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from agent_collect.automation.runtime import AutomationRuntime
from agent_collect.config.settings import load_settings
from agent_collect.discovery.models import QueryStatus
from agent_collect.discovery.scheduling import DiscoveryClaim, DiscoverySchedule, DiscoveryWorker
from agent_collect.discovery.scheduling_supabase import SupabaseDiscoverySchedules
from agent_collect.schemas.models import Vertical


NOW = datetime(2026, 10, 4, 1, tzinfo=timezone.utc)


def schedule(*, enabled=True, next_run_at=NOW, text="AI conference Korea"):
    return DiscoverySchedule(uuid4(), text, Vertical.CONFERENCE, "en", "KR", 1440,
                             next_run_at, enabled)


class FakeSchedules:
    def __init__(self, rows):
        self.rows = rows
        self.active = {}
        self.terminal = {}
        self.claims = []
        self.completions = []
        self.summaries = []

    def list_due(self, limit):
        return [row for row in self.rows if row.enabled and row.next_run_at <= NOW
                and row.id not in self.active and row.id not in self.terminal][:limit]

    def claim(self, row, *, worker_id, claim_id, lease_seconds):
        if row.id in self.active or row.id in self.terminal:
            return None
        claim = DiscoveryClaim(row, claim_id, uuid4(), worker_id,
                               NOW + timedelta(seconds=lease_seconds))
        self.active[row.id] = claim
        self.claims.append(claim)
        return claim

    def complete(self, claim, *, outcome, query_id, failure_backoff_minutes, budget_delay_minutes):
        if claim.schedule.id in self.terminal:
            return self.terminal[claim.schedule.id]
        self.completions.append((claim, outcome, query_id))
        self.terminal[claim.schedule.id] = outcome
        self.active.pop(claim.schedule.id)
        return outcome

    def record_summary(self, summary):
        self.summaries.append(summary)


class FakeRunner:
    def __init__(self, status=QueryStatus.SUCCEEDED, *, budget_blocked=False, fail=False):
        self.provider = SimpleNamespace(name="tavily")
        self.budget_guard = SimpleNamespace(check=self.check)
        self.status = status
        self.budget_blocked = budget_blocked
        self.fail = fail
        self.calls = []
        self.checks = []

    def check(self, query, *, now=None):
        self.checks.append(query)
        return SimpleNamespace(allowed=not self.budget_blocked,
                               reason=SimpleNamespace(value="DAILY_LIMIT_REACHED") if self.budget_blocked else None,
                               daily_usage=30 if self.budget_blocked else 0,
                               monthly_usage=30 if self.budget_blocked else 0)

    def run(self, query):
        self.calls.append(query)
        if self.fail:
            raise RuntimeError("provider failure")
        return SimpleNamespace(query=replace(query, status=QueryStatus.FAILED if self.budget_blocked else self.status,
                                             provenance={"budget_blocked": self.budget_blocked}))


class FakeLease:
    def __init__(self):
        self.claim = None
        self.logs = []

    def acquire(self, *, worker_id, claim_id, lease_seconds):
        if self.claim is not None:
            return False
        self.claim = claim_id
        return True

    def release(self, claim_id):
        if self.claim != claim_id:
            return False
        self.claim = None
        return True

    def record_summary(self, details):
        self.logs.append(details)


class FakeWorker:
    def __init__(self, name, calls, *, fail=False):
        self.name, self.calls, self.fail = name, calls, fail

    def run_once(self, *, dry_run=False):
        self.calls.append((self.name, dry_run))
        if self.fail:
            raise RuntimeError("worker failed")
        return SimpleNamespace(due_count=1)


class DiscoverySchedulingTests(unittest.TestCase):
    def worker(self, rows, runner=None, max_jobs=1):
        repo = FakeSchedules(rows)
        search = runner or FakeRunner()
        return DiscoveryWorker(schedules=repo, runner=search, worker_id="worker-a",
                               max_jobs=max_jobs, clock=lambda: NOW), repo, search

    def test_due_future_disabled_and_max_jobs(self):
        due = [schedule(text=f"query {n}") for n in range(3)]
        worker, repo, runner = self.worker(due + [schedule(next_run_at=NOW + timedelta(hours=1)),
                                             schedule(enabled=False)], max_jobs=2)
        summary = worker.run_once()
        self.assertEqual((summary.due_count, summary.claimed_count, summary.success_count), (2, 2, 2))
        self.assertEqual(len(runner.calls), 2)
        self.assertEqual(len(repo.summaries), 1)

    def test_dry_run_is_read_only_and_budget_visible(self):
        worker, repo, runner = self.worker([schedule()], FakeRunner(budget_blocked=True))
        summary = worker.run_once(dry_run=True)
        self.assertEqual((summary.due_count, summary.claimed_count), (1, 0))
        self.assertFalse(summary.previews[0]["budget_allowed"])
        self.assertFalse(repo.claims or repo.completions or repo.summaries or runner.calls)

    def test_budget_block_does_not_call_tavily_and_is_distinct(self):
        worker, repo, runner = self.worker([schedule()], FakeRunner(budget_blocked=True))
        summary = worker.run_once()
        self.assertEqual(summary.budget_blocked_count, 1)
        self.assertEqual(summary.failure_count, 0)
        self.assertEqual(repo.completions[0][1], "BUDGET_BLOCKED")

    def test_provider_failure_and_claim_identity(self):
        worker, repo, runner = self.worker([schedule()], FakeRunner(fail=True))
        summary = worker.run_once()
        self.assertEqual(summary.failure_count, 1)
        self.assertEqual(repo.completions[0][1], "FAILED")
        self.assertEqual(len(runner.calls), 1)
        query, claim = runner.calls[0], repo.claims[0]
        self.assertEqual((query.schedule_id, query.schedule_window_id,
                          query.schedule_claim_id, query.schedule_worker_id),
                         (claim.schedule.id, claim.window_id, claim.claim_id, "worker-a"))

    def test_double_claim_and_completion_idempotency_contract(self):
        row = schedule()
        repo = FakeSchedules([row])
        first = repo.claim(row, worker_id="a", claim_id=uuid4(), lease_seconds=120)
        self.assertIsNotNone(first)
        self.assertIsNone(repo.claim(row, worker_id="b", claim_id=uuid4(), lease_seconds=120))
        self.assertEqual(repo.complete(first, outcome="SUCCEEDED", query_id=uuid4(),
                                       failure_backoff_minutes=(60,), budget_delay_minutes=1440), "SUCCEEDED")
        self.assertEqual(repo.complete(first, outcome="FAILED", query_id=None,
                                       failure_backoff_minutes=(60,), budget_delay_minutes=1440), "SUCCEEDED")
        self.assertEqual(len(repo.completions), 1)

    def test_worker_requires_guarded_tavily(self):
        runner = FakeRunner()
        runner.budget_guard = None
        with self.assertRaises(ValueError):
            self.worker([schedule()], runner)

    def test_interval_and_time_validation(self):
        with self.assertRaises(ValueError):
            replace(schedule(), interval_minutes=60)
        with self.assertRaises(ValueError):
            replace(schedule(), next_run_at=datetime(2026, 10, 4))

    def test_four_worker_order_failure_isolation_and_dry_run(self):
        calls = []
        lease = FakeLease()
        names = ("discovery", "collection", "processing", "publish")
        runtime = AutomationRuntime(
            enabled=True, worker_enabled={name: True for name in names},
            workers={name: (lambda name=name: FakeWorker(name, calls, fail=name == "discovery"))
                     for name in names}, lease=lease, worker_id="automation")
        dry = runtime.run_once(dry_run=True)
        self.assertEqual(calls, [(name, True) for name in names])
        self.assertIsNone(lease.claim)
        self.assertFalse(lease.logs)
        self.assertEqual(dry.status, "PARTIAL")
        calls.clear()
        real = runtime.run_once()
        self.assertEqual(calls, [(name, False) for name in names])
        self.assertEqual(real.errors, {"discovery": "RuntimeError"})
        self.assertEqual(len(lease.logs), 1)
        self.assertIsNone(lease.claim)

    def test_automation_discovery_flag_and_kill_switch(self):
        calls = []
        names = ("discovery", "collection", "processing", "publish")
        workers = {name: (lambda name=name: FakeWorker(name, calls)) for name in names}
        lease = FakeLease()
        disabled = AutomationRuntime(enabled=False, worker_enabled={name: True for name in names},
                                     workers=workers, lease=lease, worker_id="a")
        self.assertEqual(disabled.run_once().status, "DISABLED")
        self.assertFalse(calls or lease.logs)
        selective = AutomationRuntime(enabled=True,
                                      worker_enabled={"discovery": False, "collection": True,
                                                      "processing": False, "publish": False},
                                      workers=workers, lease=lease, worker_id="a")
        self.assertEqual(selective.run_once().status, "COMPLETED")
        self.assertEqual(calls, [("collection", False)])

    def test_safe_defaults_and_008_sql_contract(self):
        settings = load_settings(environ={})
        self.assertEqual((settings.discovery_daily_search_limit,
                          settings.discovery_monthly_search_limit,
                          settings.discovery_query_cooldown_hours), (30, 1000, 24))
        self.assertFalse(settings.automation_enabled or settings.discovery_worker_enabled)
        self.assertEqual((settings.discovery_max_jobs_per_run,
                          settings.discovery_default_interval_minutes), (1, 1440))
        sql = (Path(__file__).parents[1] / "agent_collect/migrations/008_discovery_scheduling.sql").read_text()
        for token in ("agent_discovery_schedules", "agent_discovery_windows",
                      "agent_list_due_discovery", "agent_claim_discovery",
                      "agent_finish_discovery", "pg_advisory_xact_lock",
                      "clock_timestamp()", "security definer set search_path=pg_catalog",
                      "agent_discovery_query_window_unique"):
            self.assertIn(token, sql)
        self.assertNotIn("p_now", sql)
        self.assertNotIn("drop table", sql.lower())
        self.assertNotIn("truncate", sql.lower())
        self.assertNotIn("grant update on public.agent_discovery_schedules", sql.lower())
        self.assertNotIn("public.opportunities", sql)
        self.assertIn("values ('AI conference Korea','en','KR'),('인공지능 컨퍼런스','ko','KR')", sql)
        self.assertIn("agent_register_discovery_schedule", sql)
        self.assertIn("agent_set_discovery_schedule_enabled", sql)
        self.assertIn("v_schedule.claim_expires_at>v_now", sql)
        self.assertIn("v_schedule.claim_expires_at<=v_now", sql)
        self.assertIn("v_query.status='SUCCEEDED'", sql)
        self.assertIn("query_id is null", sql)

    def test_supabase_rpc_adapter_preserves_claim_window(self):
        row = schedule()
        claim_id, window_id = uuid4(), uuid4()
        fields = {"id": str(row.id), "query_text": row.query_text,
                  "vertical": "conference", "language": "en", "country": "KR",
                  "interval_minutes": 1440, "next_run_at": NOW.isoformat(),
                  "enabled": True, "consecutive_failures": 0,
                  "claim_id": str(claim_id), "current_window_id": str(window_id),
                  "worker_id": "a", "claim_expires_at": (NOW + timedelta(minutes=2)).isoformat()}

        class Client:
            def __init__(self):
                self.calls = []

            def rpc(self, name, args):
                self.calls.append((name, args))
                self.name = name
                return self

            def execute(self):
                if self.name in {"agent_list_due_discovery", "agent_claim_discovery"}:
                    result = [fields]
                elif self.name == "agent_register_discovery_schedule":
                    result = {**fields, "enabled": False}
                elif self.name == "agent_set_discovery_schedule_enabled":
                    result = True
                else:
                    result = "SUCCEEDED"
                return SimpleNamespace(data=result)

        client = Client()
        adapter = SupabaseDiscoverySchedules(client)
        self.assertEqual(adapter.list_due(1)[0].id, row.id)
        claim = adapter.claim(row, worker_id="a", claim_id=claim_id, lease_seconds=120)
        self.assertEqual(claim.window_id, window_id)
        self.assertEqual(adapter.complete(claim, outcome="FAILED", query_id=None,
                                          failure_backoff_minutes=(60, 360, 1440),
                                          budget_delay_minutes=1440), "SUCCEEDED")
        self.assertEqual(client.calls[-1][1]["p_window_id"], str(window_id))
        self.assertNotIn("p_now", client.calls[-1][1])
        registered = adapter.register_reviewed(query_text="AI conference Korea", language="en",
                                               country="KR", interval_minutes=1440,
                                               reviewer="operator", reason="approved query")
        self.assertFalse(registered.enabled)
        self.assertTrue(adapter.set_enabled(row.id, enabled=True, reviewer="operator",
                                            reason="budget-reviewed"))


if __name__ == "__main__":
    unittest.main()
