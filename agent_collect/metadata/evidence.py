"""Offline metadata-first evidence path.

The caller supplies one already-fetched official page in memory. This module
does not fetch, persist raw HTML, approve a Source, enqueue, or publish.
Operational use requires an auditable metadata approval and a complete,
fresh duplicate snapshot; no runtime adapter currently supplies those.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo

from agent_collect.collectors.security import UnsafeTargetError, validate_target_url
from agent_collect.dedup.engine import DeduplicationEngine
from agent_collect.discovery.evidence import EvidenceClaim, TimePrecision, _replay_claim
from agent_collect.discovery.quality import PageType
from agent_collect.extraction.preprocess import preprocess_html
from agent_collect.extraction.validation import parse_explicit_datetime, validated_calendar_date
from agent_collect.quality_gate.gate import PublishGate
from agent_collect.quality_gate.models import (
    DuplicateRecord, DuplicateStatus, GateDecision, QualityCandidate,
    QualityDuplicateResult, QualityVerification,
)
from agent_collect.registry.service import canonicalize_url
from agent_collect.schemas.models import Source, SourceType
from agent_collect.schemas.status import SourceStatus, VerificationStatus


_KST = ZoneInfo("Asia/Seoul")
_MAX_PAGE_BYTES = 1_048_576
_MAX_AGE = timedelta(minutes=15)
_REQUIRED = (
    "title", "summary", "organizer", "official_url", "application_start",
    "application_deadline", "event_start", "event_end", "application_method",
    "category",
)
_DATE_FIELDS = frozenset({
    "application_start", "application_deadline", "event_start", "event_end",
})
def _same_url(left: str, right: str) -> bool:
    try:
        validate_target_url(left)
        validate_target_url(right)
        return canonicalize_url(left) == canonicalize_url(right)
    except (UnsafeTargetError, ValueError, TypeError, UnicodeError):
        return False


class EvidenceType(StrEnum):
    PAGE_BLOCK = "PAGE_BLOCK"
    PAGE_LINK = "PAGE_LINK"


@dataclass(frozen=True, slots=True)
class MetadataAccessPolicy:
    """Human-reviewed permission for facts and transient page re-check only."""

    source_id: UUID
    official_url: str
    reviewer: str
    reviewed_at: datetime
    expires_at: datetime
    permission_url: str
    permission_digest: str
    organizer_relationship: str
    approved_organizer: str
    page_type: PageType
    officiality_confirmed: bool
    public_read: bool
    automated_access: bool
    fact_use: bool
    recheck_allowed: bool
    raw_storage_allowed: bool
    redistribution_allowed: bool
    scope_origin: str
    path_prefix: str
    approved_principal_id: UUID | None = None
    approval_event_id: UUID | None = None
    revoked_at: datetime | None = None

    def permits(self, source: Source | None, target: str, *, now: datetime
                ) -> tuple[bool, str]:
        if source is None or source.id != self.source_id:
            return False, "source_missing"
        if source.status not in {SourceStatus.REVIEW, SourceStatus.APPROVED,
                                 SourceStatus.ACTIVE}:
            return False, "source_not_reviewed"
        if source.source_type is not SourceType.OFFICIAL or not self.officiality_confirmed:
            return False, "officiality_unconfirmed"
        if self.page_type is not PageType.INDIVIDUAL_NOTICE:
            return False, "individual_notice_unconfirmed"
        if (not self.reviewer.strip() or not self.organizer_relationship.strip()
                or not self.approved_organizer.strip()):
            return False, "metadata_approval_missing"
        if (self.reviewed_at.tzinfo is None or self.expires_at.tzinfo is None or
                now.tzinfo is None or not self.reviewed_at <= now < self.expires_at):
            return False, "permission_expired"
        if self.revoked_at is not None:
            # An observed revocation is effective immediately, including
            # when client and database clocks differ slightly.
            return False, "permission_revoked"
        if len(self.permission_digest) != 64 or any(
                char not in "0123456789abcdef" for char in self.permission_digest):
            return False, "permission_evidence_invalid"
        if not all((self.public_read, self.automated_access, self.fact_use,
                    self.recheck_allowed)):
            return False, "metadata_use_not_permitted"
        try:
            for url in (target, self.official_url, self.permission_url,
                        self.scope_origin, source.canonical_url or source.url):
                validate_target_url(url)
            origin = urlsplit(self.scope_origin)
            page = urlsplit(target)
            approved = urlsplit(self.official_url)
            source_page = urlsplit(source.canonical_url or source.url)
            permission = urlsplit(self.permission_url)
            if (origin.scheme != "https" or origin.path or origin.query or
                    origin.fragment or origin.port is not None or
                    any(part.scheme != "https" or
                        (part.scheme, part.netloc.lower()) !=
                        (origin.scheme, origin.netloc.lower())
                        for part in (page, approved, source_page, permission))):
                return False, "outside_reviewed_origin"
            if (not self.path_prefix.startswith("/") or
                    not self.path_prefix.endswith("/") or
                    "%" in self.path_prefix or ".." in self.path_prefix or
                    "//" in self.path_prefix or
                    (page.path != self.path_prefix.rstrip("/") and
                     not page.path.startswith(self.path_prefix))):
                return False, "outside_reviewed_path"
            if (canonicalize_url(target) != canonicalize_url(self.official_url) or
                    canonicalize_url(target) !=
                    canonicalize_url(source.canonical_url or source.url)):
                return False, "source_url_mismatch"
        except (UnsafeTargetError, ValueError, UnicodeError):
            return False, "unsafe_target"
        return True, "reviewed_metadata_permission"


@dataclass(frozen=True, slots=True)
class MetadataFact:
    value: str
    source_url: str
    evidence_type: EvidenceType
    evidence_reference: str
    quote: str
    observed_at: datetime
    confidence: float
    access_policy_digest: str


@dataclass(frozen=True, slots=True)
class MetadataCandidate:
    id: UUID
    source_id: UUID
    official_url: str
    facts: dict[str, MetadataFact]
    discovered_at: datetime
    cohort: str


@dataclass(frozen=True, slots=True)
class DuplicateSnapshot:
    agent_records: tuple[DuplicateRecord, ...]
    service_records: tuple[DuplicateRecord, ...]
    captured_at: datetime
    agent_complete: bool
    service_complete: bool


@dataclass(frozen=True, slots=True)
class MetadataOutcome:
    candidate_id: UUID
    source_id: UUID
    cohort: str
    verification: QualityVerification
    duplicate: QualityDuplicateResult
    gate: GateDecision
    precision: TimePrecision
    values: dict[str, str]
    reasons: tuple[str, ...]
    raw_document_id: None = None
    publish_queue_written: bool = False


class MetadataEvidenceResolver:
    """Replay field references against transient page bytes and reuse Gate."""

    def __init__(self, *, verification_threshold: float = 0.8,
                 publish_threshold: float = 0.85,
                 similarity_threshold: float = 0.88) -> None:
        self.verification_threshold = verification_threshold
        self.dedup = DeduplicationEngine(similarity_threshold=similarity_threshold)
        self.gate = PublishGate(threshold=publish_threshold)

    def assess(self, candidate: MetadataCandidate, *, source: Source | None,
               policy: MetadataAccessPolicy | None, page: bytes | None,
               page_url: str | None, fetched_at: datetime | None,
               permission_document: bytes | None,
               permission_checked_at: datetime | None,
               duplicates: DuplicateSnapshot | None,
               now: datetime) -> MetadataOutcome:
        if now.tzinfo is None:
            raise ValueError("assessment time must be timezone-aware")
        reasons: list[str] = []
        values: dict[str, str] = {}
        refs: list[str] = []
        precision: dict[str, TimePrecision] = {}
        permitted = False
        if candidate.cohort not in {"synthetic", "operational"}:
            reasons.append("cohort_unknown")
        if source is None or source.id != candidate.source_id or policy is None:
            reasons.append("metadata_source_approval_missing")
        else:
            permitted, reason = policy.permits(source, candidate.official_url, now=now)
            if not permitted:
                reasons.append(reason)
            if candidate.cohort == "operational" and (
                    policy.approved_principal_id is None or
                    policy.approval_event_id is None):
                reasons.append("authenticated_metadata_approval_missing")
                permitted = False
            if (permission_document is None or permission_checked_at is None or
                    permission_checked_at.tzinfo is None or
                    not now - _MAX_AGE <= permission_checked_at <= now or
                    hashlib.sha256(permission_document).hexdigest() !=
                    policy.permission_digest):
                reasons.append("permission_evidence_changed_or_unavailable")
                permitted = False
        if not permitted or page is None or page_url is None or fetched_at is None:
            reasons.append("official_page_recheck_missing")
            prepared = None
        elif (fetched_at.tzinfo is None or not now - _MAX_AGE <= fetched_at <= now or
              len(page) == 0 or len(page) > _MAX_PAGE_BYTES or
              not _same_url(page_url, candidate.official_url)):
            reasons.append("official_page_recheck_invalid")
            prepared = None
        else:
            prepared = preprocess_html(page, source_url=page_url,
                                       max_input_chars=12000)
        for name in _REQUIRED:
            fact = candidate.facts.get(name)
            if fact is None:
                reasons.append(f"{name}:missing_evidence")
                continue
            if (policy is None or fact.access_policy_digest != policy.permission_digest or
                    fact.observed_at.tzinfo is None or
                    not now - _MAX_AGE <= fact.observed_at <= now or
                    not 0.0 <= fact.confidence <= 1.0 or
                    fact.confidence < self.verification_threshold or
                    len(fact.quote) > 500 or
                    not _same_url(fact.source_url, candidate.official_url)):
                reasons.append(f"{name}:evidence_invalid")
                continue
            expected_type = (EvidenceType.PAGE_LINK if name == "official_url"
                             else EvidenceType.PAGE_BLOCK)
            if fact.evidence_type is not expected_type or prepared is None:
                reasons.append(f"{name}:evidence_unreplayed")
                continue
            claim = EvidenceClaim(
                fact.value, fact.quote,
                (fact.evidence_reference,) if expected_type is EvidenceType.PAGE_BLOCK else (),
                (fact.evidence_reference,) if expected_type is EvidenceType.PAGE_LINK else (),
            )
            replay_name = {"application_start": "registration_start",
                           "application_deadline": "registration_deadline"}.get(name, name)
            value, time_precision, error = _replay_claim(
                replay_name, claim, prepared, page_url)
            if error or value is None:
                reasons.append(f"{name}:{error or 'not_replayed'}")
                continue
            values[name] = value
            precision[name] = time_precision
            refs.append(f"{canonicalize_url(page_url)}#{fact.evidence_reference}")

        if (policy is not None and "organizer" in values and
                " ".join(values["organizer"].casefold().split()) !=
                " ".join(policy.approved_organizer.casefold().split())):
            reasons.append("organizer_relationship_mismatch")

        start = values.get("application_start")
        deadline = values.get("application_deadline")
        start_precision = precision.get("application_start", TimePrecision.UNKNOWN)
        deadline_precision = precision.get("application_deadline", TimePrecision.UNKNOWN)
        window_precision = (start_precision if start_precision is deadline_precision
                            else TimePrecision.UNKNOWN)
        if window_precision is TimePrecision.UNKNOWN:
            reasons.append("application_time_unconfirmed")
        elif start and deadline:
            first = _time_value(start, start_precision, end_of_day=False)
            last = _time_value(deadline, deadline_precision, end_of_day=True)
            if first is None or last is None or last < first:
                reasons.append("application_window_invalid")
            elif last <= now:
                reasons.append("application_closed")
        event_start = values.get("event_start")
        event_end = values.get("event_end")
        event_start_at = (_time_value(event_start, precision.get("event_start"),
                                      end_of_day=False) if event_start else None)
        event_end_at = (_time_value(event_end, precision.get("event_end"),
                                    end_of_day=True) if event_end else None)
        if (event_start_at is None or event_end_at is None or
                event_end_at < event_start_at):
            reasons.append("event_window_invalid")

        fields = {
            name: {
                "value": value, "validation_status": "VALID",
                "evidence_text": candidate.facts[name].quote,
                "evidence_block_ids": [candidate.facts[name].evidence_reference]
                if name != "official_url" else [],
                "link_ids": [candidate.facts[name].evidence_reference]
                if name == "official_url" else [],
            }
            for name, value in values.items()
        }
        quality = QualityCandidate(
            candidate.id, candidate.source_id, None, None,
            candidate.official_url, fields, True,
            min((candidate.facts[name].confidence for name in values),
                default=0.0), "METADATA_REPLAYED",
            {"evidence_mode": "METADATA_FIRST", "cohort": candidate.cohort},
            event_start_at, event_end_at,
        )
        if (duplicates is None or not duplicates.agent_complete or
                not duplicates.service_complete or
                duplicates.captured_at.tzinfo is None or
                not now - _MAX_AGE <= duplicates.captured_at <= now):
            reasons.append("duplicate_lookup_incomplete")
            duplicate = QualityDuplicateResult(
                candidate.id, DuplicateStatus.REVIEW_REQUIRED, "lookup_incomplete",
                0.0, ("duplicate_lookup_incomplete",), now)
        else:
            duplicate = self.dedup.check(
                quality, list(duplicates.agent_records + duplicates.service_records))
            if duplicate.status is not DuplicateStatus.UNIQUE:
                reasons.append("duplicate_or_possible_duplicate")
        if quality.confidence < self.verification_threshold:
            reasons.append("low_confidence")
        if not refs:
            reasons.append("evidence_missing")
        verification = QualityVerification(
            candidate.id,
            (VerificationStatus.VERIFIED.value if not reasons
             else VerificationStatus.REVIEW_REQUIRED.value),
            quality.confidence, tuple(dict.fromkeys(reasons)),
            {"evidence_mode": "METADATA_FIRST",
             "access_policy_digest": policy.permission_digest if policy else None,
             "rechecked_at": fetched_at.isoformat() if fetched_at else None,
             "time_precision": window_precision.value,
             "duplicate_status": duplicate.status.value},
            tuple(refs), now,
        )
        gate = self.gate.decide(quality, duplicate, verification)
        return MetadataOutcome(candidate.id, candidate.source_id, candidate.cohort,
                               verification, duplicate, gate,
                               window_precision, values, verification.reasons)


def _time_value(value: str | None, precision: TimePrecision | None, *,
                end_of_day: bool) -> datetime | None:
    if not value:
        return None
    if precision is TimePrecision.EXACT_TIME:
        return parse_explicit_datetime(value)
    if precision is TimePrecision.DATE_ONLY:
        day = validated_calendar_date(value)
        return (datetime.combine(day, time.max if end_of_day else time.min, _KST)
                if day else None)
    return None


@dataclass(frozen=True, slots=True)
class MetadataMetrics:
    cohort: str
    official_sources_discovered: int
    metadata_eligible_sources: int
    metadata_candidates: int
    evidence_resolution_rate: float
    verified_opportunities: int
    publish_ready_candidates: int
    review_required: int
    duplicate_opportunities: int
    source_approval_bottlenecks: int


def metadata_metrics(outcomes: tuple[MetadataOutcome, ...], *,
                     cohort: str, official_source_ids: set[UUID],
                     eligible_source_ids: set[UUID]) -> MetadataMetrics:
    if cohort not in {"synthetic", "operational"} or any(
            row.cohort != cohort for row in outcomes):
        raise ValueError("metadata metrics cannot mix synthetic and operational cohorts")
    resolved = sum(len(row.values) == len(_REQUIRED) for row in outcomes)
    verified = sum(row.verification.status == VerificationStatus.VERIFIED.value
                   for row in outcomes)
    ready = sum(row.gate.status.value == "PUBLISH_READY" for row in outcomes)
    return MetadataMetrics(
        cohort, len(official_source_ids), len(eligible_source_ids),
        len(outcomes), resolved / len(outcomes) if outcomes else 0.0,
        verified, ready,
        sum(row.gate.status.value == "REVIEW_REQUIRED" for row in outcomes),
        sum(row.duplicate.status is DuplicateStatus.DUPLICATE for row in outcomes),
        sum(any(reason in {"metadata_source_approval_missing",
                           "source_not_reviewed", "officiality_unconfirmed",
                           "metadata_approval_missing"}
                for reason in row.reasons) for row in outcomes),
    )
