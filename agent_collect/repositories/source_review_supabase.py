"""Supabase review port: one PostgreSQL RPC owns transition and audit atomically."""

from datetime import datetime
from typing import Any
from uuid import UUID

from agent_collect.registry.approval import SourceDecision, SourceReviewCommand, SourceReviewEvent
from agent_collect.repositories.supabase import SupabaseSourceRepository
from agent_collect.schemas.models import Source, SourceType
from agent_collect.schemas.status import SourceStatus


class SupabaseSourceReviewRepository:
    def __init__(self, client: Any) -> None:
        self.client = client
        self.sources = SupabaseSourceRepository(client)

    def get_source(self, source_id: UUID) -> Source | None:
        return self.sources.get(source_id)

    def transition(self, command: SourceReviewCommand) -> Source:
        result = self.client.rpc("agent_review_source", {
            "p_source_id": str(command.source_id),
            "p_expected_status": command.expected_status.value,
            "p_target_status": command.target_status.value,
            "p_reviewer": command.reviewer,
            "p_reason": command.reason,
            "p_decision": command.decision.value,
            "p_decision_id": str(command.decision_id),
            "p_source_type": command.source_type.value if command.source_type else None,
            "p_officiality_confirmed": command.officiality_confirmed,
            "p_collection_allowed": command.collection_allowed,
        }).execute().data
        row = result[0] if isinstance(result, list) else result
        if not isinstance(row, dict) or row.get("id") != str(command.source_id):
            raise RuntimeError("source review RPC did not return matching Source")
        return self.sources._from_row(row)

    def list_events(self, source_id: UUID) -> list[SourceReviewEvent]:
        rows = (self.client.table("agent_source_review_events").select("*")
                .eq("source_id", str(source_id)).order("reviewed_at").execute().data or [])
        events = []
        for row in rows:
            command = SourceReviewCommand(
                source_id=UUID(row["source_id"]),
                expected_status=SourceStatus(row["from_status"]),
                target_status=SourceStatus(row["review_status"]),
                reviewer=row["reviewer"], reason=row["review_reason"],
                decision=SourceDecision(row["review_decision"]),
                reviewed_at=datetime.fromisoformat(row["reviewed_at"].replace("Z", "+00:00")),
                decision_id=UUID(row["id"]),
                source_type=(SourceType(row["source_type_after"])
                             if row["review_decision"] == SourceDecision.TYPE_CHANGED.value else None),
                officiality_confirmed=row.get("officiality_confirmed"),
                collection_allowed=row.get("collection_allowed"),
            )
            events.append(SourceReviewEvent(
                command, row["canonical_url"], row["normalized_domain"],
                SourceType(row["source_type_before"]), SourceType(row["source_type_after"]),
                row["approval_decision"], row["activation_decision"],
                row.get("officiality_confirmed"), row.get("collection_allowed"),
            ))
        return events
