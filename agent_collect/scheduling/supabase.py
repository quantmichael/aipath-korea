"""Supabase scheduling port. All mutations use Agent-only atomic RPCs from 005."""

from typing import Any
from uuid import UUID

from agent_collect.registry.approval import source_is_collectible
from agent_collect.repositories.contracts import SourceRepository
from agent_collect.repositories.supabase import _datetime
from agent_collect.schemas.models import CollectionRun
from agent_collect.schemas.status import RunStatus
from agent_collect.scheduling.models import CollectionClaim, DueSource, WorkerSummary
from agent_collect.scheduling.policy import CollectionSchedulePolicy


class SupabaseCollectionScheduleRepository:
    def __init__(self, client: Any, sources: SourceRepository) -> None:
        self.client = client
        self.sources = sources

    def list_due(self, limit: int) -> list[DueSource]:
        if limit < 1 or limit > 100:
            raise ValueError("aware time and due limit 1..100 required")
        rows = self.client.rpc("agent_list_due_sources", {
            "p_limit": limit,
        }).execute().data or []
        due = []
        for row in rows:
            source = self.sources.get(UUID(row["source_id"]))
            # Defense in depth: the RPC also checks the 004 approval snapshot.
            if source is None or not source_is_collectible(source):
                continue
            due.append(DueSource(source, UUID(row["window_id"]),
                                 int(row["interval_minutes"]),
                                 _datetime(row["next_collection_at"])))
        return due

    def claim(self, source_id: UUID, *, worker_id: str, claim_id: UUID,
              policy: CollectionSchedulePolicy) -> CollectionClaim | None:
        rows = self.client.rpc("agent_claim_due_source", {
            "p_source_id": str(source_id), "p_worker_id": worker_id,
            "p_claim_id": str(claim_id),
            "p_lease_seconds": policy.lease_seconds,
            "p_max_domain_claims": policy.max_concurrent_per_domain,
            "p_backoff_minutes": list(policy.failure_backoff_minutes),
            "p_failure_review_threshold": policy.failure_review_threshold,
        }).execute().data or []
        if not rows:
            return None
        row = rows[0]
        return CollectionClaim(UUID(row["source_id"]), UUID(row["window_id"]),
                               UUID(row["claim_id"]), row["claimed_by"],
                               _datetime(row["claimed_at"]),
                               _datetime(row["claim_expires_at"]))

    def complete(self, claim: CollectionClaim, *, run: CollectionRun | None,
                 policy: CollectionSchedulePolicy) -> RunStatus:
        result = self.client.rpc("agent_finish_collection", {
            "p_source_id": str(claim.source_id), "p_claim_id": str(claim.claim_id),
            "p_run_id": str(run.id) if run else None,
            "p_backoff_minutes": list(policy.failure_backoff_minutes),
            "p_failure_review_threshold": policy.failure_review_threshold,
        }).execute()
        return RunStatus(result.data)

    def record_summary(self, summary: WorkerSummary) -> None:
        self.client.table("agent_logs").insert({
            "level": "INFO" if summary.failure_count == 0 else "WARNING",
            "event_type": "COLLECTION_WORKER_RUN",
            "message": "one-shot known-source collection worker",
            "details": {
                "worker_id": summary.worker_id,
                "started_at": summary.started_at.isoformat(),
                "finished_at": summary.finished_at.isoformat(),
                "due_count": summary.due_count,
                "claimed_count": summary.claimed_count,
                "success_count": summary.success_count,
                "failure_count": summary.failure_count,
                "skipped_count": summary.skipped_count,
            },
        }).execute()
