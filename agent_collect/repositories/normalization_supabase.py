"""Agent-only Supabase normalization writer; Service tables are never touched."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from agent_collect.repositories.extraction_supabase import SupabaseExtractionRepository
from agent_collect.repositories.normalization import NormalizationState


class SupabaseNormalizationRepository:
    def __init__(self, client: Any) -> None:
        self.client = client

    def get(self, candidate_id: UUID) -> NormalizationState | None:
        rows = (self.client.table("agent_candidates").select("*")
                .eq("id", str(candidate_id)).limit(1).execute().data or [])
        if not rows:
            return None
        row = rows[0]
        return NormalizationState(SupabaseExtractionRepository._row(row),
                                  row.get("normalized_payload") or {}, row.get("updated_at"))

    def save(self, candidate_id: UUID, *, extracted_payload: dict[str, Any],
             normalized_payload: dict[str, Any], audit: dict[str, Any]) -> NormalizationState:
        current = self.get(candidate_id)
        if current is None:
            raise KeyError("candidate not found")
        if current.extraction.payload != extracted_payload:
            raise ValueError("extracted_payload changed during normalization")
        if current.normalized_payload:
            if (current.normalized_payload == normalized_payload and
                    (current.extraction.provenance.get("normalization") or {}).get("input_sha256")
                    == audit["input_sha256"]):
                return current
            raise ValueError("existing normalization differs")
        if current.updated_at is None:
            raise RuntimeError("candidate version missing; normalization write refused")
        provenance = {**current.extraction.provenance, "normalization": audit}
        updated_at = datetime.now(timezone.utc).isoformat()
        rows = (self.client.table("agent_candidates")
                .update({"normalized_payload": normalized_payload, "provenance": provenance,
                         "updated_at": updated_at})
                .eq("id", str(candidate_id)).eq("item_fingerprint", current.extraction.fingerprint)
                .eq("updated_at", current.updated_at)
                .select("id").execute().data or [])
        if len(rows) != 1:
            raise RuntimeError("normalization write not confirmed")
        updated = self.get(candidate_id)
        if updated is None or updated.normalized_payload != normalized_payload or (
            updated.extraction.provenance.get("normalization") != audit
        ):
            raise RuntimeError("normalization readback mismatch")
        return updated
