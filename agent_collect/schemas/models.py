"""Agent-owned domain models, independent of Supabase row shapes."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from re import fullmatch
from typing import Any
from uuid import UUID

from agent_collect.schemas.status import (
    CandidateStatus,
    RunStatus,
    SourceStatus,
    VerificationStatus,
)
from agent_collect.schemas.validation import (
    require_aware,
    require_confidence,
    require_date_order,
    require_enum,
    require_http_url,
    require_text,
    require_uuid,
)


class SourceType(StrEnum):
    OFFICIAL = "official"
    AGGREGATOR = "aggregator"
    COMMUNITY = "community"
    UNKNOWN = "unknown"


class CollectionMethod(StrEnum):
    HTTP_HTML = "http_html"
    HTTP = "http"
    REST_API = "rest_api"
    RSS = "rss"
    HTML = "html"
    SITEMAP = "sitemap"
    SEARCH = "search"
    BROWSER = "browser"


class Vertical(StrEnum):
    CONFERENCE = "conference"
    COMPETITION = "competition"
    HACKATHON = "hackathon"
    EDUCATION = "education"
    MEETUP = "meetup"
    GRANT = "grant"
    RESEARCH = "research"
    JOB = "job"
    UNKNOWN = "unknown"


class DuplicateMethod(StrEnum):
    EXACT_URL = "exact_url"
    CANONICAL_URL = "canonical_url"
    NORMALIZED_TITLE = "normalized_title"
    ORGANIZER_DATE = "organizer_date"
    SIMILARITY = "similarity"
    LLM = "llm"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class Source:
    id: UUID
    name: str
    url: str
    source_type: SourceType
    status: SourceStatus
    discovered_at: datetime
    collection_method: CollectionMethod | None = None
    canonical_url: str | None = None
    is_active: bool = False
    last_collected_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    category_hint: str | None = None
    country_code: str | None = None
    language_code: str | None = None
    collection_interval_minutes: int | None = None
    discovery_method: str | None = None
    agent_rationale: str | None = None
    confidence: float | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    collection_config: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_uuid(self.id, "id")
        require_enum(self.source_type, SourceType, "source_type")
        require_enum(self.status, SourceStatus, "status")
        if self.collection_method is not None:
            require_enum(self.collection_method, CollectionMethod, "collection_method")
        require_text(self.name, "name")
        require_http_url(self.url, "url")
        if self.canonical_url is not None:
            require_http_url(self.canonical_url, "canonical_url")
        require_aware(self.discovered_at, "discovered_at")
        for name in ("last_collected_at", "last_success_at", "last_failure_at"):
            value = getattr(self, name)
            if value is not None:
                require_aware(value, name)
        if self.is_active and self.status is not SourceStatus.ACTIVE:
            raise ValueError("active source must have ACTIVE status")
        if self.collection_interval_minutes is not None and self.collection_interval_minutes <= 0:
            raise ValueError("collection_interval_minutes must be positive")
        if self.confidence is not None:
            require_confidence(self.confidence)


@dataclass(frozen=True, slots=True)
class CollectionRun:
    id: UUID
    source_id: UUID
    status: RunStatus
    started_at: datetime
    finished_at: datetime | None = None
    collection_method: CollectionMethod | None = None
    found_count: int = 0
    stored_count: int = 0
    error_count: int = 0
    error_summary: str | None = None
    source_config_snapshot: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_uuid(self.id, "id")
        require_uuid(self.source_id, "source_id")
        require_enum(self.status, RunStatus, "status")
        if self.collection_method is not None:
            require_enum(self.collection_method, CollectionMethod, "collection_method")
        require_aware(self.started_at, "started_at")
        if self.finished_at is not None:
            require_aware(self.finished_at, "finished_at")
            if self.finished_at < self.started_at:
                raise ValueError("finished_at must not precede started_at")
        if min(self.found_count, self.stored_count, self.error_count) < 0:
            raise ValueError("run counts must be non-negative")
        if self.source_config_snapshot.get("trigger") == "scheduled":
            window = self.source_config_snapshot.get("schedule_window_id")
            if not isinstance(window, str):
                raise ValueError("scheduled Run requires a scheduling window ID")
            try:
                parsed_window = UUID(window)
            except ValueError as error:
                raise ValueError("scheduled Run requires a valid UUID window ID") from error
            if str(parsed_window) != window:
                raise ValueError("scheduled Run window ID must use canonical UUID text")


@dataclass(frozen=True, slots=True)
class RawDocument:
    id: UUID
    run_id: UUID
    source_id: UUID
    source_url: str
    content_ref: str
    content_sha256: str
    collected_at: datetime
    media_type: str | None = None
    canonical_url: str | None = None
    http_status: int | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_uuid(self.id, "id")
        require_uuid(self.run_id, "run_id")
        require_uuid(self.source_id, "source_id")
        require_http_url(self.source_url, "source_url")
        if self.canonical_url is not None:
            require_http_url(self.canonical_url, "canonical_url")
        require_text(self.content_ref, "content_ref")
        if not fullmatch(r"[0-9a-f]{64}", self.content_sha256):
            raise ValueError("content_sha256 must be lowercase SHA-256 hex")
        require_aware(self.collected_at, "collected_at")
        if self.http_status is not None and not 100 <= self.http_status <= 599:
            raise ValueError("http_status must be between 100 and 599")


@dataclass(frozen=True, slots=True)
class ExtractedItem:
    id: UUID
    raw_document_id: UUID
    run_id: UUID
    source_id: UUID
    title: str | None = None
    summary: str | None = None
    organizer: str | None = None
    official_url: str | None = None
    raw_fields: dict[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    confidence: float | None = None

    def __post_init__(self) -> None:
        for name in ("id", "raw_document_id", "run_id", "source_id"):
            require_uuid(getattr(self, name), name)
        if self.official_url is not None:
            require_http_url(self.official_url, "official_url")
        if self.confidence is not None:
            require_confidence(self.confidence)


@dataclass(frozen=True, slots=True)
class NormalizedItem:
    id: UUID
    extracted_item_id: UUID
    run_id: UUID
    source_id: UUID
    title: str
    summary: str | None
    organizer: str | None
    official_url: str
    application_start_at: datetime | None = None
    application_deadline_at: datetime | None = None
    event_start_at: datetime | None = None
    event_end_at: datetime | None = None
    format: str | None = None
    price_type: str | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("id", "extracted_item_id", "run_id", "source_id"):
            require_uuid(getattr(self, name), name)
        require_text(self.title, "title")
        require_http_url(self.official_url, "official_url")
        require_date_order(
            self.application_start_at, self.application_deadline_at, "application"
        )
        require_date_order(self.event_start_at, self.event_end_at, "event")


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    item_id: UUID
    vertical: Vertical
    confidence: float
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_uuid(self.item_id, "item_id")
        require_enum(self.vertical, Vertical, "vertical")
        require_confidence(self.confidence)


@dataclass(frozen=True, slots=True)
class DuplicateResult:
    item_id: UUID
    is_duplicate: bool
    method: DuplicateMethod
    confidence: float
    matched_ref: str | None = None

    def __post_init__(self) -> None:
        require_uuid(self.item_id, "item_id")
        require_enum(self.method, DuplicateMethod, "method")
        require_confidence(self.confidence)
        if self.is_duplicate and not self.matched_ref:
            raise ValueError("duplicate result requires matched_ref")
        if self.is_duplicate and self.method is DuplicateMethod.NONE:
            raise ValueError("duplicate result requires a comparison method")


@dataclass(frozen=True, slots=True)
class VerificationResult:
    item_id: UUID
    status: VerificationStatus
    confidence: float
    evidence_refs: tuple[str, ...]
    verified_at: datetime | None = None
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_uuid(self.item_id, "item_id")
        require_enum(self.status, VerificationStatus, "status")
        require_confidence(self.confidence)
        if self.verified_at is not None:
            require_aware(self.verified_at, "verified_at")
        if self.status is VerificationStatus.VERIFIED:
            if self.verified_at is None or not self.evidence_refs:
                raise ValueError("verified result requires time and evidence")


@dataclass(frozen=True, slots=True)
class PublishCandidate:
    """Versioned handoff accepted by a future ServiceDBAdapter."""

    contract_version: int
    agent_item_id: UUID
    agent_source_id: UUID
    run_id: UUID
    vertical: Vertical
    title: str
    summary: str
    organizer: str
    official_url: str
    source_name: str
    verified_at: datetime
    verification_status: VerificationStatus
    confidence: float
    duplicate_result: DuplicateResult
    evidence_refs: tuple[str, ...]
    description: str | None = None
    target_audience: str | None = None
    difficulty: str | None = None
    format: str | None = None
    region: str | None = None
    venue: str | None = None
    price_type: str | None = None
    price_text: str | None = None
    application_start_at: datetime | None = None
    application_deadline_at: datetime | None = None
    event_start_at: datetime | None = None
    event_end_at: datetime | None = None
    source_homepage_url: str | None = None
    external_id: str | None = None
    canonical_url: str | None = None
    image_url: str | None = None
    tag_slugs: tuple[str, ...] = ()
    discovered_at: datetime | None = None
    collected_at: datetime | None = None
    event_time_kind: str = "UNKNOWN"
    timezone_basis: str = "UNKNOWN"
    application_time_kind: str = "UNKNOWN"
    application_timezone_basis: str = "UNKNOWN"

    def __post_init__(self) -> None:
        if self.contract_version != 1:
            raise ValueError("unsupported PublishCandidate contract version")
        for name in ("agent_item_id", "agent_source_id", "run_id"):
            require_uuid(getattr(self, name), name)
        require_enum(self.vertical, Vertical, "vertical")
        require_enum(self.verification_status, VerificationStatus, "verification_status")
        if not isinstance(self.duplicate_result, DuplicateResult):
            raise ValueError("duplicate_result must be DuplicateResult")
        if self.vertical is not Vertical.CONFERENCE:
            raise ValueError("PublishCandidate v1 supports conference only")
        for name in ("title", "summary", "organizer", "source_name"):
            require_text(getattr(self, name), name)
        require_http_url(self.official_url, "official_url")
        for name in ("source_homepage_url", "canonical_url", "image_url"):
            value = getattr(self, name)
            if value is not None:
                require_http_url(value, name)
        require_aware(self.verified_at, "verified_at")
        for name in ("discovered_at", "collected_at"):
            value = getattr(self, name)
            if value is not None:
                require_aware(value, name)
        for name in ("application_start_at", "application_deadline_at",
                     "event_start_at", "event_end_at"):
            value = getattr(self, name)
            if value is not None:
                require_aware(value, name)
        require_confidence(self.confidence)
        if self.verification_status is not VerificationStatus.VERIFIED:
            raise ValueError("PublishCandidate requires VERIFIED result")
        if self.duplicate_result.item_id != self.agent_item_id:
            raise ValueError("duplicate_result item_id must match agent_item_id")
        if self.duplicate_result.is_duplicate:
            raise ValueError("duplicate cannot become PublishCandidate")
        if not self.evidence_refs or any(not ref.strip() for ref in self.evidence_refs):
            raise ValueError("PublishCandidate requires evidence_refs")
        require_date_order(
            self.application_start_at, self.application_deadline_at, "application"
        )
        require_date_order(self.event_start_at, self.event_end_at, "event")
        if self.difficulty not in {None, "beginner", "intermediate", "advanced", "all"}:
            raise ValueError("invalid difficulty")
        if self.format not in {None, "online", "offline", "hybrid"}:
            raise ValueError("invalid format")
        if self.price_type not in {None, "free", "paid", "mixed", "unknown"}:
            raise ValueError("invalid price_type")
        if self.event_time_kind not in {"UNKNOWN", "DATE_ONLY", "DATETIME_CONFIRMED"}:
            raise ValueError("invalid event_time_kind")
        if self.timezone_basis not in {"UNKNOWN", "CONFIRMED", "DERIVED"}:
            raise ValueError("invalid timezone_basis")
        if self.application_time_kind not in {"UNKNOWN", "DATE_ONLY", "DATETIME_CONFIRMED"}:
            raise ValueError("invalid application_time_kind")
        if self.application_timezone_basis not in {"UNKNOWN", "CONFIRMED", "DERIVED"}:
            raise ValueError("invalid application_timezone_basis")
        if any(not fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", tag) for tag in self.tag_slugs):
            raise ValueError("tag_slugs must be service-compatible slugs")
