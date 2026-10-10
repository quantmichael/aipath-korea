"""Agent-only quality persistence port and offline implementation."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from agent_collect.extraction.schema import StoredExtraction
from agent_collect.quality_gate.models import DuplicateRecord, QualityVerification


@dataclass(frozen=True, slots=True)
class StoredVerification:
    id: UUID
    value: QualityVerification
    gate_summary: dict[str, Any]


class QualityRepository(Protocol):
    def get_candidate(self, candidate_id: UUID) -> StoredExtraction | None: ...
    def get_normalized_event_times(self, candidate_id: UUID) -> tuple[datetime | None, datetime | None]: ...
    def get_temporal_metadata(self, candidate_id: UUID) -> tuple[str, str]: ...
    def get_normalized_registration_times(self, candidate_id: UUID) -> tuple[datetime | None, datetime | None]: ...
    def get_application_temporal_metadata(self, candidate_id: UUID) -> tuple[str, str]: ...
    def get_normalization_diagnostics(self, candidate_id: UUID) -> dict[str, Any]: ...
    def list_candidate_records(self) -> list[DuplicateRecord]: ...
    def save_verification(self, verification: QualityVerification,
                          duplicate_summary: dict[str, Any],
                          gate_summary: dict[str, Any]) -> StoredVerification: ...
    def enqueue(self, *, candidate_id: UUID, verification_id: UUID,
                publish_candidate: dict[str, Any]) -> bool: ...


class InMemoryQualityRepository:
    def __init__(self, rows: list[StoredExtraction] = ()) -> None:
        self.rows = {row.id: row for row in rows}
        self.normalized_event_times: dict[UUID, tuple[datetime | None, datetime | None]] = {}
        self.temporal_metadata: dict[UUID, tuple[str, str]] = {}
        self.normalized_registration_times: dict[UUID, tuple[datetime | None, datetime | None]] = {}
        self.application_temporal_metadata: dict[UUID, tuple[str, str]] = {}
        self.normalization_diagnostics: dict[UUID, dict[str, Any]] = {}
        self.verifications: list[StoredVerification] = []
        self.queue: dict[UUID, dict[str, Any]] = {}

    def get_candidate(self, candidate_id: UUID) -> StoredExtraction | None:
        return self.rows.get(candidate_id)

    def get_normalized_event_times(self, candidate_id: UUID) -> tuple[datetime | None, datetime | None]:
        return self.normalized_event_times.get(candidate_id, (None, None))

    def get_temporal_metadata(self, candidate_id: UUID) -> tuple[str, str]:
        return self.temporal_metadata.get(candidate_id, ("UNKNOWN", "UNKNOWN"))

    def get_normalized_registration_times(self, candidate_id: UUID) -> tuple[datetime | None, datetime | None]:
        return self.normalized_registration_times.get(candidate_id, (None, None))

    def get_application_temporal_metadata(self, candidate_id: UUID) -> tuple[str, str]:
        return self.application_temporal_metadata.get(candidate_id, ("UNKNOWN", "UNKNOWN"))

    def get_normalization_diagnostics(self, candidate_id: UUID) -> dict[str, Any]:
        if candidate_id in self.normalization_diagnostics:
            return self.normalization_diagnostics[candidate_id]
        kind, basis = self.get_temporal_metadata(candidate_id)
        return {"status": "NORMALIZED" if self.get_normalized_event_times(candidate_id)[0] else "MISSING",
                "event_time_kind": kind, "timezone_basis": basis,
                "application_time_kind": self.get_application_temporal_metadata(candidate_id)[0],
                "reason_codes": [] if self.get_normalized_event_times(candidate_id)[0]
                else ["NORMALIZATION_MISSING"]}

    def list_candidate_records(self) -> list[DuplicateRecord]:
        from agent_collect.dedup.engine import candidate_record
        from agent_collect.quality_gate.models import QualityCandidate
        return [candidate_record(QualityCandidate.from_stored(row)) for row in self.rows.values()]

    def save_verification(self, verification: QualityVerification,
                          duplicate_summary: dict[str, Any],
                          gate_summary: dict[str, Any]) -> StoredVerification:
        key = verification.checks.get("cache_reuse_key")
        identifier = uuid5(NAMESPACE_URL, f"aipath-cache-verification:{verification.candidate_id}:{key}") if key else uuid4()
        existing = next((row for row in self.verifications if row.id == identifier), None)
        if existing is not None:
            return existing
        stored = StoredVerification(identifier, verification, gate_summary)
        self.verifications.append(stored)
        return stored

    def enqueue(self, *, candidate_id: UUID, verification_id: UUID,
                publish_candidate: dict[str, Any]) -> bool:
        if candidate_id in self.queue:
            return False
        self.queue[candidate_id] = {"verification_id": verification_id,
                                    "publish_candidate": publish_candidate}
        return True
