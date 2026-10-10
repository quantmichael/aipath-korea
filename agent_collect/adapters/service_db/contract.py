"""Versioned Service publish port and deterministic outcome contract."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from agent_collect.schemas.models import PublishCandidate
from agent_collect.schemas.validation import (
    require_aware,
    require_enum,
    require_text,
    require_uuid,
)


class ServicePublicationStatus(StrEnum):
    SCHEDULED = "scheduled"
    OPEN = "open"
    ONGOING = "ongoing"
    CLOSED = "closed"
    ENDED = "ended"


class PublishAction(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    HELD = "held"
    CONFLICT = "conflict"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    FAILED = "failed"


class PublishOutcomeStatus(StrEnum):
    PUBLISHED = "PUBLISHED"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class PublishResult:
    agent_item_id: UUID
    action: PublishAction
    service_opportunity_id: UUID | None = None
    service_slug: str | None = None
    published_status: ServicePublicationStatus | None = None
    published_at: datetime | None = None
    error_code: str | None = None
    error_detail: str | None = None
    source_id: UUID | None = None
    conflict_status: str | None = None

    @property
    def candidate_id(self) -> UUID:
        return self.agent_item_id

    @property
    def service_record_id(self) -> UUID | None:
        return self.service_opportunity_id

    @property
    def status(self) -> PublishOutcomeStatus:
        if self.action in {PublishAction.CREATED, PublishAction.UPDATED}:
            return PublishOutcomeStatus.PUBLISHED
        if self.action is PublishAction.SKIPPED_DUPLICATE:
            return PublishOutcomeStatus.SKIPPED_DUPLICATE
        if self.action is PublishAction.FAILED:
            return PublishOutcomeStatus.FAILED
        return PublishOutcomeStatus.REVIEW_REQUIRED

    def __post_init__(self) -> None:
        require_uuid(self.agent_item_id, "agent_item_id")
        require_enum(self.action, PublishAction, "action")
        if self.service_opportunity_id is not None:
            require_uuid(self.service_opportunity_id, "service_opportunity_id")
        if self.source_id is not None:
            require_uuid(self.source_id, "source_id")
        if self.published_status is not None:
            require_enum(self.published_status, ServicePublicationStatus, "published_status")
        if self.action in {PublishAction.CREATED, PublishAction.UPDATED}:
            if not self.service_opportunity_id or not self.service_slug:
                raise ValueError("successful publish requires service ID and slug")
            if self.published_status is None or self.published_at is None:
                raise ValueError("successful publish requires status and time")
            require_aware(self.published_at, "published_at")
        elif self.action in {PublishAction.HELD, PublishAction.CONFLICT, PublishAction.FAILED}:
            require_text(self.error_code, "error_code")


class PublicationStatusResolver(Protocol):
    """A deterministic, date-based service status policy; never an LLM call."""

    def resolve(
        self, candidate: PublishCandidate, *, at: datetime
    ) -> ServicePublicationStatus | None: ...


class ServiceDBAdapter(Protocol):
    """Only PublishCandidate crosses into the existing service database."""

    def publish(self, candidate: PublishCandidate) -> PublishResult: ...
