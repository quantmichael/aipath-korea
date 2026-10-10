"""Human-reviewed Source lifecycle. Discovery signals are never approval."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from typing import TYPE_CHECKING

from agent_collect.registry.service import canonicalize_url
from agent_collect.schemas.models import Source, SourceType
from agent_collect.schemas.status import SourceStatus
from agent_collect.schemas.validation import require_aware, require_text

if TYPE_CHECKING:
    from agent_collect.repositories.source_review import SourceReviewRepository


class SourceDecision(StrEnum):
    REVIEW_STARTED = "REVIEW_STARTED"
    TYPE_CHANGED = "TYPE_CHANGED"
    APPROVED = "APPROVED"
    ACTIVATED = "ACTIVATED"
    HELD = "HELD"
    REJECTED = "REJECTED"
    DISABLED = "DISABLED"


@dataclass(frozen=True, slots=True)
class SourceReviewCommand:
    source_id: UUID
    expected_status: SourceStatus
    target_status: SourceStatus
    reviewer: str
    reason: str
    decision: SourceDecision
    reviewed_at: datetime
    decision_id: UUID
    source_type: SourceType | None = None
    officiality_confirmed: bool | None = None
    collection_allowed: bool | None = None

    def __post_init__(self) -> None:
        require_text(self.reviewer, "reviewer")
        require_text(self.reason, "reason")
        require_aware(self.reviewed_at, "reviewed_at")
        if not isinstance(self.source_id, UUID) or not isinstance(self.decision_id, UUID):
            raise ValueError("source_id and decision_id must be UUID")


@dataclass(frozen=True, slots=True)
class SourceReviewEvent:
    command: SourceReviewCommand
    canonical_url: str
    domain: str
    source_type_before: SourceType
    source_type_after: SourceType
    approval_decision: str
    activation_decision: str
    officiality_confirmed: bool | None
    collection_allowed: bool | None


def source_domain(source: Source) -> str:
    return (urlsplit(canonicalize_url(source.canonical_url or source.url))
            .hostname or "").removeprefix("www.")


def _trust(source: Source, source_type: SourceType) -> str:
    if source_type is SourceType.OFFICIAL:
        return "OFFICIAL"
    if source_type is SourceType.AGGREGATOR and (source.confidence or 0) >= 0.8:
        return "TRUSTED"
    raise ValueError("approval requires reviewed official or trusted aggregator type")


def source_is_collectible(source: Source) -> bool:
    approval = (source.provenance.get("source_review") or {}).get("approval") or {}
    if source.status is not SourceStatus.ACTIVE or not source.is_active or source.collection_method is None:
        return False
    try:
        trust = _trust(source, source.source_type)
    except ValueError:
        return False
    return bool(
        approval.get("decision") == "APPROVED"
        and approval.get("canonical_url") == source.canonical_url
        and approval.get("domain") == source_domain(source)
        and approval.get("source_type") == source.source_type.value
        and approval.get("trust") == trust
        and approval.get("confidence") == source.confidence
        and approval.get("collection_method") == source.collection_method.value
        and approval.get("collection_config") == source.collection_config
        and approval.get("collection_allowed") is True
        and isinstance(approval.get("officiality_confirmed"), bool)
        and (source.source_type is not SourceType.OFFICIAL or
             approval.get("officiality_confirmed") is True)
        and approval.get("reviewer") and approval.get("reviewed_at")
    )


def decide_source_transition(source: Source, command: SourceReviewCommand
                             ) -> tuple[Source, SourceReviewEvent]:
    """Pure state transition shared by in-memory workflow and SQL contract tests."""
    if source.id != command.source_id or source.status is not command.expected_status:
        raise ValueError("source status changed; review again")
    current, target = source.status, command.target_status
    decision = command.decision
    expected = {
        SourceDecision.REVIEW_STARTED: (SourceStatus.REVIEW,
            {SourceStatus.CANDIDATE, SourceStatus.HOLD, SourceStatus.REJECTED,
             SourceStatus.DISABLED}),
        SourceDecision.TYPE_CHANGED: (SourceStatus.REVIEW, {SourceStatus.REVIEW}),
        SourceDecision.APPROVED: (SourceStatus.APPROVED, {SourceStatus.REVIEW}),
        SourceDecision.ACTIVATED: (SourceStatus.ACTIVE, {SourceStatus.APPROVED}),
        SourceDecision.HELD: (SourceStatus.HOLD,
            {SourceStatus.REVIEW, SourceStatus.APPROVED, SourceStatus.ACTIVE}),
        SourceDecision.REJECTED: (SourceStatus.REJECTED, {SourceStatus.REVIEW}),
        SourceDecision.DISABLED: (SourceStatus.DISABLED, {SourceStatus.ACTIVE}),
    }
    required_target, origins = expected[decision]
    if target is not required_target or current not in origins:
        raise ValueError(f"invalid source transition: {current} -> {target}")
    if decision is SourceDecision.TYPE_CHANGED:
        if command.source_type is None or command.source_type is source.source_type:
            raise ValueError("reviewed type change requires a different source_type")
    elif command.source_type is not None:
        raise ValueError("source_type may change only during REVIEW")
    if decision is SourceDecision.APPROVED:
        if command.collection_allowed is not True or not isinstance(command.officiality_confirmed, bool):
            raise ValueError("human collection permission and officiality decision required")
        if source.source_type is SourceType.OFFICIAL and command.officiality_confirmed is not True:
            raise ValueError("official source requires confirmed officiality")
    elif command.collection_allowed is not None or command.officiality_confirmed is not None:
        raise ValueError("permission decisions belong to approval")
    new_type = command.source_type or source.source_type
    review = dict(source.provenance.get("source_review") or {})
    if decision is SourceDecision.APPROVED:
        if source.collection_method is None:
            raise ValueError("collection method required for approval")
        review["approval"] = {
            "decision": "APPROVED", "reviewer": command.reviewer,
            "reason": command.reason, "reviewed_at": command.reviewed_at.isoformat(),
            "canonical_url": source.canonical_url, "domain": source_domain(source),
            "source_type": new_type.value, "trust": _trust(source, new_type),
            "confidence": source.confidence,
            "collection_method": source.collection_method.value,
            "collection_config": source.collection_config,
            "officiality_confirmed": command.officiality_confirmed,
            "collection_allowed": command.collection_allowed,
        }
    elif decision in {SourceDecision.REVIEW_STARTED, SourceDecision.TYPE_CHANGED,
                      SourceDecision.HELD, SourceDecision.REJECTED, SourceDecision.DISABLED}:
        review.pop("approval", None)
    if decision is SourceDecision.ACTIVATED and not source_is_approved(source):
        raise ValueError("activation requires matching human approval record")
    review["last_decision_id"] = str(command.decision_id)
    review["last_decision"] = decision.value
    review["review_status"] = target.value
    updated = replace(source, source_type=new_type, status=target,
                      is_active=target is SourceStatus.ACTIVE,
                      provenance={**source.provenance, "source_review": review})
    event = SourceReviewEvent(
        command, source.canonical_url or canonicalize_url(source.url),
        source_domain(source), source.source_type, new_type,
        ("APPROVED" if decision is SourceDecision.APPROVED else
         "REJECTED" if decision is SourceDecision.REJECTED else
         "HELD" if decision is SourceDecision.HELD else "UNCHANGED"),
        ("ACTIVATED" if decision is SourceDecision.ACTIVATED else
         "DISABLED" if decision is SourceDecision.DISABLED else "NOT_ACTIVATED"),
        command.officiality_confirmed, command.collection_allowed,
    )
    return updated, event


def source_is_approved(source: Source) -> bool:
    return (source.status is SourceStatus.APPROVED and
            source_is_collectible(replace(source, status=SourceStatus.ACTIVE, is_active=True)))


class SourceApprovalService:
    def __init__(self, repository: "SourceReviewRepository") -> None:
        self.repository = repository

    def _act(self, source_id: UUID, target: SourceStatus, decision: SourceDecision,
             *, reviewer: str, reason: str, source_type: SourceType | None = None,
             officiality_confirmed: bool | None = None,
             collection_allowed: bool | None = None,
             decision_id: UUID | None = None) -> Source:
        source = self.repository.get_source(source_id)
        if source is None:
            raise KeyError(f"unknown source: {source_id}")
        command = SourceReviewCommand(source_id, source.status, target, reviewer, reason,
                                      decision, datetime.now(timezone.utc),
                                      decision_id or uuid4(), source_type,
                                      officiality_confirmed, collection_allowed)
        return self.repository.transition(command)

    def begin_review(self, source_id: UUID, *, reviewer: str, reason: str,
                     decision_id: UUID | None = None) -> Source:
        return self._act(source_id, SourceStatus.REVIEW, SourceDecision.REVIEW_STARTED,
                         reviewer=reviewer, reason=reason, decision_id=decision_id)

    def change_source_type(self, source_id: UUID, source_type: SourceType, *,
                           reviewer: str, reason: str, decision_id: UUID | None = None) -> Source:
        return self._act(source_id, SourceStatus.REVIEW, SourceDecision.TYPE_CHANGED,
                         reviewer=reviewer, reason=reason, source_type=source_type,
                         decision_id=decision_id)

    def approve(self, source_id: UUID, *, reviewer: str, reason: str,
                officiality_confirmed: bool, collection_allowed: bool,
                decision_id: UUID | None = None) -> Source:
        return self._act(source_id, SourceStatus.APPROVED, SourceDecision.APPROVED,
                         reviewer=reviewer, reason=reason, decision_id=decision_id,
                         officiality_confirmed=officiality_confirmed,
                         collection_allowed=collection_allowed)

    def activate(self, source_id: UUID, *, reviewer: str, reason: str,
                 decision_id: UUID | None = None) -> Source:
        return self._act(source_id, SourceStatus.ACTIVE, SourceDecision.ACTIVATED,
                         reviewer=reviewer, reason=reason, decision_id=decision_id)

    def hold(self, source_id: UUID, *, reviewer: str, reason: str,
             decision_id: UUID | None = None) -> Source:
        return self._act(source_id, SourceStatus.HOLD, SourceDecision.HELD,
                         reviewer=reviewer, reason=reason, decision_id=decision_id)

    def reject(self, source_id: UUID, *, reviewer: str, reason: str,
               decision_id: UUID | None = None) -> Source:
        return self._act(source_id, SourceStatus.REJECTED, SourceDecision.REJECTED,
                         reviewer=reviewer, reason=reason, decision_id=decision_id)

    def disable(self, source_id: UUID, *, reviewer: str, reason: str,
                decision_id: UUID | None = None) -> Source:
        return self._act(source_id, SourceStatus.DISABLED, SourceDecision.DISABLED,
                         reviewer=reviewer, reason=reason, decision_id=decision_id)
