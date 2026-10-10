"""Scheduling domain contracts, separate from Agent DB row shapes."""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import md5
from uuid import UUID

from agent_collect.schemas.models import CollectionMethod, Source
from agent_collect.schemas.validation import require_aware


def window_id_for(source_id: UUID, next_collection_at: datetime) -> UUID:
    """Mirror 005's Source + UTC epoch-microsecond window identity.

    MD5 encodes a stable 128-bit identifier; it is not used for authentication.
    """
    require_aware(next_collection_at, "next_collection_at")
    utc = next_collection_at.astimezone(timezone.utc)
    delta = utc - datetime(1970, 1, 1, tzinfo=timezone.utc)
    micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    return UUID(hex=md5(f"{source_id}:{micros}".encode("ascii"),
                        usedforsecurity=False).hexdigest())


@dataclass(frozen=True, slots=True)
class DueSource:
    source: Source
    window_id: UUID
    interval_minutes: int
    next_collection_at: datetime

    def __post_init__(self) -> None:
        require_aware(self.next_collection_at, "next_collection_at")
        if self.interval_minutes <= 0:
            raise ValueError("collection interval must be positive")


@dataclass(frozen=True, slots=True)
class CollectionClaim:
    source_id: UUID
    window_id: UUID
    claim_id: UUID
    worker_id: str
    claimed_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        require_aware(self.claimed_at, "claimed_at")
        require_aware(self.expires_at, "expires_at")
        if not self.worker_id.strip() or self.expires_at <= self.claimed_at:
            raise ValueError("invalid collection claim")


@dataclass(frozen=True, slots=True)
class DuePreview:
    source_id: UUID
    source_name: str
    collection_method: CollectionMethod
    interval_minutes: int
    next_collection_at: datetime


@dataclass(frozen=True, slots=True)
class WorkerSummary:
    worker_id: str
    started_at: datetime
    finished_at: datetime
    dry_run: bool
    due_count: int
    claimed_count: int
    success_count: int
    failure_count: int
    skipped_count: int
    due: tuple[DuePreview, ...] = ()

    def __post_init__(self) -> None:
        require_aware(self.started_at, "started_at")
        require_aware(self.finished_at, "finished_at")
        if self.finished_at < self.started_at or min(
            self.due_count, self.claimed_count, self.success_count,
            self.failure_count, self.skipped_count,
        ) < 0:
            raise ValueError("invalid worker summary")
