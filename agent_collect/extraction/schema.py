"""Versioned extraction schema, separate from Service DB and PublishCandidate."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


EXTRACTION_VERSION = "conference-grounded-v2"
FIELD_NAMES = (
    "title", "organizer", "summary", "description", "official_url",
    "registration_url", "registration_start", "registration_deadline",
    "event_start", "event_end", "country", "city", "venue", "format",
    "language", "fee", "currency", "eligibility", "category", "tags",
)
EVIDENCE_REQUIRED = frozenset({
    "title", "organizer", "registration_deadline", "event_start", "event_end",
    "city", "venue", "fee",
})
DATE_FIELDS = frozenset({"registration_start", "registration_deadline", "event_start", "event_end"})
URL_FIELDS = frozenset({"official_url", "registration_url"})


class ExtractionStatus(StrEnum):
    EXTRACTED = "EXTRACTED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    NOT_CONFERENCE = "NOT_CONFERENCE"
    FAILED = "EXTRACTION_FAILED"
    BUDGET_BLOCKED = "BUDGET_BLOCKED"
    CACHED = "CACHED"


@dataclass(frozen=True, slots=True)
class FieldEvidence:
    value: str | None
    evidence_text: str | None
    confidence: float | None
    source_url: str
    raw_value: str | None = None
    raw_evidence_text: str | None = None
    evidence_block_ids: tuple[str, ...] = ()
    link_ids: tuple[str, ...] = ()
    validation_status: str = "UNVALIDATED"

    def __post_init__(self) -> None:
        if self.value is not None and not isinstance(self.value, str):
            raise ValueError("field value must be text or null")
        if self.evidence_text is not None and not isinstance(self.evidence_text, str):
            raise ValueError("evidence_text must be text or null")
        if self.raw_evidence_text is not None and not isinstance(self.raw_evidence_text, str):
            raise ValueError("raw_evidence_text must be text or null")
        if not all(isinstance(item, str) for item in self.evidence_block_ids + self.link_ids):
            raise ValueError("evidence IDs must be strings")
        if self.confidence is not None and (not isinstance(self.confidence, (int, float)) or
                                            isinstance(self.confidence, bool) or
                                            not 0 <= self.confidence <= 1):
            raise ValueError("field confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class ConferenceCandidate:
    fields: dict[str, FieldEvidence]
    document_confidence: float
    is_conference: bool
    review_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if set(self.fields) != set(FIELD_NAMES):
            raise ValueError("conference fields must match schema")
        if not isinstance(self.is_conference, bool):
            raise ValueError("is_conference must be boolean")
        if not isinstance(self.document_confidence, (int, float)) or isinstance(self.document_confidence, bool) or not 0 <= self.document_confidence <= 1:
            raise ValueError("document_confidence must be between 0 and 1")

    def as_payload(self) -> dict[str, Any]:
        return {
            "schema_version": EXTRACTION_VERSION,
            "is_conference": self.is_conference,
            "document_confidence": self.document_confidence,
            "fields": {name: {
                "value": field.value, "raw_value": field.raw_value,
                "evidence_text": field.evidence_text, "raw_evidence_text": field.raw_evidence_text,
                "source_url": field.source_url,
                "confidence": field.confidence,
                "evidence_block_ids": list(field.evidence_block_ids),
                "link_ids": list(field.link_ids),
                "validation_status": field.validation_status,
            } for name, field in self.fields.items()},
            "review_reasons": list(self.review_reasons),
        }


@dataclass(frozen=True, slots=True)
class ExtractionOutcome:
    raw_document_id: UUID
    source_id: UUID
    run_id: UUID
    status: ExtractionStatus
    candidate_id: UUID | None
    candidate: ConferenceCandidate | None
    provider: str
    model: str
    prompt_version: str
    extracted_at: datetime
    input_hash: str
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StoredExtraction:
    id: UUID
    raw_document_id: UUID
    run_id: UUID
    source_id: UUID
    source_url: str
    fingerprint: str
    status: ExtractionStatus
    payload: dict[str, Any]
    provenance: dict[str, Any]


def validate_provider_payload(payload: Any, *, source_url: str) -> ConferenceCandidate:
    if not isinstance(payload, dict) or set(payload) != {"is_conference", "document_confidence", "fields"}:
        raise ValueError("malformed structured output")
    raw_fields = payload["fields"]
    if not isinstance(raw_fields, dict) or set(raw_fields) != set(FIELD_NAMES):
        raise ValueError("malformed structured fields")
    fields = {}
    for name, row in raw_fields.items():
        if not isinstance(row, dict) or set(row) != {"value", "evidence_block_ids", "link_ids", "confidence"}:
            raise ValueError("malformed field evidence")
        block_ids, link_ids = row["evidence_block_ids"], row["link_ids"]
        if (not isinstance(block_ids, list) or not isinstance(link_ids, list) or
                not all(isinstance(item, str) for item in block_ids + link_ids)):
            raise ValueError("malformed evidence IDs")
        fields[name] = FieldEvidence(
            value=row["value"], raw_value=row["value"],
            evidence_text=None, evidence_block_ids=tuple(block_ids), link_ids=tuple(link_ids),
            confidence=row["confidence"],
            source_url=source_url,
        )
    return ConferenceCandidate(fields, payload["document_confidence"], payload["is_conference"])


def openai_json_schema() -> dict[str, Any]:
    field_schema = {"type": "object", "additionalProperties": False,
                    "properties": {
                        "value": {"type": ["string", "null"]},
                        "evidence_block_ids": {"type": "array", "items": {"type": "string"}},
                        "link_ids": {"type": "array", "items": {"type": "string"}},
                        "confidence": {"type": ["number", "null"]},
                    }, "required": ["value", "evidence_block_ids", "link_ids", "confidence"]}
    return {"type": "object", "additionalProperties": False,
            "properties": {
                "is_conference": {"type": "boolean"},
                "document_confidence": {"type": "number"},
                "fields": {"type": "object", "additionalProperties": False,
                           "properties": {name: field_schema for name in FIELD_NAMES},
                           "required": list(FIELD_NAMES)},
            }, "required": ["is_conference", "document_confidence", "fields"]}
