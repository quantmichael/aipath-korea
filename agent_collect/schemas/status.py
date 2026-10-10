"""Central workflow vocabulary and permitted candidate transitions."""

from enum import StrEnum


class CandidateStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    SOURCE_ANALYZED = "SOURCE_ANALYZED"
    COLLECTED = "COLLECTED"
    RAW_STORED = "RAW_STORED"
    EXTRACTED = "EXTRACTED"
    NORMALIZED = "NORMALIZED"
    CLASSIFIED = "CLASSIFIED"
    DEDUP_CHECKED = "DEDUP_CHECKED"
    DUPLICATE = "DUPLICATE"
    STAGED = "STAGED"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REJECTED = "REJECTED"
    PUBLISH_READY = "PUBLISH_READY"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class VerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REJECTED = "REJECTED"


class PublishStatus(StrEnum):
    NOT_REQUESTED = "NOT_REQUESTED"
    READY = "READY"
    PUBLISHED = "PUBLISHED"
    HELD = "HELD"
    FAILED = "FAILED"


class SourceStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    REVIEW = "REVIEW"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    HOLD = "HOLD"
    REJECTED = "REJECTED"
    DISABLED = "DISABLED"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


_ALLOWED_TRANSITIONS: dict[CandidateStatus, frozenset[CandidateStatus]] = {
    CandidateStatus.DISCOVERED: frozenset({CandidateStatus.SOURCE_ANALYZED}),
    CandidateStatus.SOURCE_ANALYZED: frozenset({CandidateStatus.COLLECTED}),
    CandidateStatus.COLLECTED: frozenset({CandidateStatus.RAW_STORED}),
    CandidateStatus.RAW_STORED: frozenset({CandidateStatus.EXTRACTED}),
    CandidateStatus.EXTRACTED: frozenset({CandidateStatus.NORMALIZED}),
    CandidateStatus.NORMALIZED: frozenset({CandidateStatus.CLASSIFIED}),
    CandidateStatus.CLASSIFIED: frozenset({CandidateStatus.DEDUP_CHECKED}),
    CandidateStatus.DEDUP_CHECKED: frozenset(
        {CandidateStatus.DUPLICATE, CandidateStatus.STAGED}
    ),
    CandidateStatus.STAGED: frozenset({CandidateStatus.VERIFYING}),
    CandidateStatus.VERIFYING: frozenset(
        {
            CandidateStatus.VERIFIED,
            CandidateStatus.REVIEW_REQUIRED,
            CandidateStatus.REJECTED,
        }
    ),
    CandidateStatus.VERIFIED: frozenset({CandidateStatus.PUBLISH_READY}),
    CandidateStatus.PUBLISH_READY: frozenset({CandidateStatus.PUBLISHED}),
    CandidateStatus.REVIEW_REQUIRED: frozenset(
        {CandidateStatus.VERIFYING, CandidateStatus.REJECTED}
    ),
    CandidateStatus.DUPLICATE: frozenset(),
    CandidateStatus.REJECTED: frozenset(),
    CandidateStatus.PUBLISHED: frozenset(),
    CandidateStatus.FAILED: frozenset(),
}


def can_transition(current: CandidateStatus, target: CandidateStatus) -> bool:
    """Return whether a persisted candidate may move to the next state."""
    if target is CandidateStatus.FAILED:
        return current not in {
            CandidateStatus.DUPLICATE,
            CandidateStatus.REJECTED,
            CandidateStatus.PUBLISHED,
            CandidateStatus.FAILED,
        }
    return target in _ALLOWED_TRANSITIONS[current]


def require_transition(current: CandidateStatus, target: CandidateStatus) -> None:
    if not can_transition(current, target):
        raise ValueError(f"invalid candidate transition: {current} -> {target}")
