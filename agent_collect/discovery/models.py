"""Discovery domain objects, distinct from registered Sources."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from agent_collect.schemas.models import CollectionMethod, Vertical
from agent_collect.schemas.validation import (
    require_aware, require_confidence, require_enum, require_http_url,
    require_text, require_uuid,
)


class QueryStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class SearchResultStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    FILTERED = "FILTERED"
    CANDIDATE = "CANDIDATE"


class SourceCandidateStatus(StrEnum):
    EVALUATION_PENDING = "EVALUATION_PENDING"
    ACCEPTED = "ACCEPTED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REJECTED = "REJECTED"
    PROMOTED = "PROMOTED"


class EvaluationDecision(StrEnum):
    ACCEPT = "ACCEPT"
    REVIEW = "REVIEW"
    REJECT = "REJECT"


@dataclass(frozen=True, slots=True)
class DiscoveryQuery:
    id: UUID
    text: str
    provider: str
    created_at: datetime
    vertical_hint: Vertical = Vertical.UNKNOWN
    status: QueryStatus = QueryStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result_count: int = 0
    candidate_count: int = 0
    promoted_count: int = 0
    error_summary: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    schedule_id: UUID | None = None
    schedule_window_id: UUID | None = None
    schedule_claim_id: UUID | None = None
    schedule_worker_id: str | None = None

    def __post_init__(self) -> None:
        require_uuid(self.id, "id")
        require_text(self.text, "text")
        require_text(self.provider, "provider")
        require_aware(self.created_at, "created_at")
        require_enum(self.vertical_hint, Vertical, "vertical_hint")
        require_enum(self.status, QueryStatus, "status")
        scheduled = (self.schedule_id, self.schedule_window_id,
                     self.schedule_claim_id, self.schedule_worker_id)
        if any(value is not None for value in scheduled) and not all(value is not None for value in scheduled):
            raise ValueError("scheduled Discovery identity must be complete")
        for name in ("started_at", "completed_at"):
            value = getattr(self, name)
            if value is not None:
                require_aware(value, name)
        if min(self.result_count, self.candidate_count, self.promoted_count) < 0:
            raise ValueError("discovery counts must be non-negative")
        if self.promoted_count > self.candidate_count or self.candidate_count > self.result_count:
            raise ValueError("discovery counts are inconsistent")


@dataclass(frozen=True, slots=True)
class SearchResult:
    id: UUID
    query_id: UUID
    provider: str
    rank: int
    title: str
    original_url: str
    snippet: str
    discovered_at: datetime
    canonical_url: str | None = None
    domain: str | None = None
    status: SearchResultStatus = SearchResultStatus.DISCOVERED
    filter_reasons: tuple[str, ...] = ()
    provider_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_uuid(self.id, "id")
        require_uuid(self.query_id, "query_id")
        require_text(self.provider, "provider")
        if self.rank < 1:
            raise ValueError("rank must be positive")
        require_text(self.title, "title")
        if not isinstance(self.original_url, str):
            raise ValueError("original_url must be text")
        require_aware(self.discovered_at, "discovered_at")
        require_enum(self.status, SearchResultStatus, "status")
        if self.canonical_url is not None:
            require_http_url(self.canonical_url, "canonical_url")


@dataclass(frozen=True, slots=True)
class SourceCandidate:
    id: UUID
    query_id: UUID
    search_result_id: UUID
    original_url: str
    candidate_url: str
    canonical_url: str
    domain: str
    title: str
    snippet: str
    discovered_at: datetime
    status: SourceCandidateStatus = SourceCandidateStatus.EVALUATION_PENDING
    evaluation_decision: EvaluationDecision | None = None
    confidence: float | None = None
    reasons: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    collection_method: CollectionMethod | None = None
    evaluated_at: datetime | None = None
    promoted_source_id: UUID | None = None
    promoted_at: datetime | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("id", "query_id", "search_result_id"):
            require_uuid(getattr(self, name), name)
        for name in ("original_url", "candidate_url", "canonical_url"):
            require_http_url(getattr(self, name), name)
        require_text(self.domain, "domain")
        require_text(self.title, "title")
        require_aware(self.discovered_at, "discovered_at")
        require_enum(self.status, SourceCandidateStatus, "status")
        if self.evaluation_decision is not None:
            require_enum(self.evaluation_decision, EvaluationDecision, "evaluation_decision")
        if self.confidence is not None:
            require_confidence(self.confidence)
        if self.collection_method is not None:
            require_enum(self.collection_method, CollectionMethod, "collection_method")
        for name in ("evaluated_at", "promoted_at"):
            value = getattr(self, name)
            if value is not None:
                require_aware(value, name)
        if self.promoted_source_id is not None:
            require_uuid(self.promoted_source_id, "promoted_source_id")
        if self.status is SourceCandidateStatus.PROMOTED and (self.promoted_source_id is None or self.promoted_at is None):
            raise ValueError("promoted candidate requires source ID and timestamp")


@dataclass(frozen=True, slots=True)
class SourceEvaluation:
    candidate_id: UUID
    decision: EvaluationDecision
    confidence: float
    reasons: tuple[str, ...]
    evidence: tuple[str, ...]
    evaluated_at: datetime
    suggested_method: CollectionMethod | None = None

    def __post_init__(self) -> None:
        require_uuid(self.candidate_id, "candidate_id")
        require_enum(self.decision, EvaluationDecision, "decision")
        require_confidence(self.confidence)
        require_aware(self.evaluated_at, "evaluated_at")
        if self.suggested_method is not None:
            require_enum(self.suggested_method, CollectionMethod, "suggested_method")
