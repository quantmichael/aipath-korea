"""Schedule persistence contract and an atomic in-memory test implementation."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Callable, Protocol
from uuid import UUID

from agent_collect.registry.approval import source_domain, source_is_collectible
from agent_collect.repositories.contracts import SourceRepository
from agent_collect.schemas.models import CollectionRun, Source
from agent_collect.schemas.status import RunStatus
from agent_collect.schemas.validation import require_aware
from agent_collect.scheduling.models import CollectionClaim, DueSource, WorkerSummary, window_id_for
from agent_collect.scheduling.policy import CollectionSchedulePolicy


class CollectionScheduleRepository(Protocol):
    def list_due(self, limit: int) -> list[DueSource]: ...

    def claim(self, source_id: UUID, *, worker_id: str, claim_id: UUID,
              policy: CollectionSchedulePolicy) -> CollectionClaim | None: ...

    def complete(self, claim: CollectionClaim, *, run: CollectionRun | None,
                 policy: CollectionSchedulePolicy) -> RunStatus: ...

    def record_summary(self, summary: WorkerSummary) -> None: ...


@dataclass(slots=True)
class ScheduleRow:
    source_id: UUID
    window_id: UUID
    interval_minutes: int
    next_collection_at: datetime
    last_collection_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    consecutive_failures: int = 0
    review_needed: bool = False
    claim: CollectionClaim | None = None


class InMemoryCollectionScheduleRepository:
    def __init__(self, sources: SourceRepository,
                 *, clock: Callable[[], datetime] | None = None) -> None:
        self.sources = sources
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._rows: dict[UUID, ScheduleRow] = {}
        self._completed: dict[tuple[UUID, UUID], CollectionRun] = {}
        self._terminal: dict[UUID, tuple[UUID, UUID, RunStatus, UUID | None]] = {}
        self.summaries: list[WorkerSummary] = []
        self._lock = RLock()

    def seed(self, source: Source, *, next_collection_at: datetime,
             interval_minutes: int = 720) -> ScheduleRow:
        require_aware(next_collection_at, "next_collection_at")
        if interval_minutes <= 0:
            raise ValueError("interval must be positive")
        with self._lock:
            row = ScheduleRow(source.id, window_id_for(source.id, next_collection_at),
                              interval_minutes, next_collection_at)
            self._rows[source.id] = row
            return row

    def get(self, source_id: UUID) -> ScheduleRow | None:
        with self._lock:
            return self._rows.get(source_id)

    def list_due(self, limit: int) -> list[DueSource]:
        now = self.clock()
        require_aware(now, "now")
        if limit <= 0:
            raise ValueError("due limit must be positive")
        with self._lock:
            due = []
            for row in sorted(self._rows.values(), key=lambda item: (item.next_collection_at, str(item.source_id))):
                source = self.sources.get(row.source_id)
                if (source is not None and source_is_collectible(source) and not row.review_needed
                        and row.next_collection_at <= now
                        and (row.claim is None or row.claim.expires_at <= now)):
                    due.append(DueSource(source, row.window_id, row.interval_minutes,
                                         row.next_collection_at))
                    if len(due) == limit:
                        break
            return due

    def _advance(self, row: ScheduleRow, run: CollectionRun | None,
                 finished_at: datetime, policy: CollectionSchedulePolicy) -> None:
        row.last_collection_at = finished_at
        if run is not None and run.status is RunStatus.SUCCEEDED:
            row.last_success_at = finished_at
            row.consecutive_failures = 0
            row.review_needed = False
            row.next_collection_at = policy.after_success(finished_at, row.interval_minutes)
        else:
            row.last_failure_at = finished_at
            row.consecutive_failures += 1
            row.review_needed = policy.review_needed(row.consecutive_failures)
            row.next_collection_at = policy.after_failure(finished_at, row.consecutive_failures)
        row.window_id = window_id_for(row.source_id, row.next_collection_at)
        row.claim = None

    def record_completed_run(self, source_id: UUID, window_id: UUID, run: CollectionRun) -> None:
        if (run.source_id != source_id or run.status not in {RunStatus.SUCCEEDED, RunStatus.FAILED}
                or run.source_config_snapshot.get("trigger") != "scheduled"
                or run.source_config_snapshot.get("schedule_window_id") != str(window_id)):
            raise ValueError("completed scheduled run required")
        with self._lock:
            previous = self._completed.get((source_id, window_id))
            if previous is not None and previous.id != run.id:
                raise ValueError("scheduled CollectionRun window already exists")
            if previous is not None and previous.status is not run.status:
                raise ValueError("terminal scheduled Run status is immutable")
            self._completed[(source_id, window_id)] = run

    def claim(self, source_id: UUID, *, worker_id: str, claim_id: UUID,
              policy: CollectionSchedulePolicy) -> CollectionClaim | None:
        now = self.clock()
        require_aware(now, "now")
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        with self._lock:
            row = self._rows.get(source_id)
            source = self.sources.get(source_id)
            if row is None or source is None or not source_is_collectible(source) or row.review_needed:
                return None
            if row.claim is not None and row.claim.expires_at <= now:
                completed = self._completed.get((source_id, row.window_id))
                previous_claim = row.claim
                status = (RunStatus.SUCCEEDED if completed is not None and
                          completed.status is RunStatus.SUCCEEDED else RunStatus.FAILED)
                finished_at = (completed.finished_at if status is RunStatus.SUCCEEDED
                               and completed is not None else now)
                assert finished_at is not None
                self._terminal[previous_claim.claim_id] = (
                    source_id, row.window_id, status, completed.id if completed else None)
                self._advance(row, completed if status is RunStatus.SUCCEEDED else None,
                              finished_at, policy)
                return None
            if row.next_collection_at > now or row.claim is not None:
                return None
            domain = source_domain(source)
            occupied = sum(
                1 for other in self._rows.values()
                if other.claim is not None and other.claim.expires_at > now
                and (other_source := self.sources.get(other.source_id)) is not None
                and source_domain(other_source) == domain
            )
            if occupied >= policy.max_concurrent_per_domain:
                return None
            claim = CollectionClaim(source_id, row.window_id, claim_id, worker_id, now,
                                    now + timedelta(seconds=policy.lease_seconds))
            row.claim = claim
            return claim

    def complete(self, claim: CollectionClaim, *, run: CollectionRun | None,
                 policy: CollectionSchedulePolicy) -> RunStatus:
        finished_at = self.clock()
        require_aware(finished_at, "finished_at")
        with self._lock:
            prior = self._terminal.get(claim.claim_id)
            if prior is not None:
                if prior[:2] != (claim.source_id, claim.window_id) or (run is not None and run.id != prior[3]):
                    raise ValueError("Run ID does not match completed claim")
                return prior[2]
            row = self._rows.get(claim.source_id)
            if row is None or row.claim != claim or row.window_id != claim.window_id:
                raise ValueError("stale collection claim")
            if run is not None:
                snapshot = run.source_config_snapshot
                if (run.source_id != claim.source_id or
                        run.status not in {RunStatus.SUCCEEDED, RunStatus.FAILED} or
                        snapshot.get("trigger") != "scheduled" or
                        snapshot.get("claim_id") != str(claim.claim_id) or
                        snapshot.get("schedule_window_id") != str(claim.window_id) or
                        snapshot.get("worker_id") != claim.worker_id):
                    raise ValueError("run does not match collection claim")
                previous = self._completed.get((claim.source_id, claim.window_id))
                if previous is not None and previous.id != run.id:
                    raise ValueError("Run ID does not match persisted window")
                if previous is not None and previous.status is RunStatus.FAILED and run.status is RunStatus.SUCCEEDED:
                    raise ValueError("terminal scheduled Run status is immutable")
                if previous is None or previous.status is not RunStatus.SUCCEEDED:
                    self._completed[(claim.source_id, claim.window_id)] = run
            persisted = self._completed.get((claim.source_id, claim.window_id))
            success = persisted if persisted is not None and persisted.status is RunStatus.SUCCEEDED else None
            status = RunStatus.SUCCEEDED if success is not None else RunStatus.FAILED
            terminal_time = success.finished_at if success is not None else finished_at
            assert terminal_time is not None
            self._terminal[claim.claim_id] = (
                claim.source_id, claim.window_id, status,
                success.id if success is not None else run.id if run is not None else None)
            self._advance(row, success, terminal_time, policy)
            return status

    def record_summary(self, summary: WorkerSummary) -> None:
        with self._lock:
            self.summaries.append(summary)
