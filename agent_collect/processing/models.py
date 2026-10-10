"""Processing job contracts independent of Supabase row representation."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from agent_collect.schemas.validation import require_aware


class ProcessingStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCEEDED = "SUCCEEDED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    BUDGET_BLOCKED = "BUDGET_BLOCKED"


class ProcessingError(StrEnum):
    RAW_NOT_FOUND = "RAW_NOT_FOUND"
    HASH_MISMATCH = "HASH_MISMATCH"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    PROVIDER_FAILED = "PROVIDER_FAILED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    QUALITY_GATE_FAILED = "QUALITY_GATE_FAILED"
    CROSS_RAW_CACHE = "CROSS_RAW_CACHE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True, slots=True)
class ProcessingPolicy:
    version: str = "conference-processing-v1"
    max_jobs_per_run: int = 1
    lease_seconds: int = 300
    retry_backoff_minutes: tuple[int, ...] = (15, 60, 360)
    max_attempts: int = 4

    def __post_init__(self) -> None:
        if (not self.version or self.max_jobs_per_run not in range(1, 101) or
                self.lease_seconds not in range(1, 86401) or
                self.max_attempts < 1 or not self.retry_backoff_minutes or
                any(item < 1 for item in self.retry_backoff_minutes)):
            raise ValueError("invalid processing policy")

    def failure_delay_minutes(self, attempt_count: int) -> int:
        if attempt_count < 1:
            raise ValueError("attempt count must be positive")
        return self.retry_backoff_minutes[
            min(attempt_count, len(self.retry_backoff_minutes)) - 1]

    def review_needed(self, attempt_count: int) -> bool:
        return attempt_count >= self.max_attempts


@dataclass(frozen=True, slots=True)
class ProcessingJob:
    id: UUID | None
    raw_document_id: UUID
    version: str
    status: ProcessingStatus
    attempt_count: int
    next_attempt_at: datetime

    def __post_init__(self) -> None:
        require_aware(self.next_attempt_at, "next_attempt_at")
        if self.attempt_count < 0:
            raise ValueError("negative attempt count")
        if self.id is None and self.status is not ProcessingStatus.PENDING:
            raise ValueError("unqueued preview must be pending")


@dataclass(frozen=True, slots=True)
class ProcessingClaim:
    job_id: UUID
    raw_document_id: UUID
    version: str
    claim_id: UUID
    worker_id: str
    claimed_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        require_aware(self.claimed_at, "claimed_at")
        require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.claimed_at or not self.worker_id.strip():
            raise ValueError("invalid processing lease")


@dataclass(frozen=True, slots=True)
class ProcessingSummary:
    worker_id: str
    started_at: datetime
    finished_at: datetime
    dry_run: bool
    due_count: int
    claimed_count: int
    processed_count: int
    publish_ready_count: int
    review_count: int
    rejected_count: int
    failed_count: int
    budget_blocked_count: int
    due: tuple[ProcessingJob, ...]

    def __post_init__(self) -> None:
        require_aware(self.started_at, "started_at")
        require_aware(self.finished_at, "finished_at")
        if min(self.due_count,self.claimed_count,self.processed_count,
               self.publish_ready_count,self.review_count,self.rejected_count,
               self.failed_count,self.budget_blocked_count) < 0:
            raise ValueError("negative processing summary count")
