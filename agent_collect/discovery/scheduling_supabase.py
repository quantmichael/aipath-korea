"""Agent-only Discovery scheduling RPC adapter (migration 008)."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from agent_collect.discovery.scheduling import (
    DISCOVERY_VERTICALS, DiscoveryClaim, DiscoverySchedule, DiscoveryWorkerSummary,
)
from agent_collect.schemas.models import Vertical


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class SupabaseDiscoverySchedules:
    def __init__(self, client: Any) -> None:
        self.client = client

    @staticmethod
    def _schedule(row: dict[str, Any]) -> DiscoverySchedule:
        return DiscoverySchedule(
            id=UUID(row["id"]), query_text=row["query_text"],
            vertical=Vertical(row["vertical"]), language=row["language"],
            country=row["country"], interval_minutes=row["interval_minutes"],
            next_run_at=_timestamp(row["next_run_at"]), enabled=row["enabled"],
            consecutive_failures=row["consecutive_failures"],
        )

    def list_due(self, limit: int) -> list[DiscoverySchedule]:
        rows = self.client.rpc("agent_list_due_discovery", {"p_limit": limit}).execute().data or []
        return [self._schedule(row) for row in rows]

    def register_reviewed(self, *, query_text: str, language: str, country: str,
                          interval_minutes: int, reviewer: str, reason: str,
                          vertical: Vertical = Vertical.CONFERENCE) -> DiscoverySchedule:
        if vertical not in DISCOVERY_VERTICALS:
            raise ValueError("unsupported Discovery schedule vertical")
        method = ("agent_register_discovery_schedule" if vertical is Vertical.CONFERENCE
                  else "agent_register_discovery_schedule_vertical")
        row = self.client.rpc(method, {
            "p_query_text": query_text, "p_language": language, "p_country": country,
            "p_interval_minutes": interval_minutes, "p_reviewer": reviewer,
            "p_reason": reason,
            **({"p_vertical": vertical.value} if vertical is not Vertical.CONFERENCE else {}),
        }).execute().data
        if not isinstance(row, dict) or row.get("enabled") is not False:
            raise RuntimeError("reviewed Discovery registration was not confirmed inactive")
        return self._schedule(row)

    def set_enabled(self, schedule_id: UUID, *, enabled: bool,
                    reviewer: str, reason: str) -> bool:
        return self.client.rpc("agent_set_discovery_schedule_enabled", {
            "p_schedule_id": str(schedule_id), "p_enabled": enabled,
            "p_reviewer": reviewer, "p_reason": reason,
        }).execute().data is True

    def claim(self, schedule: DiscoverySchedule, *, worker_id: str,
              claim_id: UUID, lease_seconds: int) -> DiscoveryClaim | None:
        rows = self.client.rpc("agent_claim_discovery", {
            "p_schedule_id": str(schedule.id), "p_worker_id": worker_id,
            "p_claim_id": str(claim_id), "p_lease_seconds": lease_seconds,
        }).execute().data or []
        if not rows:
            return None
        row = rows[0]
        if UUID(row["id"]) != schedule.id or UUID(row["claim_id"]) != claim_id:
            raise RuntimeError("Discovery claim identity mismatch")
        return DiscoveryClaim(self._schedule(row), claim_id, UUID(row["current_window_id"]),
                              row["worker_id"], _timestamp(row["claim_expires_at"]))

    def complete(self, claim: DiscoveryClaim, *, outcome: str,
                 query_id: UUID | None, failure_backoff_minutes: tuple[int, ...],
                 budget_delay_minutes: int) -> str:
        result = self.client.rpc("agent_finish_discovery", {
            "p_schedule_id": str(claim.schedule.id), "p_claim_id": str(claim.claim_id),
            "p_window_id": str(claim.window_id), "p_outcome": outcome,
            "p_query_id": str(query_id) if query_id else None,
            "p_backoff_minutes": list(failure_backoff_minutes),
            "p_budget_delay_minutes": budget_delay_minutes,
        }).execute().data
        if result not in {"SUCCEEDED", "FAILED", "BUDGET_BLOCKED"}:
            raise RuntimeError("Discovery completion not confirmed")
        return result

    def record_summary(self, summary: DiscoveryWorkerSummary) -> None:
        self.client.table("agent_logs").insert({
            "level": "WARNING" if summary.failure_count else "INFO",
            "event_type": "DISCOVERY_WORKER_RUN",
            "message": "one-shot Discovery worker",
            "details": {
                "worker_id": summary.worker_id,
                "started_at": summary.started_at.isoformat(),
                "finished_at": summary.finished_at.isoformat(),
                "due_count": summary.due_count,
                "claimed_count": summary.claimed_count,
                "success_count": summary.success_count,
                "failure_count": summary.failure_count,
                "budget_blocked_count": summary.budget_blocked_count,
                "skipped_count": summary.skipped_count,
                "discovered_count": summary.discovered_count,
                "candidate_count": summary.candidate_count,
                "auto_approved_count": summary.auto_approved_count,
                "review_required_count": summary.review_required_count,
                "rejected_count": summary.rejected_count,
                "collection_scheduled_count": summary.collection_scheduled_count,
                "approval_failure_reasons": list(summary.approval_failure_reasons),
                "query_ids": list(summary.query_ids),
                "promoted_source_ids": list(summary.promoted_source_ids),
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
