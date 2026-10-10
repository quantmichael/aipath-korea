"""Bounded, one-shot known-Source collection worker; never a daemon."""

from datetime import datetime, timezone
from typing import Callable, Protocol
from uuid import UUID, uuid4

from agent_collect.collectors.runner import RunOutcome
from agent_collect.schemas.validation import require_aware
from agent_collect.schemas.status import RunStatus
from agent_collect.scheduling.models import DuePreview, WorkerSummary
from agent_collect.scheduling.policy import CollectionSchedulePolicy
from agent_collect.scheduling.repository import CollectionScheduleRepository


class ScheduledCollectionRunner(Protocol):
    def run_source(self, source_id: UUID, *, trigger: str, worker_id: str,
                   claim_id: UUID, schedule_window_id: UUID) -> RunOutcome: ...


class CollectionWorker:
    def __init__(self, *, schedules: CollectionScheduleRepository,
                 runner: ScheduledCollectionRunner, policy: CollectionSchedulePolicy,
                 worker_id: str, clock: Callable[[], datetime] | None = None) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        self.schedules = schedules
        self.runner = runner
        self.policy = policy
        self.worker_id = worker_id
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def run_once(self, *, dry_run: bool = False, now: datetime | None = None) -> WorkerSummary:
        started = now or self.clock()
        require_aware(started, "now")
        due = self.schedules.list_due(self.policy.max_jobs_per_run)
        previews = tuple(DuePreview(item.source.id, item.source.name,
                                    item.source.collection_method, item.interval_minutes,
                                    item.next_collection_at) for item in due)
        if dry_run:
            return WorkerSummary(self.worker_id, started, max(started, self.clock()), True,
                                 len(due), 0, 0, 0, 0, previews)
        claimed = success = failure = skipped = 0
        # Deliberately sequential. max_concurrent_jobs is an upper bound; this
        # foundation does not launch a thread pool or a long-running process.
        for item in due:
            claim = self.schedules.claim(
                item.source.id, worker_id=self.worker_id, claim_id=uuid4(),
                policy=self.policy,
            )
            if claim is None:
                skipped += 1
                continue
            claimed += 1
            try:
                outcome = self.runner.run_source(
                    claim.source_id, trigger="scheduled", worker_id=self.worker_id,
                    claim_id=claim.claim_id, schedule_window_id=claim.window_id,
                )
            except Exception:
                try:
                    terminal = self.schedules.complete(claim, run=None, policy=self.policy)
                except Exception:
                    # DB state remains authoritative; lease expiry reconciles
                    # an already-persisted success without another HTTP call.
                    failure += 1
                else:
                    if terminal is RunStatus.SUCCEEDED:
                        success += 1
                    else:
                        failure += 1
                continue
            try:
                terminal = self.schedules.complete(
                    claim, run=outcome.run, policy=self.policy,
                )
            except ValueError:
                # A locally rejected Run/claim mismatch is not an ambiguous
                # response; leave the lease for authoritative DB recovery.
                failure += 1
                continue
            except Exception:
                # The RPC may have committed before its response failed. Retry
                # without a Run ID: the DB completion ledger/successful Run is
                # the authority, not this exception or an in-memory outcome.
                try:
                    terminal = self.schedules.complete(claim, run=None, policy=self.policy)
                except Exception:
                    # Otherwise lease expiry reconciles the persisted Run.
                    failure += 1
                    continue
                if terminal is RunStatus.SUCCEEDED:
                    success += 1
                else:
                    failure += 1
                continue
            if terminal is RunStatus.SUCCEEDED:
                success += 1
            else:
                failure += 1
        summary = WorkerSummary(self.worker_id, started, max(started, self.clock()), False,
                                len(due), claimed, success, failure, skipped, previews)
        self.schedules.record_summary(summary)
        return summary
