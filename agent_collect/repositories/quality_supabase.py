"""Agent-table-only Supabase repository for Quality Gate."""

from typing import Any
from datetime import datetime
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from agent_collect.dedup.engine import candidate_record
from agent_collect.extraction.schema import ExtractionStatus, StoredExtraction
from agent_collect.normalization.engine import extraction_input_sha256
from agent_collect.quality_gate.models import DuplicateRecord, QualityCandidate, QualityVerification
from agent_collect.repositories.extraction_supabase import SupabaseExtractionRepository
from agent_collect.repositories.quality import StoredVerification


class SupabaseQualityRepository:
    def __init__(self, client: Any, *, max_records: int = 500) -> None:
        if max_records <= 0:
            raise ValueError("max_records must be positive")
        self.client = client
        self.max_records = max_records

    def get_candidate(self, candidate_id: UUID) -> StoredExtraction | None:
        rows = (self.client.table("agent_candidates").select("*")
                .eq("id", str(candidate_id)).limit(1).execute().data or [])
        return SupabaseExtractionRepository._row(rows[0]) if rows else None

    def _normalized_payload(self, candidate_id: UUID) -> dict[str, Any]:
        rows = (self.client.table("agent_candidates")
                .select("normalized_payload,extracted_payload")
                .eq("id", str(candidate_id)).limit(1).execute().data or [])
        payload = (rows[0].get("normalized_payload") or {}) if rows else {}
        if not isinstance(payload, dict) or not payload:
            return {}
        extracted = rows[0].get("extracted_payload")
        if (not isinstance(extracted, dict) or
                payload.get("input_sha256") != extraction_input_sha256(extracted)):
            return {"status": "STALE", "reason_codes": ["EXTRACTION_CHANGED_SINCE_NORMALIZATION"]}
        return payload

    @staticmethod
    def _timestamp(value: Any) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return result if result.tzinfo is not None else None

    def get_normalized_event_times(self, candidate_id: UUID) -> tuple[datetime | None, datetime | None]:
        payload = self._normalized_payload(candidate_id)
        return self._timestamp(payload.get("event_start_at")), self._timestamp(payload.get("event_end_at"))

    def get_temporal_metadata(self, candidate_id: UUID) -> tuple[str, str]:
        payload = self._normalized_payload(candidate_id)
        return str(payload.get("event_time_kind") or "UNKNOWN"), str(payload.get("timezone_basis") or "UNKNOWN")

    def get_normalized_registration_times(self, candidate_id: UUID) -> tuple[datetime | None, datetime | None]:
        payload = self._normalized_payload(candidate_id)
        return self._timestamp(payload.get("application_start_at")), self._timestamp(payload.get("application_deadline_at"))

    def get_application_temporal_metadata(self, candidate_id: UUID) -> tuple[str, str]:
        payload = self._normalized_payload(candidate_id)
        return (str(payload.get("application_time_kind") or "UNKNOWN"),
                str(payload.get("application_timezone_basis") or "UNKNOWN"))

    def get_normalization_diagnostics(self, candidate_id: UUID) -> dict[str, Any]:
        payload = self._normalized_payload(candidate_id)
        return {
            "status": str(payload.get("status") or "MISSING"),
            "version": payload.get("normalization_version"),
            "event_time_kind": str(payload.get("event_time_kind") or "UNKNOWN"),
            "timezone_basis": str(payload.get("timezone_basis") or "UNKNOWN"),
            "application_time_kind": str(payload.get("application_time_kind") or "UNKNOWN"),
            "application_timezone_basis": str(payload.get("application_timezone_basis") or "UNKNOWN"),
            "reason_codes": list(payload.get("reason_codes", []) if payload
                                 else ["NORMALIZATION_MISSING"]),
        }

    def list_candidate_records(self) -> list[DuplicateRecord]:
        response = (self.client.table("agent_candidates")
                    .select("id,source_id,run_id,raw_document_id,source_url,extracted_payload,provenance",
                            count="exact")
                    .limit(self.max_records + 1).execute())
        rows = response.data or []
        if response.count is None or response.count > self.max_records or len(rows) > self.max_records:
            raise RuntimeError("agent duplicate scan incomplete")
        records = []
        for row in rows:
            payload = row.get("extracted_payload") or {}
            if not isinstance(payload.get("fields"), dict):
                continue
            stored = StoredExtraction(
                id=UUID(row["id"]), source_id=UUID(row["source_id"]),
                run_id=UUID(row["run_id"]), raw_document_id=UUID(row["raw_document_id"]),
                source_url=row.get("source_url") or "", fingerprint="",
                status=ExtractionStatus.REVIEW_REQUIRED,
                payload=payload, provenance=row.get("provenance") or {},
            )
            records.append(candidate_record(QualityCandidate.from_stored(stored)))
        return records

    def save_verification(self, verification: QualityVerification,
                          duplicate_summary: dict[str, Any],
                          gate_summary: dict[str, Any]) -> StoredVerification:
        key = verification.checks.get("cache_reuse_key")
        verification_id = uuid5(NAMESPACE_URL, f"aipath-cache-verification:{verification.candidate_id}:{key}") if key else uuid4()
        def existing_result():
            rows = (self.client.table("agent_verifications").select("*")
                    .eq("id", str(verification_id)).limit(1).execute().data or [])
            if not rows:
                return None
            row = rows[0]
            value = QualityVerification(UUID(row["candidate_id"]), row["result"], row["confidence"],
                                        tuple(row.get("reasons") or []), row["checks"],
                                        tuple(row.get("evidence_refs") or []),
                                        datetime.fromisoformat(row["verified_at"].replace("Z", "+00:00")))
            return StoredVerification(verification_id, value, row["checks"]["gate_decision"])
        if key:
            existing = existing_result()
            if existing is not None:
                return existing
        checks = {**verification.checks, "duplicate_result": duplicate_summary,
                  "gate_decision": gate_summary,
                  "policy_version": "conference-quality-v1"}
        try:
            self.client.table("agent_verifications").insert({
                "id": str(verification_id), "candidate_id": str(verification.candidate_id),
                "result": verification.status, "confidence": verification.confidence,
                "checks": checks, "evidence_refs": list(verification.evidence_refs),
                "reasons": list(verification.reasons), "verified_at": verification.verified_at.isoformat(),
            }).execute()
        except Exception:
            if key:
                existing = existing_result()
                if existing is not None:
                    return existing
            raise
        return StoredVerification(verification_id, verification, gate_summary)

    def enqueue(self, *, candidate_id: UUID, verification_id: UUID,
                publish_candidate: dict[str, Any]) -> bool:
        existing = (self.client.table("agent_publish_queue").select("id")
                    .eq("candidate_id", str(candidate_id)).limit(1).execute().data or [])
        if existing:
            return False
        try:
            self.client.table("agent_publish_queue").insert({
                "id": str(uuid4()), "candidate_id": str(candidate_id),
                "verification_id": str(verification_id), "contract_version": 1,
                "publish_candidate": publish_candidate, "status": "READY",
            }).execute()
        except Exception:
            # Concurrent insert may win the unique(candidate_id) race. Re-read
            # before treating the exception as idempotent success.
            existing = (self.client.table("agent_publish_queue").select("id")
                        .eq("candidate_id", str(candidate_id)).limit(1).execute().data or [])
            if existing:
                return False
            raise
        return True
