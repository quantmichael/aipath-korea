"""Manual quality application service; no LLM, collection or Service writes."""

import hashlib
import json

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable
from uuid import UUID

from agent_collect.adapters.service_db.duplicate_lookup import ServiceDuplicateLookup
from agent_collect.config.settings import AgentSettings
from agent_collect.dedup.engine import DeduplicationEngine
from agent_collect.extraction.schema import ExtractionStatus, StoredExtraction
from agent_collect.quality_gate.gate import PublishGate
from agent_collect.quality_gate.models import (
    DuplicateStatus, GateDecision, GateStatus, QualityCandidate, QualityDuplicateResult,
    QualityVerification, ReviewReason,
)
from agent_collect.repositories.contracts import RawDocumentRepository, SourceRepository
from agent_collect.repositories.quality import QualityRepository
from agent_collect.schemas.models import DuplicateMethod, DuplicateResult, PublishCandidate, Vertical
from agent_collect.schemas.status import VerificationStatus
from agent_collect.verification.engine import VerificationEngine


def _json_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class QualityGateOutcome:
    candidate_id: UUID
    duplicate: QualityDuplicateResult
    verification: QualityVerification
    decision: GateDecision
    verification_id: UUID
    queued: bool


class QualityGateRunner:
    def __init__(self, *, sources: SourceRepository, documents: RawDocumentRepository,
                 quality: QualityRepository, service_duplicates: ServiceDuplicateLookup,
                 settings: AgentSettings,
                 evidence_assessor: Callable[[StoredExtraction, QualityVerification], Any] | None = None,
                 require_official_evidence: bool = False) -> None:
        self.sources = sources
        self.processing_version = settings.processing_version
        self.documents = documents
        self.quality = quality
        self.service_duplicates = service_duplicates
        self.dedup = DeduplicationEngine(similarity_threshold=settings.dedup_similarity_threshold)
        self.verifier = VerificationEngine(threshold=settings.verification_threshold)
        self.gate = PublishGate(threshold=settings.publish_threshold)
        self.evidence_assessor = evidence_assessor
        self.require_official_evidence = require_official_evidence

    def run(self, candidate_id: UUID) -> QualityGateOutcome:
        stored = self.quality.get_candidate(candidate_id)
        if stored is None:
            raise KeyError(f"unknown candidate: {candidate_id}")
        candidate = QualityCandidate.from_stored(stored)
        start_at, end_at = self.quality.get_normalized_event_times(candidate_id)
        candidate = replace(candidate, event_start_at=start_at, event_end_at=end_at)
        source = self.sources.get(candidate.source_id)
        raw = self.documents.get(candidate.raw_document_id)
        lookup_failures: list[str] = []
        records = []
        try:
            records.extend(self.quality.list_candidate_records())
        except Exception:
            lookup_failures.append(ReviewReason.AGENT_LOOKUP_FAILED.value)
        try:
            records.extend(self.service_duplicates.list_records())
        except Exception:
            lookup_failures.append(ReviewReason.SERVICE_LOOKUP_FAILED.value)
        if lookup_failures:
            duplicate = QualityDuplicateResult(
                candidate.id, DuplicateStatus.REVIEW_REQUIRED, "lookup_incomplete", 0,
                tuple(lookup_failures), datetime.now(timezone.utc),
            )
        else:
            duplicate = self.dedup.check(candidate, records)
        verification = self.verifier.verify(candidate, source=source, raw_document=raw,
                                             duplicate=duplicate)
        normalization = self.quality.get_normalization_diagnostics(candidate_id)
        verification = replace(verification, checks={
            **verification.checks, "normalization": normalization,
        })
        safety_reasons = []
        if stored.status is ExtractionStatus.REVIEW_REQUIRED:
            safety_reasons.append("EXTRACTION_REVIEW_REQUIRED")
        if normalization.get("status") != "NORMALIZED":
            safety_reasons.append("NORMALIZATION_REVIEW_REQUIRED")
        start, deadline = self.quality.get_normalized_registration_times(candidate_id)
        if (normalization.get("application_time_kind") != "DATETIME_CONFIRMED" or
                start is None or start.tzinfo is None or
                deadline is None or deadline.tzinfo is None):
            safety_reasons.append("RECRUITMENT_TIME_UNCONFIRMED")
        elif deadline <= datetime.now(timezone.utc):
            safety_reasons.append("RECRUITMENT_EXPIRED")
        if safety_reasons and verification.status == VerificationStatus.VERIFIED.value:
            verification = replace(
                verification, status=VerificationStatus.REVIEW_REQUIRED.value,
                reasons=tuple(dict.fromkeys((*verification.reasons, *safety_reasons))),
                checks={**verification.checks, "evidence_safety_reasons": safety_reasons},
            )
        if lookup_failures:
            verification = QualityVerification(
                verification.candidate_id, VerificationStatus.REVIEW_REQUIRED.value,
                verification.confidence,
                tuple(dict.fromkeys((*verification.reasons, *lookup_failures))),
                {**verification.checks, "lookup_failures": lookup_failures},
                verification.evidence_refs, verification.verified_at,
            )
        if self.require_official_evidence and verification.status == VerificationStatus.VERIFIED.value:
            evidence_state = "UNAVAILABLE"
            evidence_reasons: tuple[str, ...] = ()
            try:
                if self.evidence_assessor is not None:
                    evidence = self.evidence_assessor(stored, verification)
                    evidence_state = evidence.state.value
                    evidence_reasons = evidence.reasons
            except Exception:
                evidence_state = "ERROR"
            if evidence_state != "VERIFIED":
                reason = "OFFICIAL_EVIDENCE_" + evidence_state
                verification = replace(
                    verification, status=VerificationStatus.REVIEW_REQUIRED.value,
                    reasons=tuple(dict.fromkeys((*verification.reasons, reason))),
                    checks={**verification.checks, "official_evidence": {
                        "state": evidence_state, "reasons": list(evidence_reasons),
                    }},
                )
            else:
                verification = replace(verification, checks={
                    **verification.checks, "official_evidence": {"state": "VERIFIED"},
                })
        decision = self.gate.decide(candidate, duplicate, verification)
        summary = _json_value(asdict(duplicate))
        if stored.provenance.get("cache_reused"):
            # Stable input/result identity survives a crash between Gate save and
            # the processing checkpoint. Policy or judgment changes create history.
            context = {
                "candidate_id": str(candidate.id), "fingerprint": stored.fingerprint,
                "payload": stored.payload, "processing_version": self.processing_version,
                "verification_threshold": self.verifier.threshold,
                "publish_threshold": self.gate.threshold,
                "dedup_threshold": self.dedup.similarity_threshold,
                "verification": {"status": verification.status, "confidence": verification.confidence,
                                 "checks": verification.checks, "reasons": verification.reasons,
                                 "evidence_refs": verification.evidence_refs},
                "duplicate": {k: v for k, v in summary.items() if k != "checked_at"},
                "gate": _json_value(asdict(decision)),
            }
            key = hashlib.sha256(json.dumps(_json_value(context), sort_keys=True).encode()).hexdigest()
            verification = replace(verification, checks={**verification.checks, "cache_reuse_key": key})
        saved = self.quality.save_verification(
            verification, summary, _json_value(asdict(decision)))
        verification = saved.value
        queued = False
        if decision.status is GateStatus.PUBLISH_READY:
            assert source is not None
            event_time_kind, timezone_basis = self.quality.get_temporal_metadata(candidate.id)
            application_start_at, application_deadline_at = (
                self.quality.get_normalized_registration_times(candidate.id))
            application_time_kind, application_timezone_basis = (
                self.quality.get_application_temporal_metadata(candidate.id))
            publication = PublishCandidate(
                contract_version=1, agent_item_id=candidate.id,
                agent_source_id=candidate.source_id, run_id=candidate.run_id,
                vertical=Vertical.CONFERENCE, title=candidate.value("title"),
                summary=candidate.value("summary"), organizer=candidate.value("organizer"),
                official_url=candidate.value("official_url"), source_name=source.name,
                verified_at=verification.verified_at,
                verification_status=VerificationStatus.VERIFIED,
                confidence=verification.confidence,
                duplicate_result=DuplicateResult(candidate.id, False, DuplicateMethod.NONE, 1),
                evidence_refs=verification.evidence_refs,
                event_start_at=candidate.event_start_at,
                event_end_at=candidate.event_end_at,
                application_start_at=application_start_at,
                application_deadline_at=application_deadline_at,
                source_homepage_url=source.url,
                collected_at=raw.collected_at if raw else None,
                event_time_kind=event_time_kind,
                timezone_basis=timezone_basis,
                application_time_kind=application_time_kind,
                application_timezone_basis=application_timezone_basis,
            )
            queued = self.quality.enqueue(
                candidate_id=candidate.id, verification_id=saved.id,
                publish_candidate=_json_value(asdict(publication)),
            )
        return QualityGateOutcome(candidate.id, duplicate, verification, decision,
                                  saved.id, queued)
