"""One-shot, bounded scheduling around the existing DiscoveryRunner."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol
from uuid import UUID, uuid4

from agent_collect.discovery.models import DiscoveryQuery, QueryStatus
from agent_collect.discovery.runner import DiscoveryRunner
from agent_collect.schemas.models import Vertical


@dataclass(frozen=True, slots=True)
class DiscoverySchedule:
    id: UUID
    query_text: str
    vertical: Vertical
    language: str
    country: str
    interval_minutes: int
    next_run_at: datetime
    enabled: bool = False
    consecutive_failures: int = 0

    def __post_init__(self) -> None:
        if self.vertical is not Vertical.CONFERENCE or not self.query_text.strip():
            raise ValueError("only named Conference queries are schedulable")
        if self.interval_minutes < 1440 or self.next_run_at.tzinfo is None:
            raise ValueError("interval must respect the 24h cooldown and time must be aware")


@dataclass(frozen=True, slots=True)
class DiscoveryClaim:
    schedule: DiscoverySchedule
    claim_id: UUID
    window_id: UUID
    worker_id: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class DiscoveryWorkerSummary:
    worker_id: str
    started_at: datetime
    finished_at: datetime
    dry_run: bool
    due_count: int
    claimed_count: int
    success_count: int
    failure_count: int
    budget_blocked_count: int
    skipped_count: int
    previews: tuple[dict[str, Any], ...]


class DiscoveryScheduleRepository(Protocol):
    def list_due(self, limit: int) -> list[DiscoverySchedule]: ...
    def claim(self, schedule: DiscoverySchedule, *, worker_id: str,
              claim_id: UUID, lease_seconds: int) -> DiscoveryClaim | None: ...
    def complete(self, claim: DiscoveryClaim, *, outcome: str,
                 query_id: UUID | None, failure_backoff_minutes: tuple[int, ...],
                 budget_delay_minutes: int) -> str: ...
    def record_summary(self, summary: DiscoveryWorkerSummary) -> None: ...


class DiscoveryWorker:
    def __init__(self, *, schedules: DiscoveryScheduleRepository,
                 runner: DiscoveryRunner, worker_id: str, max_jobs: int = 1,
                 lease_seconds: int = 120, failure_backoff_minutes: tuple[int, ...] = (60, 360, 1440),
                 budget_delay_minutes: int = 1440,
                 clock: Callable[[], datetime] | None = None) -> None:
        if not worker_id.strip() or not 1 <= max_jobs <= 100 or not 1 <= lease_seconds <= 86400:
            raise ValueError("invalid Discovery worker limit or lease")
        if not failure_backoff_minutes or min(*failure_backoff_minutes, budget_delay_minutes) < 1:
            raise ValueError("invalid Discovery backoff")
        if runner.provider.name != "tavily" or runner.budget_guard is None:
            raise ValueError("scheduled Discovery requires guarded Tavily Runner")
        self.schedules, self.runner, self.worker_id = schedules, runner, worker_id
        self.max_jobs, self.lease_seconds = max_jobs, lease_seconds
        self.failure_backoff_minutes = failure_backoff_minutes
        self.budget_delay_minutes = budget_delay_minutes
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _query(self, schedule: DiscoverySchedule, *, claim: DiscoveryClaim | None = None) -> DiscoveryQuery:
        provenance: dict[str, Any] = {"scheduled_discovery": True,
                                      "schedule_id": str(schedule.id)}
        if claim is not None:
            provenance.update(window_id=str(claim.window_id), claim_id=str(claim.claim_id),
                              worker_id=claim.worker_id)
        return DiscoveryQuery(id=uuid4(), text=schedule.query_text, provider="tavily",
                              vertical_hint=Vertical.CONFERENCE, created_at=self.clock(),
                              provenance=provenance,
                              schedule_id=schedule.id if claim else None,
                              schedule_window_id=claim.window_id if claim else None,
                              schedule_claim_id=claim.claim_id if claim else None,
                              schedule_worker_id=claim.worker_id if claim else None)

    def run_once(self, *, dry_run: bool = False) -> DiscoveryWorkerSummary:
        started = self.clock()
        due = self.schedules.list_due(self.max_jobs)
        claimed = success = failure = budget_blocked = skipped = 0
        previews: list[dict[str, Any]] = []
        for schedule in due:
            if not schedule.enabled or schedule.vertical is not Vertical.CONFERENCE:
                skipped += 1
                continue
            if dry_run:
                decision = self.runner.budget_guard.check(self._query(schedule), now=self.clock())
                previews.append({"schedule_id": str(schedule.id), "query": schedule.query_text,
                                 "interval_minutes": schedule.interval_minutes,
                                 "next_run_at": schedule.next_run_at.isoformat(),
                                 "budget_allowed": decision.allowed,
                                 "budget_reason": decision.reason.value if decision.reason else None,
                                 "daily_usage": decision.daily_usage,
                                 "monthly_usage": decision.monthly_usage})
                continue
            claim = self.schedules.claim(schedule, worker_id=self.worker_id,
                                         claim_id=uuid4(), lease_seconds=self.lease_seconds)
            if claim is None:
                skipped += 1
                continue
            claimed += 1
            outcome = "FAILED"
            query_id = None
            try:
                result = self.runner.run(self._query(claim.schedule, claim=claim))
                query_id = result.query.id
                if result.query.status is QueryStatus.SUCCEEDED:
                    outcome = "SUCCEEDED"
                elif result.query.provenance.get("budget_blocked"):
                    outcome = "BUDGET_BLOCKED"
            except Exception:
                # The DB reconciles a query that committed SUCCEEDED before a
                # late application exception; memory is not the authority.
                pass
            terminal = self.schedules.complete(
                claim, outcome=outcome, query_id=query_id,
                failure_backoff_minutes=self.failure_backoff_minutes,
                budget_delay_minutes=self.budget_delay_minutes)
            if terminal == "SUCCEEDED":
                success += 1
            elif terminal == "BUDGET_BLOCKED":
                budget_blocked += 1
            else:
                failure += 1
        summary = DiscoveryWorkerSummary(
            self.worker_id, started, self.clock(), dry_run, len(due), claimed,
            success, failure, budget_blocked, skipped, tuple(previews))
        if not dry_run:
            self.schedules.record_summary(summary)
        return summary
