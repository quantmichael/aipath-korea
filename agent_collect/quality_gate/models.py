"""Quality-gate domain values; no database row or service schema coupling."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from agent_collect.extraction.schema import StoredExtraction


class DuplicateStatus(StrEnum):
    UNIQUE = "UNIQUE"
    DUPLICATE = "DUPLICATE"
    POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class GateStatus(StrEnum):
    PUBLISH_READY = "PUBLISH_READY"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REJECTED = "REJECTED"


class SourceTrust(StrEnum):
    OFFICIAL = "OFFICIAL"
    TRUSTED = "TRUSTED"
    UNKNOWN = "UNKNOWN"
    LOW_TRUST = "LOW_TRUST"


class ReviewReason(StrEnum):
    MISSING_PROVENANCE = "MISSING_PROVENANCE"
    MISSING_RAW_DOCUMENT = "MISSING_RAW_DOCUMENT"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    UNVERIFIED_DATE = "UNVERIFIED_DATE"
    UNVERIFIED_URL = "UNVERIFIED_URL"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    INVALID_EVIDENCE = "INVALID_EVIDENCE"
    INVALID_DATE = "INVALID_DATE"
    INVALID_URL = "INVALID_URL"
    POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"
    DUPLICATE = "DUPLICATE"
    LOW_SOURCE_TRUST = "LOW_SOURCE_TRUST"
    UNKNOWN_SOURCE_TRUST = "UNKNOWN_SOURCE_TRUST"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    NOT_CONFERENCE = "NOT_CONFERENCE"
    INVALID_SOURCE = "INVALID_SOURCE"
    SERVICE_LOOKUP_FAILED = "SERVICE_LOOKUP_FAILED"
    AGENT_LOOKUP_FAILED = "AGENT_LOOKUP_FAILED"
    UNNORMALIZED_EVENT_TIME = "UNNORMALIZED_EVENT_TIME"


@dataclass(frozen=True, slots=True)
class QualityCandidate:
    id: UUID
    source_id: UUID
    run_id: UUID
    raw_document_id: UUID
    source_url: str
    fields: dict[str, dict[str, Any]]
    is_conference: bool
    confidence: float
    extraction_status: str
    provenance: dict[str, Any] = field(default_factory=dict)
    event_start_at: datetime | None = None
    event_end_at: datetime | None = None

    @classmethod
    def from_stored(cls, row: StoredExtraction) -> "QualityCandidate":
        payload = row.payload
        if not isinstance(payload, dict) or not isinstance(payload.get("fields"), dict):
            raise ValueError("candidate lacks structured extraction fields")
        return cls(
            id=row.id, source_id=row.source_id, run_id=row.run_id,
            raw_document_id=row.raw_document_id, source_url=row.source_url,
            fields=payload["fields"], is_conference=payload.get("is_conference") is True,
            confidence=float(payload.get("document_confidence") or 0),
            extraction_status=row.status.value, provenance=row.provenance,
        )

    def value(self, name: str) -> str | None:
        item = self.fields.get(name) or {}
        value = item.get("value") if item.get("validation_status") == "VALID" else None
        return value.strip() if isinstance(value, str) and value.strip() else None

    def field(self, name: str) -> dict[str, Any]:
        return self.fields.get(name) or {}


@dataclass(frozen=True, slots=True)
class DuplicateRecord:
    id: str
    origin: str
    title: str | None
    organizer: str | None
    official_url: str | None
    registration_url: str | None
    canonical_url: str | None
    event_start: str | None
    raw_document_id: str | None = None


@dataclass(frozen=True, slots=True)
class QualityDuplicateResult:
    candidate_id: UUID
    status: DuplicateStatus
    match_type: str
    similarity: float
    reasons: tuple[str, ...]
    checked_at: datetime
    matched_candidate_id: str | None = None
    matched_service_record_id: str | None = None


@dataclass(frozen=True, slots=True)
class QualityVerification:
    candidate_id: UUID
    status: str
    confidence: float
    reasons: tuple[str, ...]
    checks: dict[str, Any]
    evidence_refs: tuple[str, ...]
    verified_at: datetime


@dataclass(frozen=True, slots=True)
class GateDecision:
    candidate_id: UUID
    status: GateStatus
    reasons: tuple[str, ...]
