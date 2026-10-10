"""Maps extraction domain records onto existing Agent tables; no Service DB access."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from agent_collect.extraction.schema import ExtractionStatus, StoredExtraction


class SupabaseExtractionRepository:
    def __init__(self, client: Any) -> None:
        self.client = client

    @staticmethod
    def _row(row: dict[str, Any]) -> StoredExtraction:
        return StoredExtraction(
            id=UUID(row["id"]), raw_document_id=UUID(row["raw_document_id"]),
            run_id=UUID(row["run_id"]), source_id=UUID(row["source_id"]),
            source_url=row["source_url"], fingerprint=row["item_fingerprint"],
            status=ExtractionStatus((row.get("provenance") or {})["extraction_status"]),
            payload=row.get("extracted_payload") or {},
            provenance=row.get("provenance") or {},
        )

    def find_by_raw_fingerprint(self, raw_document_id: UUID, fingerprint: str) -> StoredExtraction | None:
        rows = (self.client.table("agent_candidates").select("*")
                .eq("raw_document_id", str(raw_document_id))
                .eq("item_fingerprint", fingerprint).limit(1).execute().data or [])
        return self._row(rows[0]) if rows else None

    def find_by_fingerprint(self, fingerprint: str) -> StoredExtraction | None:
        rows = (self.client.table("agent_candidates").select("*")
                .eq("item_fingerprint", fingerprint).limit(1).execute().data or [])
        return self._row(rows[0]) if rows else None

    def find_by_content_hash(self, content_hash: str) -> StoredExtraction | None:
        rows = (self.client.table("agent_candidates").select("*")
                .contains("provenance", {"content_sha256": content_hash})
                .limit(1).execute().data or [])
        return self._row(rows[0]) if rows else None

    def save(self, row: StoredExtraction) -> StoredExtraction:
        if not row.provenance.get("cache_reused"):
            return self._insert(row)
        existing = self.find_by_raw_fingerprint(row.raw_document_id, row.fingerprint)
        if existing is not None:
            return existing
        try:
            return self._insert(row)
        except Exception:
            # UNIQUE(raw_document_id,item_fingerprint) may have a concurrent winner.
            existing = self.find_by_raw_fingerprint(row.raw_document_id, row.fingerprint)
            if existing is not None:
                return existing
            raise

    def _insert(self, row: StoredExtraction) -> StoredExtraction:
        db_status = {
            ExtractionStatus.EXTRACTED: "EXTRACTED",
            ExtractionStatus.REVIEW_REQUIRED: "REVIEW_REQUIRED",
            ExtractionStatus.NOT_CONFERENCE: "REJECTED",
        }[row.status]
        self.client.table("agent_candidates").insert({
            "id": str(row.id), "raw_document_id": str(row.raw_document_id),
            "run_id": str(row.run_id), "source_id": str(row.source_id),
            "source_url": row.source_url, "item_fingerprint": row.fingerprint,
            "status": db_status, "vertical": "conference" if row.status != ExtractionStatus.NOT_CONFERENCE else "unknown",
            "official_url": (row.payload.get("fields", {}).get("official_url") or {}).get("value"),
            "extracted_payload": row.payload, "provenance": row.provenance,
        }).execute()
        return row

    def count_attempts_since(self, since: datetime) -> int:
        # Exact count avoids PostgREST's default 1,000-row result-page limit.
        response = (self.client.table("agent_logs").select("id", count="exact", head=True)
                    .eq("event_type", "EXTRACTION_LLM_ATTEMPT")
                    .gte("created_at", since.isoformat()).execute())
        if response.count is None:
            raise RuntimeError("extraction usage count unavailable")
        return response.count

    def count_attempts_for_fingerprint(self, fingerprint: str) -> int:
        response = (self.client.table("agent_logs").select("id", count="exact", head=True)
                    .eq("event_type", "EXTRACTION_LLM_ATTEMPT")
                    .contains("details", {"fingerprint": fingerprint}).execute())
        if response.count is None:
            raise RuntimeError("extraction retry count unavailable")
        return response.count

    def log(self, *, event_type: str, source_id: UUID, run_id: UUID,
            details: dict[str, Any], input_tokens: int | None = None,
            output_tokens: int | None = None) -> None:
        self.client.table("agent_logs").insert({
            "id": str(uuid4()), "source_id": str(source_id), "run_id": str(run_id),
            "event_type": event_type,
            "level": "ERROR" if event_type == "EXTRACTION_FAILED" else "INFO",
            "details": details, "llm_model": details.get("model"),
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
