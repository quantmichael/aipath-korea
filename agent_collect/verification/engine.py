"""Fail-closed checks over validated extraction fields and Agent provenance."""

from datetime import datetime, timezone

from agent_collect.collectors.security import UnsafeTargetError, validate_target_url
from agent_collect.extraction.validation import validated_calendar_date
from agent_collect.quality_gate.models import (
    DuplicateStatus, QualityCandidate, QualityDuplicateResult, QualityVerification,
    ReviewReason, SourceTrust,
)
from agent_collect.schemas.models import RawDocument, Source, SourceType
from agent_collect.schemas.status import SourceStatus, VerificationStatus


REQUIRED_FIELDS = ("title", "summary", "organizer", "official_url", "event_start")


def classify_source_trust(source: Source | None) -> SourceTrust:
    if source is None or source.source_type is SourceType.UNKNOWN:
        return SourceTrust.UNKNOWN
    if source.status is SourceStatus.REJECTED or source.source_type is SourceType.COMMUNITY:
        return SourceTrust.LOW_TRUST
    if source.source_type is SourceType.OFFICIAL:
        return SourceTrust.OFFICIAL
    if source.source_type is SourceType.AGGREGATOR and (source.confidence or 0) >= 0.8:
        return SourceTrust.TRUSTED
    return SourceTrust.UNKNOWN


class VerificationEngine:
    def __init__(self, *, threshold: float) -> None:
        if not 0 <= threshold <= 1:
            raise ValueError("verification threshold must be between 0 and 1")
        self.threshold = threshold

    def verify(self, candidate: QualityCandidate, *, source: Source | None,
               raw_document: RawDocument | None,
               duplicate: QualityDuplicateResult) -> QualityVerification:
        reasons: list[str] = []
        checks: dict[str, object] = {"required_fields": {}, "duplicate": duplicate.status.value}
        if not candidate.is_conference:
            reasons.append(ReviewReason.NOT_CONFERENCE.value)
        if source is None or source.id != candidate.source_id:
            reasons.append(ReviewReason.MISSING_PROVENANCE.value)
        elif source.status is SourceStatus.REJECTED:
            reasons.append(ReviewReason.INVALID_SOURCE.value)
        if raw_document is None or (raw_document.id != candidate.raw_document_id or
                                    raw_document.run_id != candidate.run_id or
                                    raw_document.source_id != candidate.source_id):
            reasons.append(ReviewReason.MISSING_RAW_DOCUMENT.value)
        trust = classify_source_trust(source)
        checks["source_trust"] = trust.value
        if trust is SourceTrust.LOW_TRUST:
            reasons.append(ReviewReason.LOW_SOURCE_TRUST.value)
        elif trust is SourceTrust.UNKNOWN:
            reasons.append(ReviewReason.UNKNOWN_SOURCE_TRUST.value)
        evidence_refs: list[str] = []
        for name in REQUIRED_FIELDS:
            item = candidate.field(name)
            value = candidate.value(name)
            status = str(item.get("validation_status") or "MISSING")
            checks["required_fields"][name] = {"status": status, "valid": bool(value)}
            if not value:
                reasons.append(f"{ReviewReason.MISSING_REQUIRED_FIELD.value}:{name}")
                if status not in {"MISSING", "UNVALIDATED"}:
                    reasons.append(f"{ReviewReason.INVALID_EVIDENCE.value}:{name}")
                continue
            ids = item.get("link_ids", []) if name == "official_url" else item.get("evidence_block_ids", [])
            if not ids or not item.get("evidence_text"):
                reasons.append(f"{ReviewReason.INSUFFICIENT_EVIDENCE.value}:{name}")
            else:
                evidence_refs.extend(f"{candidate.raw_document_id}:{ref}" for ref in ids)
            if name == "official_url":
                try:
                    validate_target_url(value)
                except UnsafeTargetError:
                    reasons.append(ReviewReason.INVALID_URL.value)
            if name == "event_start":
                if validated_calendar_date(value) is None:
                    reasons.append(ReviewReason.INVALID_DATE.value)
        start, end = candidate.value("event_start"), candidate.value("event_end")
        if start and end:
            start_date, end_date = validated_calendar_date(start), validated_calendar_date(end)
            if start_date is None or end_date is None or end_date < start_date:
                reasons.append(ReviewReason.INVALID_DATE.value)
        if duplicate.status is DuplicateStatus.DUPLICATE:
            reasons.append(ReviewReason.DUPLICATE.value)
        elif duplicate.status is DuplicateStatus.POSSIBLE_DUPLICATE:
            reasons.append(ReviewReason.POSSIBLE_DUPLICATE.value)
        elif duplicate.status is DuplicateStatus.REVIEW_REQUIRED:
            reasons.append(ReviewReason.AGENT_LOOKUP_FAILED.value)
        if candidate.confidence < self.threshold:
            reasons.append(ReviewReason.LOW_CONFIDENCE.value)
        checks["candidate_confidence"] = candidate.confidence
        checks["evidence_count"] = len(evidence_refs)
        if ReviewReason.NOT_CONFERENCE.value in reasons or ReviewReason.INVALID_SOURCE.value in reasons:
            status = VerificationStatus.REJECTED
        elif reasons:
            status = VerificationStatus.REVIEW_REQUIRED
        else:
            status = VerificationStatus.VERIFIED
        return QualityVerification(
            candidate.id, status.value, candidate.confidence, tuple(dict.fromkeys(reasons)),
            checks, tuple(dict.fromkeys(evidence_refs)), datetime.now(timezone.utc),
        )
