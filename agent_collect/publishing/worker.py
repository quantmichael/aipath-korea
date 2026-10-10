"""Publish READY Agent snapshots through the existing ServiceDBAdapter only."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID, uuid4

from agent_collect.adapters.service_db.adapter import (
    SupabaseServiceDBAdapter, publish_candidate_from_snapshot,
)
from agent_collect.adapters.service_db.contract import PublishAction
from agent_collect.repositories.publish_ledger import PublishLedger


@dataclass(frozen=True)
class PublishJob:
    id: UUID
    candidate_id: UUID


@dataclass(frozen=True)
class PublishClaim:
    job_id: UUID
    candidate_id: UUID
    claim_id: UUID
    worker_id: str
    expires_at: datetime


@dataclass(frozen=True)
class PublishSummary:
    worker_id: str
    started_at: datetime
    finished_at: datetime
    dry_run: bool
    due_count: int
    claimed_count: int
    published_count: int
    duplicate_count: int
    review_count: int
    failed_count: int
    previews: tuple[dict[str, Any], ...]


class PublishJobs(Protocol):
    def preview(self, limit: int) -> list[PublishJob]: ...
    def list_due(self, limit: int) -> list[PublishJob]: ...
    def claim(self, job: PublishJob, *, worker_id: str, claim_id: UUID,
              lease_seconds: int) -> PublishClaim | None: ...
    def complete(self, claim: PublishClaim, *, outcome: str,
                 service_id: UUID | None = None, slug: str | None = None,
                 service_status: str | None = None, published_at: datetime | None = None,
                 error_code: str | None = None) -> str: ...
    def record_summary(self, summary: PublishSummary) -> None: ...


class _ClaimedLedger:
    """Scope Adapter result writes to a DB claim; all reads/logs delegate."""

    def __init__(self, inner: PublishLedger, jobs: PublishJobs, claim: PublishClaim) -> None:
        self.inner, self.jobs, self.lease = inner, jobs, claim

    def get(self, candidate_id: UUID):
        if candidate_id != self.lease.candidate_id:
            raise ValueError("candidate outside publish claim")
        return self.inner.get(candidate_id)

    def claim(self, candidate_id: UUID, *, prior_attempts: int) -> bool:
        raise RuntimeError("publish claim already owned by worker")

    def mark_published(self, candidate_id: UUID, *, service_id: UUID, slug: str,
                       service_status: str, published_at: datetime) -> None:
        if candidate_id != self.lease.candidate_id:
            raise ValueError("candidate outside publish claim")
        outcome = self.jobs.complete(self.lease, outcome="PUBLISHED", service_id=service_id,
                                     slug=slug, service_status=service_status,
                                     published_at=published_at)
        if outcome != "PUBLISHED":
            raise RuntimeError("publish completion not confirmed")

    def mark_failed(self, candidate_id: UUID, *, code: str) -> None:
        if candidate_id != self.lease.candidate_id:
            raise ValueError("candidate outside publish claim")
        if self.jobs.complete(self.lease, outcome="FAILED", error_code=code) != "FAILED":
            raise RuntimeError("publish failure reconciled to a different terminal outcome")

    def log(self, candidate_id: UUID, *, event_type: str, details: dict[str, Any]) -> None:
        self.inner.log(candidate_id, event_type=event_type, details=details)


class PublishWorker:
    def __init__(self, *, jobs: PublishJobs, ledger: PublishLedger,
                 adapter: SupabaseServiceDBAdapter, worker_id: str,
                 max_jobs: int = 1, lease_seconds: int = 300) -> None:
        if not worker_id.strip() or not 1 <= max_jobs <= 100 or not 1 <= lease_seconds <= 86400:
            raise ValueError("invalid publish worker policy")
        self.jobs, self.ledger, self.adapter = jobs, ledger, adapter
        self.worker_id, self.max_jobs, self.lease_seconds = worker_id, max_jobs, lease_seconds

    def _candidate(self, candidate_id: UUID):
        queue = self.ledger.get(candidate_id)
        if queue is None or queue.verification_result != "VERIFIED" or queue.gate_status != "PUBLISH_READY":
            raise ValueError("publish queue verification is not ready")
        candidate = publish_candidate_from_snapshot(queue.snapshot)
        if candidate.agent_item_id != candidate_id:
            raise ValueError("publish snapshot candidate mismatch")
        return candidate

    def run_once(self, *, dry_run: bool = False) -> PublishSummary:
        started = datetime.now(timezone.utc)
        if not dry_run and not self.adapter.publish_enabled:
            raise RuntimeError("Service publishing is disabled")
        due = self.jobs.preview(self.max_jobs) if dry_run else self.jobs.list_due(self.max_jobs)
        claimed = published = duplicate = review = failed = 0
        previews: list[dict[str, Any]] = []
        for job in due:
            if dry_run:
                try:
                    plan = self.adapter.dry_run(self._candidate(job.candidate_id), retryable=True)
                    previews.append({"candidate_id": str(job.candidate_id), "eligible": plan.eligible,
                                     "source_id": str(plan.source_id) if plan.source_id else None,
                                     "category_id": str(plan.category_id) if plan.category_id else None,
                                     "publication_status": plan.publication_status.value if plan.publication_status else None,
                                     "conflict": plan.conflict.status.value,
                                     "reasons": list(plan.reasons), "insert_payload": plan.insert_payload})
                except Exception as error:
                    previews.append({"candidate_id": str(job.candidate_id), "eligible": False,
                                     "reasons": [type(error).__name__]})
                continue
            claim = self.jobs.claim(job, worker_id=self.worker_id, claim_id=uuid4(),
                                    lease_seconds=self.lease_seconds)
            if claim is None:
                continue
            claimed += 1
            try:
                candidate = self._candidate(job.candidate_id)
                scoped = _ClaimedLedger(self.ledger, self.jobs, claim)
                adapter = SupabaseServiceDBAdapter(
                    tables=self.adapter.tables, ledger=scoped,
                    agent_sources=self.adapter.agent_sources,
                    publish_threshold=self.adapter.publish_threshold,
                    publish_enabled=self.adapter.publish_enabled,
                )
                result = adapter.publish(candidate, preclaimed=True)
                if result.action is PublishAction.CREATED:
                    published += 1
                elif result.action is PublishAction.SKIPPED_DUPLICATE:
                    # Own idempotency keys take Adapter._reconcile → CREATED.
                    # A different existing Opportunity is never counted as ours.
                    self.jobs.complete(claim, outcome="HELD", error_code="SERVICE_DUPLICATE")
                    duplicate += 1
                elif result.action is PublishAction.FAILED:
                    self.jobs.complete(claim, outcome="FAILED",
                                       error_code=result.error_code or "SERVICE_PUBLISH_FAILED")
                    failed += 1
                else:
                    self.jobs.complete(claim, outcome="HELD",
                                       error_code=result.error_code or "PUBLISH_REVIEW_REQUIRED")
                    review += 1
            except Exception as error:
                # A Service INSERT may have committed before an exception. Leave
                # the lease for a fresh readback/reconciliation after expiry.
                failed += 1
                previews.append({"candidate_id": str(job.candidate_id),
                                 "error": type(error).__name__, "recovery": "LEASE_RECHECK"})
        summary = PublishSummary(self.worker_id, started, datetime.now(timezone.utc), dry_run,
                                 len(due), claimed, published, duplicate, review, failed,
                                 tuple(previews))
        if not dry_run:
            self.jobs.record_summary(summary)
        return summary
