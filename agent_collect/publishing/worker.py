"""Publish READY Agent snapshots through the existing ServiceDBAdapter only."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol
from uuid import UUID, uuid4

from agent_collect.adapters.service_db.adapter import (
    SupabaseServiceDBAdapter, publish_candidate_from_snapshot,
)
from agent_collect.adapters.service_db.contract import PublishAction
from agent_collect.repositories.publish_ledger import PublishLedger
from agent_collect.metadata.evidence import usage_references_replayed
from agent_collect.metadata.supabase import SupabaseMetadataEvidence


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
    metadata_due_count: int = 0
    metadata_attempted_count: int = 0


@dataclass(frozen=True)
class MetadataPublishResult:
    candidate_id: UUID
    status: str
    opportunity_id: UUID | None = None
    reasons: tuple[str, ...] = ()


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
                 max_jobs: int = 1, lease_seconds: int = 300,
                 metadata: SupabaseMetadataEvidence | None = None,
                 metadata_publish_enabled: bool = False,
                 metadata_inputs: Callable[[UUID], dict[str, Any]] | None = None) -> None:
        if not worker_id.strip() or not 1 <= max_jobs <= 100 or not 1 <= lease_seconds <= 86400:
            raise ValueError("invalid publish worker policy")
        self.jobs, self.ledger, self.adapter = jobs, ledger, adapter
        self.worker_id, self.max_jobs, self.lease_seconds = worker_id, max_jobs, lease_seconds
        self.metadata = metadata
        self.metadata_publish_enabled = metadata_publish_enabled
        self.metadata_inputs = metadata_inputs

    def _confirmed_metadata_completion(self, candidate_id: UUID,
                                       prior: dict[str, Any] | None, *,
                                       expected_service_id: UUID | None = None) -> UUID | None:
        """Confirm the exact Candidate and Service identities on every readback."""
        if (prior is None or prior.get("candidate_id") != str(candidate_id) or
                prior.get("disposition") != "PUBLISHED"):
            return None
        service_id = UUID(prior["opportunity_id"])
        if expected_service_id is not None and service_id != expected_service_id:
            return None
        row = self.adapter.tables.get_opportunity(service_id)
        if (row is None or row.get("id") != str(service_id) or
                row.get("external_id") != f"runtime_agent_metadata:{candidate_id}"):
            return None
        return service_id

    def run_metadata_candidate(self, candidate_id: UUID, *, page: bytes | None,
                               page_url: str | None, fetched_at: datetime | None,
                               permission_document: bytes | None,
                               permission_checked_at: datetime | None,
                               dry_run: bool = True) -> MetadataPublishResult:
        """One explicit Candidate only; never scans or claims the Full Raw queue.

        The caller supplies fresh authorized page/permission bytes. A timeout
        is reconciled by the completion ledger; an unreadable result is UNKNOWN.
        """
        if self.metadata is None:
            raise RuntimeError("Metadata publication repository unavailable")
        if not dry_run and not (self.adapter.publish_enabled and
                                self.metadata_publish_enabled):
            raise RuntimeError("Metadata publishing is disabled")
        tables = self.adapter.tables
        if not dry_run:
            try:
                prior = tables.get_metadata_completion(candidate_id)
                service_id = self._confirmed_metadata_completion(candidate_id, prior)
            except Exception:
                return MetadataPublishResult(candidate_id, "UNKNOWN",
                                             reasons=("COMPLETION_READBACK_UNAVAILABLE",))
            if service_id is not None:
                return MetadataPublishResult(candidate_id, "ALREADY_PUBLISHED", service_id)
            if prior is not None:
                return MetadataPublishResult(candidate_id, "UNKNOWN",
                                             reasons=("COMPLETION_READBACK_MISMATCH",))
        at = datetime.now(timezone.utc)
        if dry_run:
            outcome = self.metadata.assess(
                candidate_id, page=page, page_url=page_url, fetched_at=fetched_at,
                permission_document=permission_document,
                permission_checked_at=permission_checked_at, now=at)
        else:
            outcome = self.metadata.assess_and_record(
                candidate_id, decision_id=uuid4(), page=page, page_url=page_url,
                fetched_at=fetched_at, permission_document=permission_document,
                permission_checked_at=permission_checked_at, now=at)
        source, approval = self.metadata.publication_context(candidate_id)
        if (approval is None or approval.usage_rights is None or
                not approval.usage_rights.metadata_permitted() or
                not usage_references_replayed(approval, permission_document or b"")):
            return MetadataPublishResult(candidate_id, "HELD",
                                         reasons=("METADATA_USAGE_RIGHTS_UNCONFIRMED",))
        plan = self.adapter.dry_run_metadata(outcome, source=source,
                                             approval=approval,
                                             at=datetime.now(timezone.utc))
        if not plan.eligible or plan.record is None or plan.evidence is None:
            return MetadataPublishResult(candidate_id, "HELD", reasons=plan.reasons)
        if dry_run:
            return MetadataPublishResult(candidate_id, "DRY_RUN")
        try:
            service_id, disposition = tables.publish_metadata_atomic(
                candidate_id, plan.record, plan.evidence)
        except Exception:
            # The RPC may have committed before its response was lost. A
            # missing/failed readback does not turn uncertainty into success.
            try:
                prior = tables.get_metadata_completion(candidate_id)
                service_id = self._confirmed_metadata_completion(candidate_id, prior)
                if service_id is not None:
                    return MetadataPublishResult(candidate_id, "ALREADY_PUBLISHED",
                                                 service_id)
            except Exception:
                pass
            return MetadataPublishResult(candidate_id, "UNKNOWN",
                                         reasons=("RPC_RESULT_UNCONFIRMED",))
        try:
            prior = tables.get_metadata_completion(candidate_id)
            confirmed = self._confirmed_metadata_completion(
                candidate_id, prior, expected_service_id=service_id)
        except Exception:
            return MetadataPublishResult(candidate_id, "UNKNOWN",
                                         reasons=("COMPLETION_READBACK_UNAVAILABLE",))
        if confirmed is None:
            return MetadataPublishResult(candidate_id, "UNKNOWN",
                                         reasons=("COMPLETION_READBACK_MISMATCH",))
        return MetadataPublishResult(
            candidate_id, "ALREADY_PUBLISHED" if disposition == "already_published"
            else "PUBLISHED", service_id)

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
        metadata_due = metadata_attempted = 0
        # Preserve Full Raw priority and share its per-run ceiling. A failure in
        # that path must not be followed by additional Metadata publication.
        remaining = self.max_jobs - len(due)
        if (remaining > 0 and not failed and self.adapter.publish_enabled and
                self.metadata_publish_enabled and self.metadata is not None and
                self.metadata_inputs is not None):
            try:
                ids = self.metadata.pending_publication_ids(remaining)
                if len(ids) > remaining or len(set(ids)) != len(ids):
                    raise ValueError("metadata publication selection exceeds scope")
                metadata_due = len(ids)
                for candidate_id in ids:
                    if dry_run:
                        previews.append({"candidate_id": str(candidate_id),
                                         "kind": "metadata", "eligible": False,
                                         "reasons": ["FRESH_EVIDENCE_RECHECK_REQUIRED"]})
                        continue
                    metadata_attempted += 1
                    result = self.run_metadata_candidate(
                        candidate_id, **self.metadata_inputs(candidate_id), dry_run=False)
                    previews.append({"candidate_id": str(candidate_id), "kind": "metadata",
                                     "status": result.status, "reasons": list(result.reasons)})
                    if result.status == "PUBLISHED":
                        published += 1
                    elif result.status == "ALREADY_PUBLISHED":
                        duplicate += 1
                    elif result.status == "HELD":
                        review += 1
                    else:
                        # UNKNOWN is not failure-to-commit. Stop this run without
                        # retry; a later tick rechecks the immutable completion.
                        failed += 1
                        break
            except Exception as error:
                failed += 1
                previews.append({"kind": "metadata", "error": type(error).__name__,
                                 "recovery": "COMPLETION_RECHECK"})
        summary = PublishSummary(self.worker_id, started, datetime.now(timezone.utc), dry_run,
                                 len(due) + metadata_due, claimed, published, duplicate, review,
                                 failed, tuple(previews), metadata_due, metadata_attempted)
        if not dry_run:
            self.jobs.record_summary(summary)
        return summary
