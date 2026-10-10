"""PHASE 7.3C offline scheduling, claim and worker contract tests."""

import unittest
import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import uuid4

from agent_collect.collectors.planner import CollectionPlanner
from agent_collect.collectors.runner import CollectionRunner, RunOutcome
from agent_collect.collectors.transport import HttpResponse
from agent_collect.config.settings import load_settings
from agent_collect.registry.approval import SourceApprovalService
from agent_collect.registry.service import SourceRegistryService
from agent_collect.repositories.memory import (
    InMemoryCollectionRunRepository, InMemoryRawDocumentRepository, InMemorySourceRepository,
)
from agent_collect.repositories.source_review import InMemorySourceReviewRepository
from agent_collect.schemas.models import CollectionMethod, CollectionRun, SourceType
from agent_collect.schemas.status import RunStatus
from agent_collect.scheduling.policy import CollectionSchedulePolicy
from agent_collect.scheduling.repository import InMemoryCollectionScheduleRepository
from agent_collect.scheduling.models import window_id_for
from agent_collect.scheduling.supabase import SupabaseCollectionScheduleRepository
from agent_collect.scheduling.worker import CollectionWorker
from agent_collect.storage.raw import LocalRawContentStorage


NOW = datetime(2026, 10, 4, 10, tzinfo=timezone.utc)


class FakeTransport:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def fetch(self, url, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("simulated transport failure")
        return HttpResponse(200, {"content-type": "text/html"}, b"<html>AI conference</html>", url)


class FakeRunner:
    def __init__(self, success=True):
        self.calls = []
        self.success = success

    def run_source(self, source_id, *, trigger, worker_id, claim_id, schedule_window_id):
        self.calls.append(source_id)
        run = CollectionRun(
            id=uuid4(), source_id=source_id,
            status=RunStatus.SUCCEEDED if self.success else RunStatus.FAILED,
            started_at=NOW, finished_at=NOW,
            source_config_snapshot={"trigger": trigger, "worker_id": worker_id,
                                    "claim_id": str(claim_id),
                                    "schedule_window_id": str(schedule_window_id)},
        )
        return RunOutcome(run, None, None, None if self.success else "simulated failure")


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.sources = InMemorySourceRepository()
        self.registry = SourceRegistryService(self.sources)
        self.approval = SourceApprovalService(InMemorySourceReviewRepository(self.sources))
        self.server_now = NOW
        self.schedules = InMemoryCollectionScheduleRepository(
            self.sources, clock=lambda: self.server_now)
        self.policy = CollectionSchedulePolicy()

    def source(self, name="Example", url="https://example.org/events", *, status="ACTIVE"):
        source = self.registry.register(
            name=name, url=url, source_type=SourceType.OFFICIAL,
            collection_method=CollectionMethod.HTTP_HTML,
        ).source
        if status == "CANDIDATE":
            return source
        self.approval.begin_review(source.id, reviewer="human:test", reason="fixture review")
        if status == "REVIEW":
            return self.sources.get(source.id)
        self.approval.approve(source.id, reviewer="human:test", reason="approved fixture",
                              officiality_confirmed=True, collection_allowed=True)
        if status == "APPROVED":
            return self.sources.get(source.id)
        self.approval.activate(source.id, reviewer="human:test", reason="activate fixture")
        if status == "HOLD":
            return self.approval.hold(source.id, reviewer="human:test", reason="hold fixture")
        if status == "DISABLED":
            return self.approval.disable(source.id, reviewer="human:test", reason="disable fixture")
        return self.sources.get(source.id)

    def test_due_filters_status_future_inactive_and_limit(self):
        good = self.source()
        future = self.source("Future", "https://future.org/events")
        for status in ("CANDIDATE", "REVIEW", "APPROVED", "HOLD", "DISABLED"):
            bad = self.source(status, f"https://{status.lower()}.org/events", status=status)
            self.schedules.seed(bad, next_collection_at=NOW)
        inactive = self.source("Inactive", "https://inactive.org/events")
        self.sources._rows[inactive.id] = replace(inactive, is_active=False)
        self.schedules.seed(inactive, next_collection_at=NOW)
        self.schedules.seed(future, next_collection_at=NOW + timedelta(minutes=1))
        self.schedules.seed(good, next_collection_at=NOW)
        self.assertEqual([item.source.id for item in self.schedules.list_due(1)], [good.id])
        with self.assertRaises(ValueError):
            self.schedules.list_due(0)

    def test_future_caller_time_cannot_bypass_due_or_lease(self):
        source = self.source()
        self.schedules.seed(source, next_collection_at=NOW + timedelta(hours=1))
        runner = FakeRunner()
        summary = CollectionWorker(schedules=self.schedules, runner=runner,
                                   policy=self.policy, worker_id="A",
                                   clock=lambda: NOW).run_once(now=NOW + timedelta(days=10))
        self.assertEqual(summary.due_count, 0)
        self.assertEqual(runner.calls, [])
        with self.assertRaises(TypeError):
            self.schedules.claim(source.id, worker_id="A", claim_id=uuid4(),
                                 now=NOW + timedelta(days=10), policy=self.policy)
        self.assertIsNone(self.schedules.claim(source.id, worker_id="A", claim_id=uuid4(),
                                               policy=self.policy))
        self.server_now = NOW + timedelta(hours=1)
        claim = self.schedules.claim(source.id, worker_id="A", claim_id=uuid4(), policy=self.policy)
        self.assertIsNotNone(claim)
        with self.assertRaises(TypeError):
            self.schedules.claim(source.id, worker_id="B", claim_id=uuid4(),
                                 now=NOW + timedelta(days=10), policy=self.policy)
        self.assertIsNone(self.schedules.claim(source.id, worker_id="B", claim_id=uuid4(),
                                               policy=self.policy))

    def test_claim_double_domain_release_and_lease_recovery(self):
        first = self.source()
        second = self.source("Second", "https://example.org/other")
        self.schedules.seed(first, next_collection_at=NOW)
        self.schedules.seed(second, next_collection_at=NOW)
        one = self.schedules.claim(first.id, worker_id="A", claim_id=uuid4(), policy=self.policy)
        self.assertIsNotNone(one)
        self.assertIsNone(self.schedules.claim(first.id, worker_id="B", claim_id=uuid4(), policy=self.policy))
        self.assertIsNone(self.schedules.claim(second.id, worker_id="B", claim_id=uuid4(), policy=self.policy))
        self.schedules.complete(one, run=None, policy=self.policy)
        self.assertEqual(self.schedules.get(first.id).consecutive_failures, 1)
        second_claim = self.schedules.claim(second.id, worker_id="B", claim_id=uuid4(), policy=self.policy)
        self.assertIsNotNone(second_claim)
        # Simulate a dead worker. Its expired lease is reconciled and backed off.
        later = NOW + timedelta(minutes=31)
        self.server_now = later
        self.assertIsNone(self.schedules.claim(second.id, worker_id="C", claim_id=uuid4(), policy=self.policy))
        self.assertEqual(self.schedules.get(second.id).consecutive_failures, 1)
        recovered = self.schedules.get(second.id)
        self.assertNotEqual(recovered.window_id, second_claim.window_id)
        self.assertEqual(recovered.window_id,
                         window_id_for(second.id, recovered.next_collection_at))
        self.server_now = later + timedelta(minutes=30)
        self.assertIsNotNone(self.schedules.claim(second.id, worker_id="C", claim_id=uuid4(),
                                                  policy=self.policy))

    def test_expired_success_is_reconciled_without_duplicate(self):
        source = self.source()
        self.schedules.seed(source, next_collection_at=NOW)
        claim = self.schedules.claim(source.id, worker_id="A", claim_id=uuid4(), policy=self.policy)
        run = FakeRunner().run_source(source.id, trigger="scheduled", worker_id="A",
                                      claim_id=claim.claim_id, schedule_window_id=claim.window_id).run
        self.schedules.record_completed_run(source.id, claim.window_id, run)
        self.server_now = NOW + timedelta(minutes=31)
        self.assertIsNone(self.schedules.claim(source.id, worker_id="B", claim_id=uuid4(),
                                                policy=self.policy))
        row = self.schedules.get(source.id)
        self.assertEqual(row.consecutive_failures, 0)
        self.assertEqual(row.next_collection_at, run.finished_at + timedelta(hours=12))
        self.assertEqual(row.window_id, window_id_for(source.id, row.next_collection_at))
        self.assertEqual(self.schedules.complete(claim, run=run, policy=self.policy), RunStatus.SUCCEEDED)

    def test_success_reconciles_null_and_explicit_failure_completion(self):
        for explicit in (False, True):
            with self.subTest(explicit=explicit):
                source = self.source(f"Success-{explicit}", f"https://success-{explicit}.org/events")
                self.schedules.seed(source, next_collection_at=NOW)
                claim = self.schedules.claim(source.id, worker_id="A", claim_id=uuid4(), policy=self.policy)
                run = FakeRunner().run_source(source.id, trigger="scheduled", worker_id="A",
                                              claim_id=claim.claim_id, schedule_window_id=claim.window_id).run
                self.schedules.record_completed_run(source.id, claim.window_id, run)
                self.server_now = NOW + timedelta(minutes=5)
                stale_failure = replace(run, status=RunStatus.FAILED) if explicit else None
                self.assertEqual(self.schedules.complete(claim, run=stale_failure, policy=self.policy),
                                 RunStatus.SUCCEEDED)
                row = self.schedules.get(source.id)
                self.assertEqual(row.consecutive_failures, 0)
                self.assertIsNone(row.last_failure_at)
                self.assertEqual(row.next_collection_at, run.finished_at + timedelta(hours=12))
                self.server_now = NOW

    def test_completion_is_idempotent_and_mismatched_run_rejected(self):
        source = self.source()
        self.schedules.seed(source, next_collection_at=NOW)
        claim = self.schedules.claim(source.id, worker_id="A", claim_id=uuid4(), policy=self.policy)
        self.assertEqual(self.schedules.complete(claim, run=None, policy=self.policy), RunStatus.FAILED)
        next_at = self.schedules.get(source.id).next_collection_at
        self.server_now = NOW + timedelta(minutes=10)
        self.assertEqual(self.schedules.complete(claim, run=None, policy=self.policy), RunStatus.FAILED)
        self.assertEqual(self.schedules.get(source.id).consecutive_failures, 1)
        self.assertEqual(self.schedules.get(source.id).next_collection_at, next_at)
        wrong = FakeRunner().run_source(source.id, trigger="scheduled", worker_id="A",
                                        claim_id=claim.claim_id, schedule_window_id=claim.window_id).run
        with self.assertRaises(ValueError):
            self.schedules.complete(claim, run=wrong, policy=self.policy)

    def test_active_claim_rejects_run_from_different_claim(self):
        source = self.source()
        self.schedules.seed(source, next_collection_at=NOW)
        claim = self.schedules.claim(source.id, worker_id="A", claim_id=uuid4(), policy=self.policy)
        wrong = FakeRunner().run_source(source.id, trigger="scheduled", worker_id="A",
                                        claim_id=uuid4(), schedule_window_id=claim.window_id).run
        with self.assertRaises(ValueError):
            self.schedules.complete(claim, run=wrong, policy=self.policy)
        row = self.schedules.get(source.id)
        self.assertEqual(row.claim, claim)
        self.assertEqual(row.consecutive_failures, 0)

    def test_success_completion_twice_does_not_drift(self):
        source = self.source()
        self.schedules.seed(source, next_collection_at=NOW)
        claim = self.schedules.claim(source.id, worker_id="A", claim_id=uuid4(), policy=self.policy)
        run = FakeRunner().run_source(source.id, trigger="scheduled", worker_id="A",
                                      claim_id=claim.claim_id, schedule_window_id=claim.window_id).run
        self.assertEqual(self.schedules.complete(claim, run=run, policy=self.policy), RunStatus.SUCCEEDED)
        next_at = self.schedules.get(source.id).next_collection_at
        self.server_now = NOW + timedelta(minutes=10)
        self.assertEqual(self.schedules.complete(claim, run=run, policy=self.policy), RunStatus.SUCCEEDED)
        self.assertEqual(self.schedules.complete(claim, run=None, policy=self.policy), RunStatus.SUCCEEDED)
        self.assertEqual(self.schedules.get(source.id).next_collection_at, next_at)

    def test_late_runner_exception_after_persisted_success_has_no_backoff(self):
        source = self.source()
        self.schedules.seed(source, next_collection_at=NOW)
        schedules = self.schedules
        class LateExceptionRunner(FakeRunner):
            def run_source(self, source_id, *, trigger, worker_id, claim_id, schedule_window_id):
                outcome = super().run_source(source_id, trigger=trigger, worker_id=worker_id,
                                             claim_id=claim_id, schedule_window_id=schedule_window_id)
                schedules.record_completed_run(source_id, schedule_window_id, outcome.run)
                raise RuntimeError("late response handling failure")
        summary = CollectionWorker(schedules=schedules, runner=LateExceptionRunner(),
                                   policy=self.policy, worker_id="A", clock=lambda: NOW).run_once()
        self.assertEqual((summary.success_count, summary.failure_count), (1, 0))
        self.assertEqual(self.schedules.get(source.id).consecutive_failures, 0)
        self.assertEqual(self.schedules.get(source.id).next_collection_at, NOW + timedelta(hours=12))

    def test_lost_success_completion_response_reconciles_without_drift(self):
        source = self.source()
        class LostResponseSchedules(InMemoryCollectionScheduleRepository):
            first = True
            def complete(self, claim, *, run, policy):
                result = super().complete(claim, run=run, policy=policy)
                if self.first:
                    self.first = False
                    raise RuntimeError("response lost after commit")
                return result
        schedules = LostResponseSchedules(self.sources, clock=lambda: NOW)
        schedules.seed(source, next_collection_at=NOW)
        summary = CollectionWorker(schedules=schedules, runner=FakeRunner(),
                                   policy=self.policy, worker_id="A", clock=lambda: NOW).run_once()
        self.assertEqual((summary.success_count, summary.failure_count), (1, 0))
        self.assertEqual(schedules.get(source.id).next_collection_at, NOW + timedelta(hours=12))
        self.assertEqual(schedules.get(source.id).consecutive_failures, 0)

    def test_repeated_failures_require_review_without_changing_lifecycle(self):
        source = self.source()
        self.schedules.seed(source, next_collection_at=NOW)
        current = NOW
        for count in range(1, 5):
            self.server_now = current
            claim = self.schedules.claim(source.id, worker_id="A", claim_id=uuid4(),
                                         policy=self.policy)
            self.assertIsNotNone(claim)
            self.schedules.complete(claim, run=None, policy=self.policy)
            row = self.schedules.get(source.id)
            self.assertEqual(row.consecutive_failures, count)
            current = row.next_collection_at
        self.assertTrue(row.review_needed)
        self.server_now = current
        self.assertEqual(self.schedules.list_due(1), [])
        self.assertEqual(self.sources.get(source.id).status.value, "ACTIVE")

    def test_policy_success_failure_review_and_timezone(self):
        self.assertEqual(self.policy.after_success(NOW, 360), NOW + timedelta(hours=6))
        self.assertEqual([self.policy.after_failure(NOW, n) - NOW for n in range(1, 6)],
                         [timedelta(minutes=30), timedelta(hours=2), timedelta(hours=6),
                          timedelta(hours=24), timedelta(hours=24)])
        self.assertTrue(self.policy.review_needed(4))
        with self.assertRaises(ValueError):
            self.policy.after_success(NOW.replace(tzinfo=None), 720)

    def test_window_identity_is_stable_across_timezone_and_changes_at_next_due(self):
        source_id = uuid4()
        same_in_kst = NOW.astimezone(timezone(timedelta(hours=9)))
        self.assertEqual(window_id_for(source_id, NOW), window_id_for(source_id, same_in_kst))
        self.assertNotEqual(window_id_for(source_id, NOW),
                            window_id_for(source_id, NOW + timedelta(minutes=30)))
        self.assertNotEqual(window_id_for(source_id, NOW), window_id_for(uuid4(), NOW))
        with self.assertRaises(ValueError):
            window_id_for(source_id, NOW.replace(tzinfo=None))

    def test_scheduled_run_requires_canonical_window_and_unique_source_window(self):
        source = self.source()
        repository = InMemoryCollectionRunRepository()
        base = dict(id=uuid4(), source_id=source.id, status=RunStatus.RUNNING,
                    started_at=NOW)
        for window in (None, "", "not-a-uuid", str(uuid4()).upper()):
            with self.subTest(window=window), self.assertRaises(ValueError):
                CollectionRun(**base, source_config_snapshot={
                    "trigger": "scheduled", "schedule_window_id": window})
        window = window_id_for(source.id, NOW)
        first = CollectionRun(**base, source_config_snapshot={
            "trigger": "scheduled", "schedule_window_id": str(window)})
        repository.save(first)
        repository.save(replace(first, status=RunStatus.SUCCEEDED, finished_at=NOW))
        with self.assertRaises(ValueError):
            repository.save(replace(first, id=uuid4()))
        repository.save(replace(first, id=uuid4(), source_config_snapshot={
            "trigger": "scheduled",
            "schedule_window_id": str(window_id_for(source.id, NOW + timedelta(hours=12)))}))
        repository.save(replace(first, id=uuid4(), source_id=uuid4()))
        repository.save(CollectionRun(**{**base, "id": uuid4()}, source_config_snapshot={
            "trigger": "manual"}))

    def test_approved_interval_cannot_change_without_review(self):
        approved = self.source(status="APPROVED")
        with self.assertRaises(ValueError):
            self.registry.update(approved.id, collection_interval_minutes=360)
        active = self.source("Active", "https://active.org/events")
        with self.assertRaises(ValueError):
            self.registry.update(active.id, collection_interval_minutes=360)

    def test_worker_dry_run_success_failure_and_max_jobs(self):
        first = self.source()
        second = self.source("Second", "https://other.org/events")
        for source in (first, second):
            self.schedules.seed(source, next_collection_at=NOW)
        runner = FakeRunner()
        policy = replace(self.policy, max_jobs_per_run=1)
        worker = CollectionWorker(schedules=self.schedules, runner=runner, policy=policy,
                                  worker_id="worker-A", clock=lambda: NOW)
        preview = worker.run_once(dry_run=True)
        self.assertEqual(preview.due_count, 1)
        self.assertEqual(preview.due[0].collection_method, CollectionMethod.HTTP_HTML)
        self.assertEqual(runner.calls, [])
        self.assertIsNone(self.schedules.get(preview.due[0].source_id).claim)
        self.assertEqual(self.schedules.summaries, [])
        summary = worker.run_once()
        self.assertEqual((summary.claimed_count, summary.success_count), (1, 1))
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(self.schedules.get(runner.calls[0]).next_collection_at, NOW + timedelta(hours=12))
        failed = CollectionWorker(schedules=self.schedules, runner=FakeRunner(False),
                                  policy=self.policy, worker_id="worker-B", clock=lambda: NOW).run_once()
        self.assertEqual(failed.failure_count, 1)
        self.assertEqual(len(self.schedules.summaries), 2)

    def test_real_collection_runner_integration_without_network(self):
        source = self.source()
        self.schedules.seed(source, next_collection_at=NOW)
        transport = FakeTransport()
        runs = InMemoryCollectionRunRepository()
        documents = InMemoryRawDocumentRepository()
        with TemporaryDirectory() as raw_dir:
            runner = CollectionRunner(
                sources=self.sources, runs=runs, documents=documents,
                storage=LocalRawContentStorage(Path(raw_dir)),
                planner=CollectionPlanner(transport), settings=load_settings(environ={}),
                environ={},
            )
            summary = CollectionWorker(schedules=self.schedules, runner=runner,
                                       policy=self.policy, worker_id="worker-A",
                                       clock=lambda: NOW).run_once()
            self.assertEqual((summary.success_count, transport.calls, len(documents._rows)), (1, 1, 1))
            run = next(iter(runs._rows.values()))
            self.assertEqual(run.source_config_snapshot["trigger"], "scheduled")
            self.assertEqual(run.source_config_snapshot["worker_id"], "worker-A")
            self.assertEqual(run.source_config_snapshot["schedule_window_id"],
                             str(next(iter(self.schedules._completed))[1]))
            self.assertIsNone(self.schedules.get(source.id).claim)

    def test_migration_is_agent_only_and_atomic_contract_is_present(self):
        sql = (Path(__file__).parents[1] / "agent_collect/migrations/005_collection_scheduling.sql").read_text()
        lowered = sql.lower()
        for forbidden in ("drop table", "truncate", "public.opportunities", "public.sources",
                          "public.categories", "public.tags"):
            self.assertNotIn(forbidden, lowered)
        for required in ("agent_source_schedules", "agent_list_due_sources",
                         "agent_claim_due_source", "agent_finish_collection",
                         "pg_try_advisory_xact_lock", "for update",
                         "agent_runs_scheduled_window_unique", "claim_expires_at",
                         "agent_runs_scheduled_window_required", "agent_schedule_window_id",
                         "agent_scheduled_run_claim_guard", "for share", "extract(epoch"):
            self.assertIn(required, lowered)
        for required in ("agent_collection_completions", "agent_scheduled_run_terminal_guard",
                         "v_success.finished_at", "return v_prior.outcome",
                         "source_config_snapshot ->> 'claim_id' = p_claim_id::text"):
            self.assertIn(required, lowered)
        finish = lowered.split("create function public.agent_finish_collection", 1)[1]
        self.assertLess(finish.index("select * into v_success"), finish.index("v_outcome := 'failed'"))
        self.assertNotIn("p_now", lowered)
        self.assertNotIn("p_finished_at", lowered)
        self.assertNotIn("gen_random_uuid", lowered)
        self.assertIn("clock_timestamp()", lowered)
        guard = lowered.split("create function public.agent_scheduled_run_claim_guard", 1)[1].split(
            "create trigger agent_scheduled_run_claim_before_insert", 1)[0]
        for required in ("where source_id = new.source_id for share",
                         "v_schedule.window_id::text", "v_schedule.claim_id::text",
                         "v_schedule.claimed_by", "v_schedule.claim_expires_at <= clock_timestamp()"):
            self.assertIn(required, guard)
        self.assertLess(lowered.index("create table public.agent_source_schedules"),
                        lowered.index("create function public.agent_claim_due_source"))

    def test_trigger_lock_privileges_are_isolated_from_service_role(self):
        sql = (Path(__file__).parents[1] / "agent_collect/migrations/005_collection_scheduling.sql").read_text().lower()
        grants = re.findall(r"\bgrant\s+[^;]+;", sql)
        schedule_grants = [grant for grant in grants if "on public.agent_source_schedules" in grant]
        self.assertEqual(schedule_grants,
                         ["grant select on public.agent_source_schedules to service_role;"])
        self.assertIn("revoke all on public.agent_source_schedules from public, anon, authenticated, service_role;", sql)
        self.assertIn("alter table public.agent_source_schedules enable row level security;", sql)
        for name, trigger_name in (
            ("agent_scheduled_run_claim_guard", "agent_scheduled_run_claim_before_insert"),
            ("agent_scheduled_run_terminal_guard", "agent_scheduled_run_terminal_before_update"),
        ):
            block = sql.split(f"create function public.{name}() returns trigger", 1)[1].split(
                f"create trigger {trigger_name}", 1)[0]
            self.assertIn("language plpgsql security definer set search_path = pg_catalog", block)
            self.assertIn(f"alter function public.{name}() owner to postgres;", block)
            self.assertIn("from public.agent_sources", block)
            self.assertIn("public.agent_schedulable_source(v_source)", block)
            self.assertIn("from public.agent_source_schedules", block)
            self.assertIn("for share", block)
            for predicate in ("v_schedule.claim_id::text", "v_schedule.window_id::text",
                              "v_schedule.claimed_by", "v_schedule.claim_expires_at <= clock_timestamp()"):
                self.assertIn(predicate, block)
            self.assertIn(f"revoke all on function public.{name}()\n  from public, anon, authenticated, service_role;", sql)
            self.assertNotIn("execute format", block)
        self.assertIn("create trigger agent_scheduled_run_claim_before_insert", sql)
        self.assertIn("create trigger agent_scheduled_run_terminal_before_update", sql)
        self.assertIn("grant execute on function public.agent_claim_due_source", sql)
        self.assertIn("grant execute on function public.agent_finish_collection", sql)
        self.assertNotIn("grant execute on function public.agent_scheduled_run_claim_guard", sql)
        self.assertNotIn("grant execute on function public.agent_scheduled_run_terminal_guard", sql)

    def test_offline_claim_identity_and_lease_fail_closed(self):
        source = self.source()
        self.schedules.seed(source, next_collection_at=NOW)
        claim = self.schedules.claim(source.id, worker_id="worker-A", claim_id=uuid4(), policy=self.policy)
        self.assertIsNotNone(claim)
        for worker_id, claim_id, window_id in (
            ("worker-B", claim.claim_id, claim.window_id),
            ("worker-A", uuid4(), claim.window_id),
            ("worker-A", claim.claim_id, uuid4()),
        ):
            with self.subTest(worker_id=worker_id, claim_id=claim_id, window_id=window_id):
                bad_run = FakeRunner().run_source(
                    source.id, trigger="scheduled", worker_id=worker_id,
                    claim_id=claim_id, schedule_window_id=window_id).run
                with self.assertRaises(ValueError):
                    self.schedules.complete(claim, run=bad_run, policy=self.policy)
                self.assertEqual(self.schedules.get(source.id).claim, claim)
        self.server_now = claim.expires_at + timedelta(seconds=1)
        self.assertIsNone(self.schedules.claim(source.id, worker_id="worker-B",
                                               claim_id=uuid4(), policy=self.policy))
        self.assertEqual(self.schedules.get(source.id).consecutive_failures, 1)

    def test_supabase_port_only_calls_agent_rpcs_and_log(self):
        source = self.source()
        class Query:
            def __init__(self, client, name):
                self.client, self.name = client, name
            def execute(self):
                return SimpleNamespace(data=self.client.responses.get(self.name, []))
            def insert(self, row):
                self.client.log_row = row
                return self
        class Client:
            def __init__(self):
                self.calls = []
                self.responses = {}
                self.log_row = None
            def rpc(self, name, params):
                self.calls.append((name, params))
                return Query(self, name)
            def table(self, name):
                self.calls.append(("table", name))
                return Query(self, name)
        client = Client()
        client.responses["agent_list_due_sources"] = [{
            "source_id": str(source.id), "window_id": str(uuid4()),
            "interval_minutes": 720, "next_collection_at": NOW.isoformat(),
        }]
        repo = SupabaseCollectionScheduleRepository(client, self.sources)
        due = repo.list_due(1)
        self.assertEqual(due[0].source.id, source.id)
        claim_id = uuid4()
        client.responses["agent_claim_due_source"] = [{
            "source_id": str(source.id), "window_id": str(due[0].window_id),
            "claim_id": str(claim_id), "claimed_by": "worker-A",
            "claimed_at": NOW.isoformat(),
            "claim_expires_at": (NOW + timedelta(minutes=30)).isoformat(),
        }]
        claim = repo.claim(source.id, worker_id="worker-A", claim_id=claim_id,
                           policy=self.policy)
        self.assertEqual(claim.claim_id, claim_id)
        run = FakeRunner().run_source(source.id, trigger="scheduled", worker_id="worker-A",
                                      claim_id=claim_id, schedule_window_id=due[0].window_id).run
        client.responses["agent_finish_collection"] = "SUCCEEDED"
        self.assertEqual(repo.complete(claim, run=run, policy=self.policy), RunStatus.SUCCEEDED)
        summary = CollectionWorker(schedules=self.schedules, runner=FakeRunner(),
                                   policy=self.policy, worker_id="worker-A",
                                   clock=lambda: NOW).run_once(dry_run=True)
        repo.record_summary(summary)
        self.assertEqual([name for name, _ in client.calls], [
            "agent_list_due_sources", "agent_claim_due_source", "agent_finish_collection", "table",
        ])
        self.assertEqual(client.calls[0][1], {"p_limit": 1})
        self.assertFalse(any(key in params for name, params in client.calls[:3]
                             for key in ("p_now", "p_finished_at")))
        self.assertEqual(client.calls[-1][1], "agent_logs")
        self.assertEqual(client.log_row["event_type"], "COLLECTION_WORKER_RUN")


if __name__ == "__main__":
    unittest.main()
