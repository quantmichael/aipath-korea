"""Validated state and node boundary for a future LangGraph workflow."""

from dataclasses import dataclass, replace
from typing import Protocol
from uuid import UUID

from agent_collect.schemas.models import (
    ClassificationResult,
    DuplicateResult,
    ExtractedItem,
    NormalizedItem,
    RawDocument,
    Source,
    VerificationResult,
)
from agent_collect.schemas.status import (
    CandidateStatus,
    PublishStatus,
    VerificationStatus,
    require_transition,
)
from agent_collect.schemas.validation import require_confidence


@dataclass(frozen=True, slots=True)
class AgentCollectState:
    run_id: UUID
    status: CandidateStatus = CandidateStatus.DISCOVERED
    source: Source | None = None
    raw_document: RawDocument | None = None
    extracted_item: ExtractedItem | None = None
    normalized_item: NormalizedItem | None = None
    classification: ClassificationResult | None = None
    duplicate_result: DuplicateResult | None = None
    verification_result: VerificationResult | None = None
    confidence: float | None = None
    errors: tuple[str, ...] = ()
    retry_count: int = 0
    publish_status: PublishStatus = PublishStatus.NOT_REQUESTED

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, UUID):
            raise ValueError("run_id must be UUID")
        if not isinstance(self.status, CandidateStatus):
            raise ValueError("status must be CandidateStatus")
        if not isinstance(self.publish_status, PublishStatus):
            raise ValueError("publish_status must be PublishStatus")
        if self.retry_count < 0:
            raise ValueError("retry_count must be non-negative")
        if self.confidence is not None:
            require_confidence(self.confidence)
        if self.raw_document and self.raw_document.run_id != self.run_id:
            raise ValueError("raw_document belongs to a different run")
        if self.extracted_item and self.extracted_item.run_id != self.run_id:
            raise ValueError("extracted_item belongs to a different run")
        if self.normalized_item and self.normalized_item.run_id != self.run_id:
            raise ValueError("normalized_item belongs to a different run")
        if self.source and self.raw_document and self.source.id != self.raw_document.source_id:
            raise ValueError("source and raw_document source IDs differ")
        if self.extracted_item and self.raw_document:
            if self.extracted_item.raw_document_id != self.raw_document.id:
                raise ValueError("extracted_item belongs to a different raw document")
        if self.normalized_item and self.extracted_item:
            if self.normalized_item.extracted_item_id != self.extracted_item.id:
                raise ValueError("normalized_item belongs to a different extracted item")
        if self.status in {CandidateStatus.VERIFIED, CandidateStatus.PUBLISH_READY, CandidateStatus.PUBLISHED}:
            if self.verification_result is None:
                raise ValueError("verified/publish state requires verification_result")
            if self.verification_result.status is not VerificationStatus.VERIFIED:
                raise ValueError("verification_result must be VERIFIED")
        if self.status is CandidateStatus.PUBLISHED and self.publish_status is not PublishStatus.PUBLISHED:
            raise ValueError("PUBLISHED state requires PUBLISHED publish_status")

    def transition(self, target: CandidateStatus, **updates: object) -> "AgentCollectState":
        """Apply a node result after checking the persisted state transition."""
        require_transition(self.status, target)
        return replace(self, status=target, **updates)


class AgentCollectNode(Protocol):
    """Future graph node: accepts a validated snapshot, returns a new snapshot."""

    def __call__(self, state: AgentCollectState) -> AgentCollectState: ...
