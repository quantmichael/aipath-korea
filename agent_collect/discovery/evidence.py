"""Offline official evidence verification over existing Agent artifacts.

No HTTP client, storage writer, approval transition, or provider is used here.
Search results are leads until already-collected original content is checked.
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlsplit
from uuid import UUID

from agent_collect.collectors.security import UnsafeTargetError, validate_target_url
from agent_collect.dedup.engine import normalize_title
from agent_collect.discovery.models import SearchResult
from agent_collect.discovery.quality import (
    Authority, OfficialPageEvidence, PageType, QualityAssessment, QualityClass,
    SearchQualityEvaluator, TemporalValidity,
)
from agent_collect.extraction.preprocess import PreparedContent, preprocess_html
from agent_collect.extraction.schema import ExtractionStatus, StoredExtraction
from agent_collect.extraction.validation import (
    _dates_in_text, parse_explicit_datetime, parse_explicit_korean_datetime,
    validated_calendar_date,
)
from agent_collect.quality_gate.models import QualityVerification
from agent_collect.registry.approval import source_is_collectible
from agent_collect.registry.service import canonicalize_url
from agent_collect.schemas.models import CollectionRun, RawDocument, Source, SourceType
from agent_collect.schemas.status import RunStatus, VerificationStatus


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EXACT_IN_QUOTE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})")
_CLOCK_IN_QUOTE = re.compile(
    r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)|"
    r"(?<!\d)\d{1,2}\s*시|\b\d{1,2}\s*(?:a\.?m\.?|p\.?m\.?)\b", re.I)
_NOTICE_WORDS = re.compile(
    r"notice|apply|application|register|recruit|call|공고|모집|신청|접수", re.I)
_SOCIAL_HOSTS = ("instagram.com", "facebook.com", "youtube.com", "youtu.be",
                 "x.com", "twitter.com", "blog.naver.com")
_REQUIRED = ("title", "organizer", "official_url", "registration_start",
             "registration_deadline", "event_start", "event_end")
_DATES = frozenset({"registration_start", "registration_deadline",
                    "event_start", "event_end"})
_URLS = frozenset({"official_url", "registration_url"})
_MAX_RAW_BYTES = 5_242_880


class EvidenceState(StrEnum):
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    VERIFIED = "VERIFIED"
    DUPLICATE = "DUPLICATE"


class TimePrecision(StrEnum):
    EXACT_TIME = "EXACT_TIME"
    DATE_ONLY = "DATE_ONLY"
    UNKNOWN = "UNKNOWN"


class OfficialKind(StrEnum):
    OFFICIAL_INDIVIDUAL = "OFFICIAL_INDIVIDUAL"
    OFFICIAL_LISTING = "OFFICIAL_LISTING"
    OFFICIAL_EVENT = "OFFICIAL_EVENT"
    OFFICIAL_OTHER = "OFFICIAL_OTHER"
    NEWS = "NEWS"
    SOCIAL = "SOCIAL"
    THIRD_PARTY = "THIRD_PARTY"
    UNKNOWN = "UNKNOWN"


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc.lower()}"


def _normal(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


@dataclass(frozen=True, slots=True)
class OfficialSourceReview:
    """Separate officiality/page-type assertion, never inferred from a domain."""

    source_id: UUID
    authority: Authority
    page_type: PageType
    reviewer: str
    reviewed_at: datetime
    relationship: str
    official_external_id: str | None = None

    def __post_init__(self) -> None:
        if not self.reviewer.strip() or not self.relationship.strip():
            raise ValueError("official review needs reviewer and relationship")
        if self.reviewed_at.tzinfo is None or self.reviewed_at.utcoffset() is None:
            raise ValueError("official review needs an aware time")
        if self.authority not in {Authority.OFFICIAL_INSTITUTION, Authority.OFFICIAL_ORGANIZER}:
            raise ValueError("official review needs official authority")


@dataclass(frozen=True, slots=True)
class EvidenceAccessSnapshot:
    """Reviewed robots, terms, and storage permission bound to Source approval."""

    source_id: UUID
    approval_reviewed_at: str
    scope_origin: str
    path_prefix: str
    checked_at: datetime
    expires_at: datetime
    robots_allowed: bool
    terms_allow_storage: bool
    explicit_collection_permission: bool
    access_restricted: bool
    terms_url: str
    terms_sha256: str

    def __post_init__(self) -> None:
        if (self.checked_at.tzinfo is None or self.expires_at.tzinfo is None or
                self.expires_at <= self.checked_at):
            raise ValueError("permission snapshot needs an active aware interval")
        if not _SHA256.fullmatch(self.terms_sha256):
            raise ValueError("terms digest must be SHA-256")
        validate_target_url(self.scope_origin)
        validate_target_url(self.terms_url)
        parts = urlsplit(self.scope_origin)
        if (parts.scheme != "https" or parts.path or parts.query or parts.fragment or
                parts.port is not None or
                (self.path_prefix != "/" and
                 (not self.path_prefix.startswith("/") or
                  not self.path_prefix.endswith("/")))):
            raise ValueError("permission scope must be a reviewed HTTPS origin and path")
        if "%" in self.path_prefix or ".." in self.path_prefix or "//" in self.path_prefix:
            raise ValueError("ambiguous permission path")
        if _origin(self.terms_url) != self.scope_origin:
            raise ValueError("terms page must be on reviewed origin")

    def allows(self, source: Source | None, url: str, *, now: datetime) -> tuple[bool, str]:
        if source is None or source.id != self.source_id or not source_is_collectible(source):
            return False, "source_approval_missing"
        approval = (source.provenance.get("source_review") or {}).get("approval") or {}
        if approval.get("reviewed_at") != self.approval_reviewed_at:
            return False, "source_approval_changed"
        auto = source.provenance.get("auto_approval") or {}
        if auto.get("eligible") is True and auto.get("terms_sha256") != self.terms_sha256:
            return False, "reviewed_terms_changed"
        if not self.checked_at <= now < self.expires_at:
            return False, "permission_snapshot_expired"
        if self.access_restricted or not self.robots_allowed:
            return False, "access_or_robots_blocked"
        if not self.terms_allow_storage or not self.explicit_collection_permission:
            return False, "storage_permission_unconfirmed"
        try:
            validate_target_url(url)
            parts = urlsplit(url)
            source_url = source.canonical_url or source.url
            if (parts.scheme != "https" or _origin(url) != self.scope_origin or
                    _origin(source_url) != self.scope_origin):
                return False, "outside_reviewed_origin"
            # URL canonicalization drops a trailing slash. The reviewed
            # directory itself remains within its reviewed child-path scope.
            if (parts.path != self.path_prefix.rstrip("/") and
                    not parts.path.startswith(self.path_prefix)):
                return False, "outside_reviewed_path"
            if ((parts.query or "%" in parts.path) and
                    canonicalize_url(url) != canonicalize_url(source_url)):
                return False, "parameterized_target_requires_review"
        except (UnsafeTargetError, ValueError, UnicodeError):
            return False, "unsafe_target"
        return True, "reviewed_robots_terms_and_permission"


@dataclass(frozen=True, slots=True)
class EvidenceClaim:
    value: str
    quote: str = ""
    block_ids: tuple[str, ...] = ()
    link_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OpportunityIdentity:
    canonical_url: str
    source_id: UUID
    official_external_id: str | None
    raw_sha256: str
    title: str | None
    recruitment_start: str | None
    recruitment_deadline: str | None


@dataclass(frozen=True, slots=True)
class EvidenceResolution:
    search_result_id: UUID
    source_id: UUID
    run_id: UUID
    raw_document_id: UUID
    search_url: str
    official_url: str
    relation: str
    content_ref: str
    content_sha256: str
    collected_at: datetime
    changed_from_previous: bool | None

    def audit_record(self) -> dict[str, object]:
        return {
            "search_result_id": str(self.search_result_id),
            "source_id": str(self.source_id), "run_id": str(self.run_id),
            "raw_document_id": str(self.raw_document_id),
            "search_url": self.search_url, "official_url": self.official_url,
            "relation": self.relation, "content_ref": self.content_ref,
            "content_sha256": self.content_sha256,
            "collected_at": self.collected_at.isoformat(),
            "changed_from_previous": self.changed_from_previous,
        }


@dataclass(frozen=True, slots=True)
class EvidenceOutcome:
    result: SearchResult
    state: EvidenceState
    kind: OfficialKind
    official_result: bool
    resolution: EvidenceResolution | None
    quality: QualityAssessment
    precision: TimePrecision
    duplicate_reason: str | None
    reasons: tuple[str, ...]
    followup_candidates: tuple[dict[str, object], ...] = ()

    @property
    def verified_opportunity(self) -> bool:
        return self.state is EvidenceState.VERIFIED and self.quality.verified_new_opportunity


@dataclass(frozen=True, slots=True)
class EvidencePerformanceMetrics:
    search_result_count: int
    official_result_count: int
    evidence_resolved_count: int
    verified_opportunity_count: int
    active_opportunity_count: int
    upcoming_opportunity_count: int
    duplicate_opportunity_count: int
    review_required_count: int
    evidence_resolution_rate: float
    verified_opportunity_yield: float


def performance_metrics(rows: tuple[EvidenceOutcome, ...]) -> EvidencePerformanceMetrics:
    size = len(rows)
    official = sum(row.official_result for row in rows)
    resolved = sum(row.resolution is not None for row in rows)
    verified = sum(row.verified_opportunity for row in rows)
    active = sum(row.verified_opportunity and row.quality.temporal is TemporalValidity.ACTIVE
                 for row in rows)
    upcoming = sum(row.verified_opportunity and row.quality.temporal is TemporalValidity.UPCOMING
                   for row in rows)
    duplicate = sum(row.state is EvidenceState.DUPLICATE for row in rows)
    review = sum(row.state is EvidenceState.REVIEW_REQUIRED for row in rows)
    return EvidencePerformanceMetrics(
        size, official, resolved, verified, active, upcoming, duplicate, review,
        resolved / official if official else 0.0, verified / size if size else 0.0)


def _kind(result: SearchResult, source: Source | None,
          review: OfficialSourceReview | None) -> tuple[OfficialKind, Authority, PageType]:
    url = result.canonical_url or result.original_url
    host = (urlsplit(url).hostname or "").lower()
    matched_source = False
    if source is not None:
        try:
            matched_source = canonicalize_url(url) in {
                canonicalize_url(source.url),
                canonicalize_url(source.canonical_url or source.url),
            }
        except (ValueError, UnicodeError):
            pass
    if (matched_source and review is not None and review.source_id == source.id and
            source.source_type is SourceType.OFFICIAL):
        mapping = {
            PageType.INDIVIDUAL_NOTICE: OfficialKind.OFFICIAL_INDIVIDUAL,
            PageType.OFFICIAL_LISTING: OfficialKind.OFFICIAL_LISTING,
            PageType.EVENT_PAGE: OfficialKind.OFFICIAL_EVENT,
        }
        return mapping.get(review.page_type, OfficialKind.OFFICIAL_OTHER), review.authority, review.page_type
    if any(host == domain or host.endswith("." + domain) for domain in _SOCIAL_HOSTS):
        return OfficialKind.SOCIAL, Authority.THIRD_PARTY, PageType.OTHER
    if any(host == domain or host.endswith("." + domain)
           for domain in SearchQualityEvaluator().news_domains):
        return OfficialKind.NEWS, Authority.THIRD_PARTY, PageType.NEWS
    return (OfficialKind.THIRD_PARTY if source is not None else OfficialKind.UNKNOWN,
            Authority.THIRD_PARTY if source is not None else Authority.UNKNOWN,
            PageType.UNKNOWN)


def claims_from_extraction(row: StoredExtraction) -> dict[str, EvidenceClaim]:
    """Reuse only existing validated Processing fields, never an LLM draft."""
    if row.status not in {ExtractionStatus.EXTRACTED, ExtractionStatus.REVIEW_REQUIRED,
                          ExtractionStatus.CACHED}:
        return {}
    fields = row.payload.get("fields") if isinstance(row.payload, dict) else None
    if not isinstance(fields, dict):
        return {}
    claims: dict[str, EvidenceClaim] = {}
    for name in (*_REQUIRED, "registration_url"):
        item = fields.get(name)
        if not isinstance(item, dict) or item.get("validation_status") != "VALID":
            continue
        value = item.get("value")
        quote = item.get("evidence_text")
        block_ids = item.get("evidence_block_ids")
        link_ids = item.get("link_ids")
        if (not isinstance(value, str) or not value.strip() or
                not isinstance(quote, str) or
                not isinstance(block_ids, (list, tuple)) or
                not all(isinstance(key, str) for key in block_ids) or
                not isinstance(link_ids, (list, tuple)) or
                not all(isinstance(key, str) for key in link_ids)):
            continue
        claims[name] = EvidenceClaim(
            value, quote, tuple(block_ids), tuple(link_ids))
    return claims


def _replay_claim(name: str, claim: EvidenceClaim, prepared: PreparedContent,
                  source_url: str) -> tuple[str | None, TimePrecision, str | None]:
    if not isinstance(claim.value, str) or not claim.value.strip():
        return None, TimePrecision.UNKNOWN, "missing_value"
    value = claim.value.strip()
    blocks = {row.block_id: row.text for row in prepared.evidence_blocks}
    links = {row.link_id: row.href for row in prepared.link_evidence}
    if any(key not in blocks for key in claim.block_ids):
        return None, TimePrecision.UNKNOWN, "invalid_block_id"
    if any(key not in links for key in claim.link_ids):
        return None, TimePrecision.UNKNOWN, "invalid_link_id"
    if name in _URLS:
        try:
            validate_target_url(value)
            canonical = canonicalize_url(value)
            cited = (links[key] for key in claim.link_ids) if claim.link_ids else links.values()
            if not any(canonicalize_url(link) == canonical for link in cited):
                return None, TimePrecision.UNKNOWN, "url_not_in_raw"
            if name == "official_url" and canonical != canonicalize_url(source_url):
                return None, TimePrecision.UNKNOWN, "official_url_mismatch"
            return canonical, TimePrecision.UNKNOWN, None
        except (UnsafeTargetError, ValueError, UnicodeError):
            return None, TimePrecision.UNKNOWN, "unsafe_url"
    if not claim.quote.strip():
        return None, TimePrecision.UNKNOWN, "missing_quote"
    available = " ".join(blocks[key] for key in claim.block_ids) if claim.block_ids else prepared.full_text
    if _normal(claim.quote) not in _normal(available):
        return None, TimePrecision.UNKNOWN, "quote_not_in_raw"
    if name in _DATES:
        exact = parse_explicit_datetime(value)
        if exact is not None:
            replayed_iso = any(parse_explicit_datetime(match.group()) == exact
                               for match in _EXACT_IN_QUOTE.finditer(claim.quote))
            replayed_korean = parse_explicit_korean_datetime(claim.quote) == exact
            if not replayed_iso and not replayed_korean:
                return None, TimePrecision.UNKNOWN, "exact_time_not_replayed"
            return exact.isoformat(), TimePrecision.EXACT_TIME, None
        day = validated_calendar_date(value)
        if day is not None and _CLOCK_IN_QUOTE.search(claim.quote):
            # A date-only value cannot discard a clock from its own citation.
            # Unknown timezone/ambiguous clocks require review, not an end-of-day fallback.
            return None, TimePrecision.UNKNOWN, "date_only_discards_time"
        if day is None or day.isoformat() not in _dates_in_text(claim.quote):
            return None, TimePrecision.UNKNOWN, "date_not_replayed"
        return day.isoformat(), TimePrecision.DATE_ONLY, None
    if _normal(value) not in _normal(claim.quote):
        return None, TimePrecision.UNKNOWN, "value_not_in_quote"
    return value, TimePrecision.UNKNOWN, None


def _duplicate(current: OpportunityIdentity,
               previous: tuple[OpportunityIdentity, ...]) -> str | None:
    for other in previous:
        if other.raw_sha256 == current.raw_sha256:
            return "same_raw_sha256"
        if (current.official_external_id and other.official_external_id and
                current.source_id == other.source_id and
                current.official_external_id == other.official_external_id):
            return "source_external_id"
        same_window = (current.recruitment_start is not None and
                       current.recruitment_deadline is not None and
                       current.recruitment_start == other.recruitment_start and
                       current.recruitment_deadline == other.recruitment_deadline)
        if same_window and current.canonical_url == other.canonical_url:
            return "canonical_url_window"
        if (same_window and current.title and other.title and
                normalize_title(current.title) == normalize_title(other.title)):
            return "title_recruitment_window"
    return None


def _listing_followups(result: SearchResult, prepared: PreparedContent,
                       access: EvidenceAccessSnapshot, source: Source,
                       now: datetime) -> tuple[dict[str, object], ...]:
    proposals = []
    seen: set[str] = set()
    current = canonicalize_url(result.canonical_url or result.original_url)
    for link in prepared.link_evidence:
        if link.link_id == "L000" or not _NOTICE_WORDS.search(link.text):
            continue
        try:
            canonical = canonicalize_url(link.href)
            validate_target_url(canonical)
        except (ValueError, UnsafeTargetError, UnicodeError):
            continue
        if canonical == current or canonical in seen or _origin(canonical) != _origin(current):
            continue
        seen.add(canonical)
        permitted, reason = access.allows(source, link.href, now=now)
        proposals.append({
            "source_search_result_id": str(result.id),
            "listing_raw_document_link_id": link.link_id,
            "candidate_url": canonical, "status": "PROPOSED",
            "access_status": "SCOPE_ALLOWED" if permitted else "REVIEW_REQUIRED",
            "access_reason": reason, "execution_enabled": False,
            "requires_individual_source_approval": True,
        })
        if len(proposals) == 20:
            break
    return tuple(proposals)


class OfficialEvidencePipeline:
    """Join one SearchResult to an existing official RawDocument without I/O."""

    def __init__(self) -> None:
        self.quality = SearchQualityEvaluator()

    def assess(self, result: SearchResult, *, now: datetime,
               source: Source | None = None, review: OfficialSourceReview | None = None,
               access: EvidenceAccessSnapshot | None = None,
               run: CollectionRun | None = None, raw: RawDocument | None = None,
               content: bytes | None = None, extraction: StoredExtraction | None = None,
               verification: QualityVerification | None = None,
               previous: tuple[OpportunityIdentity, ...] = (),
               previous_raw_sha256: str | None = None) -> EvidenceOutcome:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("assessment time must be timezone-aware")
        kind, authority, page_type = _kind(result, source, review)
        official = kind in {OfficialKind.OFFICIAL_INDIVIDUAL, OfficialKind.OFFICIAL_LISTING,
                            OfficialKind.OFFICIAL_EVENT, OfficialKind.OFFICIAL_OTHER}
        reasons: list[str] = []
        resolution: EvidenceResolution | None = None
        prepared: PreparedContent | None = None
        link_relation: str | None = None
        if official and source and access and run and raw and content is not None:
            allowed, reason = access.allows(source, raw.source_url, now=now)
            search_allowed, search_reason = access.allows(
                source, result.canonical_url or result.original_url, now=now)
            if not allowed or not search_allowed:
                reasons.append(reason if not allowed else search_reason)
            elif (run.source_id != source.id or raw.source_id != source.id or
                  raw.run_id != run.id or run.status is not RunStatus.SUCCEEDED or
                  raw.http_status != 200 or not (raw.media_type or "").startswith("text/html")):
                reasons.append("collection_provenance_mismatch")
            elif (not raw.content_ref.startswith(("local:", "supabase:")) or
                  len(content) == 0 or len(content) > _MAX_RAW_BYTES or
                  hashlib.sha256(content).hexdigest() != raw.content_sha256):
                reasons.append("raw_content_invalid")
            else:
                search_url = result.canonical_url or result.original_url
                requested = raw.provenance.get("requested_url")
                final = raw.provenance.get("final_url")
                if canonicalize_url(search_url) == canonicalize_url(raw.source_url):
                    link_relation = "DIRECT_URL"
                elif (isinstance(requested, str) and isinstance(final, str) and
                      canonicalize_url(search_url) == canonicalize_url(requested) and
                      canonicalize_url(raw.source_url) == canonicalize_url(final)):
                    link_relation = "COLLECTION_REDIRECT"
                else:
                    reasons.append("search_result_raw_url_unlinked")
                if link_relation:
                    prepared = preprocess_html(content, source_url=raw.source_url,
                                               max_input_chars=12000)
                    resolution = EvidenceResolution(
                        result.id, source.id, run.id, raw.id, search_url, raw.source_url,
                        link_relation, raw.content_ref, raw.content_sha256, raw.collected_at,
                        raw.content_sha256 != previous_raw_sha256
                        if previous_raw_sha256 is not None else None)
        elif official:
            reasons.append("official_raw_or_permission_missing")
        else:
            reasons.append("official_source_unconfirmed")

        facts: dict[str, str] = {}
        precision: dict[str, TimePrecision] = {}
        if resolution and prepared and kind is OfficialKind.OFFICIAL_INDIVIDUAL:
            if (extraction is None or raw is None or source is None or run is None or
                    extraction.raw_document_id != raw.id or
                    extraction.source_id != source.id or extraction.run_id != run.id):
                reasons.append("processing_candidate_missing_or_unlinked")
            else:
                for name, claim in claims_from_extraction(extraction).items():
                    value, kind_of_time, error = _replay_claim(name, claim, prepared, raw.source_url)
                    if error:
                        reasons.append(f"{name}:{error}")
                    elif value is not None:
                        facts[name] = value
                        precision[name] = kind_of_time
                for name in _REQUIRED:
                    if name not in facts:
                        reasons.append(f"{name}:missing_evidence")
                if "registration_url" not in facts:
                    reasons.append("application_method_missing")
                valid_refs = {
                    f"{raw.id}:{item.block_id}" for item in prepared.evidence_blocks
                } | {
                    f"{raw.id}:{item.link_id}" for item in prepared.link_evidence
                }
                if (verification is None or verification.candidate_id != extraction.id or
                        verification.status != VerificationStatus.VERIFIED.value or
                        not verification.evidence_refs or
                        not all(ref in valid_refs for ref in verification.evidence_refs)):
                    reasons.append("processing_verification_missing")
        elif (resolution and prepared and kind is OfficialKind.OFFICIAL_EVENT and
              extraction is not None and raw is not None and source is not None and
              run is not None and extraction.raw_document_id == raw.id and
              extraction.source_id == source.id and extraction.run_id == run.id):
            # Event dates may be recorded, but never stand in for recruitment.
            for name in ("event_start", "event_end"):
                claim = claims_from_extraction(extraction).get(name)
                if claim is None:
                    continue
                value, kind_of_time, error = _replay_claim(name, claim, prepared, raw.source_url)
                if error:
                    reasons.append(f"{name}:{error}")
                elif value is not None:
                    facts[name] = value
                    precision[name] = kind_of_time
        if resolution and kind is OfficialKind.OFFICIAL_LISTING:
            reasons.append("listing_is_not_individual_notice")
        elif resolution and kind is OfficialKind.OFFICIAL_EVENT:
            reasons.append("event_dates_are_not_recruitment_dates")

        window_precision = TimePrecision.UNKNOWN
        start, deadline = facts.get("registration_start"), facts.get("registration_deadline")
        if start and deadline:
            first, last = precision["registration_start"], precision["registration_deadline"]
            if first is last and first in {TimePrecision.DATE_ONLY, TimePrecision.EXACT_TIME}:
                window_precision = first
            else:
                reasons.append("mixed_recruitment_precision")
        event_start, event_end = facts.get("event_start"), facts.get("event_end")
        if event_start and event_end:
            first_day = validated_calendar_date(event_start)
            last_day = validated_calendar_date(event_end)
            if first_day is None or last_day is None or last_day < first_day:
                reasons.append("event_window_invalid")
        evidence = None
        if resolution and review and raw:
            evidence = OfficialPageEvidence(
                raw.source_url, now, authority, page_type,
                recruitment_start_at=parse_explicit_datetime(start)
                if window_precision is TimePrecision.EXACT_TIME and start else None,
                recruitment_deadline_at=parse_explicit_datetime(deadline)
                if window_precision is TimePrecision.EXACT_TIME and deadline else None,
                recruitment_start_on=validated_calendar_date(start)
                if window_precision is TimePrecision.DATE_ONLY and start else None,
                recruitment_deadline_on=validated_calendar_date(deadline)
                if window_precision is TimePrecision.DATE_ONLY and deadline else None,
                event_start_on=validated_calendar_date(event_start) if event_start else None,
                event_end_on=validated_calendar_date(event_end) if event_end else None,
                event_identity=facts.get("title"))
        # Candidate/Source URL repeats are not proven duplicate opportunities.
        # The opportunity identity check below is the only duplicate authority.
        quality_input = replace(
            result, filter_reasons=(),
            canonical_url=(canonicalize_url(raw.source_url)
                           if evidence and raw and link_relation == "COLLECTION_REDIRECT"
                           else result.canonical_url))
        quality = self.quality.assess(quality_input, now=now, evidence=evidence)
        duplicate_reason = None
        if (resolution and raw and source and kind is OfficialKind.OFFICIAL_INDIVIDUAL and
                start and deadline):
            identity = OpportunityIdentity(
                canonicalize_url(raw.source_url), source.id,
                review.official_external_id if review else None,
                raw.content_sha256, facts.get("title"), start, deadline)
            duplicate_reason = _duplicate(identity, previous)
        if duplicate_reason:
            state = EvidenceState.DUPLICATE
            quality = replace(quality, classification=QualityClass.DUPLICATE,
                              duplicate_reason=duplicate_reason, verified_new_opportunity=False)
        elif (not reasons and kind is OfficialKind.OFFICIAL_INDIVIDUAL and
              quality.classification in {QualityClass.ACTIVE_OPPORTUNITY,
                                         QualityClass.UPCOMING_OPPORTUNITY}):
            state = EvidenceState.VERIFIED
        else:
            state = EvidenceState.REVIEW_REQUIRED
            quality = replace(quality, verified_new_opportunity=False)
            if kind is OfficialKind.OFFICIAL_INDIVIDUAL:
                quality = replace(quality, classification=QualityClass.UNVERIFIED,
                                  confidence="UNVERIFIED")
        followups = (_listing_followups(result, prepared, access, source, now)
                     if kind is OfficialKind.OFFICIAL_LISTING and
                     prepared and access and source else ())
        audit = quality.audit_record(candidate_persisted=None)
        audit["evidence_verified"] = state is EvidenceState.VERIFIED
        audit["evidence_resolution_status"] = state.value
        audit["recruitment_time_precision"] = window_precision.value
        metadata = {
            **result.provider_metadata, "quality": audit,
            "official_evidence": {
                "policy_version": "official-evidence-v1",
                "official_kind": kind.value,
                "officiality_reasons": (
                    ["reviewed_source_type", "reviewed_organizer_relationship"]
                    if official else ["officiality_unconfirmed"]),
                "source_reviewed_by": review.reviewer if official and review else None,
                "source_reviewed_at": review.reviewed_at.isoformat()
                if official and review else None,
                "reviewed_relationship": review.relationship if official and review else None,
                "access_snapshot_checked_at": access.checked_at.isoformat()
                if official and access else None,
                "access_snapshot_expires_at": access.expires_at.isoformat()
                if official and access else None,
                "reviewed_terms_sha256": access.terms_sha256
                if official and access else None,
                "access_reason": reasons[0] if resolution is None and reasons else "allowed",
                "resolution": resolution.audit_record() if resolution else None,
                "duplicate_reason": duplicate_reason,
                "review_reasons": list(dict.fromkeys(reasons)),
                "application_method": "OFFICIAL_REGISTRATION_URL"
                if "registration_url" in facts else None,
                "listing_followups": list(followups),
            },
        }
        return EvidenceOutcome(
            replace(result, provider_metadata=metadata), state, kind, official,
            resolution, quality, window_precision, duplicate_reason,
            tuple(dict.fromkeys(reasons)), followups)
