"""Agent-only migration 007 RPC port for publish claims and run leases."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from agent_collect.publishing.worker import PublishClaim, PublishJob, PublishSummary


class SupabasePublishJobs:
    def __init__(self, client: Any) -> None:
        self.client = client

    def _due(self, limit: int) -> list[PublishJob]:
        rows = self.client.rpc("agent_list_due_publish", {"p_limit": limit}).execute().data or []
        return [PublishJob(UUID(row["id"]), UUID(row["candidate_id"])) for row in rows]

    def preview(self, limit: int) -> list[PublishJob]:
        return self._due(limit)

    def list_due(self, limit: int) -> list[PublishJob]:
        return self._due(limit)

    def claim(self, job: PublishJob, *, worker_id: str, claim_id: UUID,
              lease_seconds: int) -> PublishClaim | None:
        rows = self.client.rpc("agent_claim_publish", {
            "p_queue_id": str(job.id), "p_worker_id": worker_id,
            "p_claim_id": str(claim_id), "p_lease_seconds": lease_seconds,
        }).execute().data or []
        if not rows:
            return None
        row = rows[0]
        if UUID(row["job_id"]) != job.id or UUID(row["candidate_id"]) != job.candidate_id:
            raise RuntimeError("publish claim identity mismatch")
        return PublishClaim(job.id, job.candidate_id, UUID(row["claim_id"]),
                            row["worker_id"],
                            datetime.fromisoformat(row["claim_expires_at"].replace("Z", "+00:00")))

    def complete(self, claim: PublishClaim, *, outcome: str,
                 service_id: UUID | None = None, slug: str | None = None,
                 service_status: str | None = None, published_at: datetime | None = None,
                 error_code: str | None = None) -> str:
        result = self.client.rpc("agent_finish_publish", {
            "p_queue_id": str(claim.job_id), "p_claim_id": str(claim.claim_id),
            "p_outcome": outcome, "p_service_id": str(service_id) if service_id else None,
            "p_slug": slug, "p_service_status": service_status,
            "p_published_at": published_at.isoformat() if published_at else None,
            "p_error_code": error_code,
        }).execute().data
        if result not in {"PUBLISHED", "HELD", "FAILED"}:
            raise RuntimeError("publish completion not confirmed")
        return result

    def record_summary(self, summary: PublishSummary) -> None:
        self.client.table("agent_logs").insert({
            "level": "WARNING" if summary.failed_count else "INFO",
            "event_type": "PUBLISH_WORKER_RUN", "message": "one-shot publish worker",
            "details": {"worker_id": summary.worker_id,
                        "started_at": summary.started_at.isoformat(),
                        "finished_at": summary.finished_at.isoformat(),
                        "due_count": summary.due_count,
                        "claimed_count": summary.claimed_count,
                        "published_count": summary.published_count,
                        "duplicate_count": summary.duplicate_count,
                        "review_count": summary.review_count,
                        "failed_count": summary.failed_count},
        }).execute()


class SupabaseAutomationLease:
    def __init__(self, client: Any) -> None:
        self.client = client

    def acquire(self, *, worker_id: str, claim_id: UUID, lease_seconds: int) -> bool:
        return self.client.rpc("agent_acquire_automation_lease", {
            "p_worker_id": worker_id, "p_claim_id": str(claim_id),
            "p_lease_seconds": lease_seconds,
        }).execute().data is True

    def release(self, claim_id: UUID) -> bool:
        return self.client.rpc("agent_release_automation_lease", {
            "p_claim_id": str(claim_id),
        }).execute().data is True

    def record_summary(self, details: dict[str, Any]) -> None:
        self.client.table("agent_logs").insert({
            "level": "WARNING" if details.get("errors") else "INFO",
            "event_type": "AUTOMATION_RUN", "message": "one-shot automation runtime",
            "details": details,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
