"""Supabase port for migration 006's Agent-only atomic processing RPCs."""

from typing import Any
from uuid import UUID

from agent_collect.processing.models import (
    ProcessingClaim, ProcessingJob, ProcessingPolicy, ProcessingStatus, ProcessingSummary,
)
from agent_collect.repositories.supabase import _datetime


class SupabaseProcessingRepository:
    def __init__(self, client: Any) -> None:
        self.client = client

    def seed(self, version: str, ref_prefix: str, limit: int) -> int:
        return int(self.client.rpc("agent_seed_processing_jobs", {
            "p_version": version, "p_ref_prefix": ref_prefix, "p_limit": limit,
        }).execute().data)

    def preview(self, version: str, ref_prefix: str, limit: int) -> list[ProcessingJob]:
        rows = self.client.rpc("agent_preview_processing", {
            "p_version": version, "p_ref_prefix": ref_prefix, "p_limit": limit,
        }).execute().data or []
        return [ProcessingJob(UUID(row["job_id"]) if row["job_id"] else None,
                              UUID(row["raw_document_id"]), version,
                              ProcessingStatus(row["status"]), 0,
                              _datetime(row["next_attempt_at"])) for row in rows]

    def list_due(self, version: str, ref_prefix: str, limit: int) -> list[ProcessingJob]:
        rows = self.client.rpc("agent_list_due_processing", {
            "p_version": version, "p_ref_prefix": ref_prefix, "p_limit": limit,
        }).execute().data or []
        return [ProcessingJob(UUID(row["id"]), UUID(row["raw_document_id"]),
                              row["processing_version"], ProcessingStatus(row["status"]),
                              row["attempt_count"], _datetime(row["next_attempt_at"]))
                for row in rows]

    def claim(self, job_id: UUID, *, worker_id: str, claim_id: UUID,
              policy: ProcessingPolicy) -> ProcessingClaim | None:
        rows = self.client.rpc("agent_claim_processing", {
            "p_job_id": str(job_id), "p_worker_id": worker_id,
            "p_claim_id": str(claim_id), "p_lease_seconds": policy.lease_seconds,
            "p_backoff_minutes": list(policy.retry_backoff_minutes),
            "p_max_attempts": policy.max_attempts,
        }).execute().data or []
        if not rows:
            return None
        row = rows[0]
        return ProcessingClaim(UUID(row["id"]), UUID(row["raw_document_id"]),
                               row["processing_version"], UUID(row["claim_id"]),
                               row["worker_id"], _datetime(row["claimed_at"]),
                               _datetime(row["claim_expires_at"]))

    def checkpoint(self, claim: ProcessingClaim, *, candidate_id: UUID,
                   verification_id: UUID | None = None,
                   gate_status: str | None = None) -> None:
        result = self.client.rpc("agent_processing_checkpoint", {
            "p_job_id": str(claim.job_id), "p_claim_id": str(claim.claim_id),
            "p_candidate_id": str(candidate_id),
            "p_verification_id": str(verification_id) if verification_id else None,
            "p_gate_status": gate_status,
        }).execute().data
        if result is not True:
            raise RuntimeError("processing checkpoint not confirmed")

    def complete(self, claim: ProcessingClaim, *, outcome: ProcessingStatus,
                 policy: ProcessingPolicy, candidate_id: UUID | None = None,
                 verification_id: UUID | None = None, gate_status: str | None = None,
                 error_code: str | None = None) -> ProcessingStatus:
        value = self.client.rpc("agent_finish_processing", {
            "p_job_id": str(claim.job_id), "p_claim_id": str(claim.claim_id),
            "p_outcome": outcome.value,
            "p_candidate_id": str(candidate_id) if candidate_id else None,
            "p_verification_id": str(verification_id) if verification_id else None,
            "p_gate_status": gate_status, "p_error_code": error_code,
            "p_backoff_minutes": list(policy.retry_backoff_minutes),
            "p_max_attempts": policy.max_attempts,
        }).execute().data
        return ProcessingStatus(value)

    def record_summary(self, summary: ProcessingSummary) -> None:
        self.client.table("agent_logs").insert({
            "level": "WARNING" if summary.failed_count else "INFO",
            "event_type": "PROCESSING_WORKER_RUN",
            "message": "one-shot raw-document processing worker",
            "details": {
                "worker_id": summary.worker_id,
                "started_at": summary.started_at.isoformat(),
                "finished_at": summary.finished_at.isoformat(),
                "dry_run": summary.dry_run,
                "due_count": summary.due_count,
                "claimed_count": summary.claimed_count,
                "processed_count": summary.processed_count,
                "publish_ready_count": summary.publish_ready_count,
                "review_count": summary.review_count,
                "rejected_count": summary.rejected_count,
                "failed_count": summary.failed_count,
                "budget_blocked_count": summary.budget_blocked_count,
            },
        }).execute()
