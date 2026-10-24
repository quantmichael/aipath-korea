"""The only Service table I/O implementation used by Agent publication."""

from typing import Any
from uuid import UUID


class SupabaseServiceTables:
    def __init__(self, client: Any, *, max_scan: int = 500) -> None:
        if max_scan <= 0:
            raise ValueError("max_scan must be positive")
        self.client = client
        self.max_scan = max_scan
        self.read_count = 0
        self.write_count = 0

    def _bounded(self, table: str, columns: str) -> list[dict[str, Any]]:
        self.read_count += 1
        response = (self.client.table(table).select(columns, count="exact")
                    .limit(self.max_scan + 1).execute())
        rows = response.data or []
        if (response.count is None or response.count != len(rows) or
                response.count > self.max_scan):
            raise RuntimeError(f"{table} scan incomplete")
        return rows

    def list_sources(self) -> list[dict[str, Any]]:
        return self._bounded(
            "sources", "id,name,homepage_url,source_type,collection_method,is_active")

    def list_categories(self) -> list[dict[str, Any]]:
        return self._bounded("categories", "id,slug,is_active")

    def get_conference_category(self) -> dict[str, Any] | None:
        self.read_count += 1
        rows = (self.client.table("categories").select("id,slug,is_active")
                .eq("slug", "conference").limit(2).execute().data or [])
        if len(rows) != 1 or not rows[0].get("is_active"):
            return None
        return rows[0]

    def list_tags(self, slugs: tuple[str, ...]) -> list[dict[str, Any]]:
        if not slugs:
            return []
        self.read_count += 1
        return (self.client.table("tags").select("id,slug")
                .in_("slug", list(slugs)).execute().data or [])

    def list_opportunities(self) -> list[dict[str, Any]]:
        return self._bounded("opportunities",
                             "id,source_id,external_id,title,organizer,official_url,event_start_at,slug,status")

    def register_opportunity(self, payload: dict[str, Any],
                             evidence: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """The RPC commits the public row only with validated time evidence."""
        self.write_count += 1
        rows = self.client.rpc("register_opportunity_with_evidence", {
            "p_record": payload, "p_evidence": evidence,
            "p_candidate_id": None, "p_publish": True,
        }).execute().data or []
        if len(rows) != 1 or rows[0].get("disposition") not in {
            "registered", "protected_existing"
        } or not rows[0].get("opportunity_id"):
            raise RuntimeError("atomic Opportunity registration response invalid")
        record = self.get_opportunity(UUID(rows[0]["opportunity_id"]))
        if record is None:
            raise RuntimeError("atomic Opportunity registration readback missing")
        return record, rows[0]["disposition"]

    def get_metadata_completion(self, candidate_id: UUID) -> dict[str, Any] | None:
        self.read_count += 1
        rows = (self.client.table("agent_metadata_publish_completions")
                .select("candidate_id,opportunity_id,approval_id,verification_at,published_at,disposition")
                .eq("candidate_id", str(candidate_id)).limit(1).execute().data or [])
        return rows[0] if rows else None

    def publish_metadata_atomic(self, candidate_id: UUID, payload: dict[str, Any],
                                evidence: dict[str, Any]) -> tuple[UUID, str]:
        """Migration 018 owns registration, time evidence and completion atomically."""
        self.write_count += 1
        rows = self.client.rpc("agent_publish_metadata_candidate", {
            "p_candidate_id": str(candidate_id),
            "p_record": payload, "p_evidence": evidence,
        }).execute().data or []
        if len(rows) != 1 or rows[0].get("disposition") not in {
            "registered", "already_published"
        } or not rows[0].get("opportunity_id"):
            raise RuntimeError("atomic metadata publication response invalid")
        return UUID(rows[0]["opportunity_id"]), rows[0]["disposition"]

    def insert_opportunity(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.write_count += 1
        rows = self.client.table("opportunities").insert(payload).execute().data or []
        if len(rows) != 1:
            raise RuntimeError("opportunity insert response not singular")
        return rows[0]

    def register_time_evidence(self, record_id: UUID, evidence: dict[str, Any]) -> None:
        self.write_count += 1
        self.client.rpc("record_opportunity_status_evidence", {
            "p_opportunity_id": str(record_id), "p_evidence": evidence,
        }).execute()

    def get_opportunity(self, record_id: UUID) -> dict[str, Any] | None:
        self.read_count += 1
        rows = (self.client.table("opportunities").select("*")
                .eq("id", str(record_id)).limit(1).execute().data or [])
        return rows[0] if rows else None

    def public_archive_contains(self, record_id: UUID) -> bool:
        self.read_count += 1
        rows = self.client.rpc("opportunity_public_archive_ids", {
            "p_ids": [str(record_id)]
        }).execute().data or []
        return any(row.get("opportunity_id") == str(record_id) for row in rows)
