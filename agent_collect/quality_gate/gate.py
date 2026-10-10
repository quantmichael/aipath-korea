"""Deterministic final publish-readiness decision; never publishes Service data."""

from agent_collect.quality_gate.models import (
    DuplicateStatus, GateDecision, GateStatus, QualityCandidate,
    QualityDuplicateResult, QualityVerification, ReviewReason,
)
from agent_collect.schemas.status import VerificationStatus
from agent_collect.extraction.validation import validated_calendar_date
from agent_collect.verification.engine import REQUIRED_FIELDS


class PublishGate:
    def __init__(self, *, threshold: float) -> None:
        if not 0 <= threshold <= 1:
            raise ValueError("publish threshold must be between 0 and 1")
        self.threshold = threshold

    def decide(self, candidate: QualityCandidate, duplicate: QualityDuplicateResult,
               verification: QualityVerification) -> GateDecision:
        if verification.candidate_id != candidate.id or duplicate.candidate_id != candidate.id:
            raise ValueError("quality result candidate mismatch")
        reasons = list(verification.reasons)
        if not candidate.is_conference:
            return GateDecision(candidate.id, GateStatus.REJECTED,
                                tuple(dict.fromkeys((*reasons, ReviewReason.NOT_CONFERENCE.value))))
        if verification.status == VerificationStatus.REJECTED.value:
            return GateDecision(candidate.id, GateStatus.REJECTED, tuple(reasons))
        if duplicate.status is not DuplicateStatus.UNIQUE:
            return GateDecision(candidate.id, GateStatus.REVIEW_REQUIRED, tuple(reasons))
        if verification.status != VerificationStatus.VERIFIED.value:
            return GateDecision(candidate.id, GateStatus.REVIEW_REQUIRED, tuple(reasons))
        if any(not candidate.value(name) for name in REQUIRED_FIELDS):
            reasons.append(ReviewReason.MISSING_REQUIRED_FIELD.value)
        if not verification.evidence_refs:
            reasons.append(ReviewReason.INSUFFICIENT_EVIDENCE.value)
        if verification.confidence < self.threshold:
            reasons.append(ReviewReason.LOW_CONFIDENCE.value)
        if candidate.event_start_at is None or candidate.event_start_at.tzinfo is None:
            reasons.append(ReviewReason.UNNORMALIZED_EVENT_TIME.value)
        elif (validated_calendar_date(candidate.value("event_start") or "")
              != candidate.event_start_at.date()):
            reasons.append(ReviewReason.INVALID_DATE.value)
        if candidate.event_end_at is not None and (
            candidate.event_end_at.tzinfo is None or candidate.event_start_at is None
            or candidate.event_end_at < candidate.event_start_at
        ):
            reasons.append(ReviewReason.INVALID_DATE.value)
        if reasons:
            return GateDecision(candidate.id, GateStatus.REVIEW_REQUIRED, tuple(dict.fromkeys(reasons)))
        return GateDecision(candidate.id, GateStatus.PUBLISH_READY, ())
