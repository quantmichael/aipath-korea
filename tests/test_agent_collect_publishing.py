"""PHASE 9 one-shot publish/automation contracts; no network or Service DB."""

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from agent_collect.adapters.service_db.adapter import SupabaseServiceDBAdapter
from agent_collect.automation.runtime import AutomationRuntime
from agent_collect.config.settings import load_settings
from agent_collect.publishing.worker import PublishClaim, PublishJob, PublishWorker
from tests.test_agent_collect_service_publish import FakeLedger, FakeTables, candidate


class FakeJobs:
    def __init__(self, ledger):
        self.ledger = ledger
        self.job = PublishJob(uuid4(), ledger.publication.agent_item_id)
        self.current = None
        self.completions = {}
        self.summaries = []
        self.claims = 0

    def preview(self, limit):
        expired = (self.current is not None and
                   self.current.expires_at <= datetime.now(timezone.utc))
        return [self.job][:limit] if self.ledger.status in {"READY", "FAILED"} or expired else []

    def list_due(self, limit):
        return self.preview(limit)

    def claim(self, job, *, worker_id, claim_id, lease_seconds):
        if job != self.job or not self.preview(1):
            return None
        self.ledger.status = "PUBLISHING"
        self.claims += 1
        self.current = PublishClaim(job.id, job.candidate_id, claim_id, worker_id,
                                    datetime.now(timezone.utc) + timedelta(seconds=lease_seconds))
        return self.current

    def complete(self, claim, *, outcome, service_id=None, slug=None,
                 service_status=None, published_at=None, error_code=None):
        if claim.claim_id in self.completions:
            return self.completions[claim.claim_id]
        if self.current is None or claim.claim_id != self.current.claim_id:
            raise RuntimeError("stale claim")
        self.completions[claim.claim_id] = outcome
        self.ledger.status = outcome
        if service_id:
            self.ledger.service_opportunity_id = service_id
        self.current = None
        return outcome

    def record_summary(self, summary):
        self.summaries.append(summary)


class PublishWorkerTests(unittest.TestCase):
    def setUp(self):
        now = datetime.now(timezone.utc)
        self.item = replace(candidate(), verified_at=now,
                            application_start_at=now-timedelta(days=5),
                            application_deadline_at=now+timedelta(days=40),
                            event_start_at=now+timedelta(days=100),
                            event_end_at=now+timedelta(days=101))
        self.tables = FakeTables()
        self.ledger = FakeLedger(self.item)
        self.jobs = FakeJobs(self.ledger)
        self.adapter = SupabaseServiceDBAdapter(tables=self.tables, ledger=self.ledger,
                                                publish_enabled=True)
        self.worker = PublishWorker(jobs=self.jobs, ledger=self.ledger,
                                    adapter=self.adapter, worker_id="worker-a")

    def test_dry_run_mapped_payload_without_claim_or_insert(self):
        result = self.worker.run_once(dry_run=True)
        self.assertEqual((result.due_count, result.claimed_count), (1, 0))
        self.assertTrue(result.previews[0]["eligible"])
        self.assertEqual(result.previews[0]["insert_payload"]["source_id"],
                         str(self.tables.source_id))
        self.assertEqual((self.jobs.claims, self.tables.insert_calls), (0, 0))
        self.assertEqual(self.ledger.status, "READY")
        self.assertFalse(self.jobs.summaries)

    def test_ready_only_and_publish_success_readback_idempotency(self):
        result = self.worker.run_once()
        self.assertEqual((result.claimed_count, result.published_count), (1, 1))
        self.assertEqual(self.ledger.status, "PUBLISHED")
        self.assertEqual(self.tables.insert_calls, 1)
        again = self.worker.run_once()
        self.assertEqual(again.due_count, 0)
        self.assertEqual(self.tables.insert_calls, 1)
        self.assertEqual(len(self.jobs.completions), 1)

    def test_completion_log_failure_does_not_retry_service_insert(self):
        original_log = self.ledger.log
        def flaky_log(candidate_id, *, event_type, details):
            if event_type == "SERVICE_PUBLISHED":
                raise RuntimeError("audit transport unavailable")
            return original_log(candidate_id, event_type=event_type, details=details)
        self.ledger.log = flaky_log
        result = self.worker.run_once()
        self.assertEqual(result.published_count, 1)
        self.assertEqual(self.tables.insert_calls, 1)
        self.assertEqual(self.ledger.status, "PUBLISHED")

    def test_review_queue_never_claimed(self):
        self.ledger.verification_result = "REVIEW_REQUIRED"
        with self.assertRaises(ValueError):
            self.worker._candidate(self.item.agent_item_id)
        self.ledger.status = "HELD"
        self.assertEqual(self.worker.run_once().claimed_count, 0)
        self.assertEqual(self.tables.insert_calls, 0)

    def test_conflict_holds_without_insert(self):
        self.tables.opportunities.append({
            "id": str(uuid4()), "source_id": str(self.tables.source_id),
            "external_id": "other", "slug": "other", "title": "other",
            "organizer": "other", "official_url": self.item.official_url,
            "event_start_at": self.item.event_start_at.isoformat(),
        })
        result = self.worker.run_once()
        self.assertEqual(result.duplicate_count, 1)
        self.assertEqual(self.ledger.status, "HELD")
        self.assertEqual(self.tables.insert_calls, 0)

    def test_insert_committed_before_completion_recovers_without_second_insert(self):
        payload = self.adapter.dry_run(self.item).insert_payload
        first = self.tables.insert_opportunity(payload)
        self.assertEqual(self.tables.insert_calls, 1)
        old = self.jobs.claim(self.jobs.job, worker_id="crashed", claim_id=uuid4(),
                              lease_seconds=300)
        self.jobs.current = PublishClaim(old.job_id, old.candidate_id, old.claim_id,
                                         old.worker_id,
                                         datetime.now(timezone.utc)-timedelta(seconds=1))
        result = self.worker.run_once()
        self.assertEqual(result.published_count, 1)
        self.assertEqual(self.ledger.service_opportunity_id.hex,
                         first["id"].replace("-", ""))
        self.assertEqual(self.tables.insert_calls, 1)

    def test_disabled_publish_rejects_real_pass(self):
        self.adapter.publish_enabled = False
        with self.assertRaises(RuntimeError):
            self.worker.run_once()
        self.assertEqual(self.jobs.claims, 0)

    def test_agent_source_must_still_be_reviewed_active(self):
        self.adapter.agent_sources = SimpleNamespace(get=lambda source_id: None)
        plan = self.adapter.dry_run(self.item)
        self.assertIn("AGENT_SOURCE_NOT_ACTIVE_APPROVED", plan.reasons)
        result = self.worker.run_once()
        self.assertEqual(result.review_count, 1)
        self.assertEqual(self.tables.insert_calls, 0)

    def test_agent_source_identity_cannot_be_rebound(self):
        source = SimpleNamespace(canonical_url="https://different.org", url="https://different.org",
                                 name="AI Society")
        self.adapter.agent_sources = SimpleNamespace(get=lambda source_id: source)
        with patch("agent_collect.adapters.service_db.adapter.source_is_collectible", return_value=True):
            plan = self.adapter.dry_run(self.item)
        self.assertIn("AGENT_SOURCE_IDENTITY_MISMATCH", plan.reasons)

    def test_claim_competition_and_duplicate_completion(self):
        claim = self.jobs.claim(self.jobs.job, worker_id="a", claim_id=uuid4(), lease_seconds=300)
        self.assertIsNotNone(claim)
        self.assertIsNone(self.jobs.claim(self.jobs.job, worker_id="b",
                                          claim_id=uuid4(), lease_seconds=300))
        self.assertEqual(self.jobs.complete(claim, outcome="FAILED", error_code="test"), "FAILED")
        self.assertEqual(self.jobs.complete(claim, outcome="FAILED", error_code="test"), "FAILED")
        self.assertEqual(len(self.jobs.completions), 1)

    def test_expired_lease_reclaim_and_old_claim_rejected(self):
        old = self.jobs.claim(self.jobs.job, worker_id="a", claim_id=uuid4(),
                              lease_seconds=300)
        self.jobs.current = PublishClaim(old.job_id, old.candidate_id, old.claim_id,
                                         old.worker_id,
                                         datetime.now(timezone.utc)-timedelta(seconds=1))
        new = self.jobs.claim(self.jobs.job, worker_id="b", claim_id=uuid4(),
                              lease_seconds=300)
        self.assertNotEqual(old.claim_id, new.claim_id)
        with self.assertRaises(RuntimeError):
            self.jobs.complete(old, outcome="FAILED", error_code="late")


class FakeLease:
    def __init__(self):
        self.held = False
        self.logs = []
        self.releases = 0

    def acquire(self, **kwargs):
        if self.held:
            return False
        self.held = True
        return True

    def release(self, claim_id):
        self.held = False
        self.releases += 1
        return True

    def record_summary(self, details):
        self.logs.append(details)


class FakeWorker:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    def run_once(self, *, dry_run=False):
        self.calls.append(dry_run)
        if self.fail:
            raise RuntimeError("worker failed")
        return SimpleNamespace(due_count=1)


class AutomationTests(unittest.TestCase):
    def test_kill_switch_and_defaults(self):
        settings = load_settings(environ={})
        self.assertFalse(settings.automation_enabled)
        self.assertFalse(settings.publish_worker_enabled)
        self.assertFalse(settings.publish_enabled)
        lease, worker = FakeLease(), FakeWorker()
        runtime = AutomationRuntime(enabled=False, worker_enabled={"collection": True},
                                    workers={"collection": lambda: worker},
                                    lease=lease, worker_id="test")
        self.assertEqual(runtime.run_once().status, "DISABLED")
        self.assertFalse(worker.calls)
        self.assertFalse(lease.logs)

    def test_dry_run_never_locks_or_logs(self):
        lease, worker = FakeLease(), FakeWorker()
        runtime = AutomationRuntime(enabled=True, worker_enabled={"collection": True},
                                    workers={"collection": lambda: worker},
                                    lease=lease, worker_id="test")
        result = runtime.run_once(dry_run=True)
        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(worker.calls, [True])
        self.assertFalse(lease.held or lease.logs)

    def test_run_lock_isolation_and_release(self):
        lease = FakeLease()
        collection, processing, publish = FakeWorker(fail=True), FakeWorker(), FakeWorker()
        runtime = AutomationRuntime(
            enabled=True, worker_enabled={"collection": True, "processing": True,
                                          "publish": False},
            workers={"collection": lambda: collection,
                     "processing": lambda: processing, "publish": lambda: publish},
            lease=lease, worker_id="test")
        result = runtime.run_once()
        self.assertEqual(result.status, "PARTIAL")
        self.assertEqual(processing.calls, [False])
        self.assertFalse(publish.calls)
        self.assertEqual(lease.releases, 1)
        self.assertEqual(lease.logs[0]["errors"], {"collection": "RuntimeError"})
        lease.held = True
        self.assertEqual(runtime.run_once().status, "LOCKED")

    def test_processing_failure_does_not_block_publish(self):
        lease = FakeLease()
        collection, processing, publish = FakeWorker(), FakeWorker(fail=True), FakeWorker()
        runtime = AutomationRuntime(
            enabled=True, worker_enabled={"collection": True, "processing": True,
                                          "publish": True},
            workers={"collection": lambda: collection,
                     "processing": lambda: processing, "publish": lambda: publish},
            lease=lease, worker_id="test")
        result = runtime.run_once()
        self.assertEqual(result.status, "PARTIAL")
        self.assertEqual(publish.calls, [False])
        self.assertEqual(result.errors, {"processing": "RuntimeError"})

    def test_migration_agent_only_atomic_rpc_contract(self):
        sql = (Path(__file__).parents[1] / "agent_collect/migrations/007_publish_automation.sql").read_text()
        for name in ("agent_list_due_publish", "agent_claim_publish",
                     "agent_finish_publish", "agent_acquire_automation_lease",
                     "agent_release_automation_lease", "agent_publish_completions"):
            self.assertIn(name, sql)
        self.assertIn("security definer set search_path=pg_catalog", sql)
        self.assertIn("clock_timestamp()", sql)
        self.assertIn("agent_publish_insert_guard", sql)
        self.assertIn("grant select,insert on public.agent_publish_queue to service_role", sql)
        self.assertNotIn("grant update on public.agent_publish_queue to service_role", sql)
        self.assertNotIn("p_now", sql)
        for forbidden in ("drop table", "truncate", "alter table public.opportunities",
                          "alter table public.sources", "alter table public.categories"):
            self.assertNotIn(forbidden, sql.lower())


if __name__ == "__main__":
    unittest.main()
