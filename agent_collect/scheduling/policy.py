"""Deterministic fixed-interval and failure-backoff policy."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from agent_collect.schemas.validation import require_aware


@dataclass(frozen=True, slots=True)
class CollectionSchedulePolicy:
    default_interval_minutes: int = 720
    lease_seconds: int = 1800
    max_jobs_per_run: int = 5
    max_concurrent_jobs: int = 1
    max_concurrent_per_domain: int = 1
    failure_backoff_minutes: tuple[int, ...] = (30, 120, 360, 1440)
    failure_review_threshold: int = 4

    def __post_init__(self) -> None:
        if min(self.default_interval_minutes, self.lease_seconds,
               self.max_jobs_per_run, self.max_concurrent_jobs,
               self.max_concurrent_per_domain, self.failure_review_threshold) <= 0:
            raise ValueError("scheduling limits must be positive")
        if not self.failure_backoff_minutes or any(item <= 0 for item in self.failure_backoff_minutes):
            raise ValueError("failure backoff must contain positive minutes")
        if self.max_concurrent_jobs > self.max_jobs_per_run:
            raise ValueError("concurrent jobs cannot exceed jobs per run")
        if self.max_jobs_per_run > 100 or self.lease_seconds > 86400 or self.max_concurrent_per_domain > 16:
            raise ValueError("scheduling limits exceed DB RPC bounds")

    def after_success(self, finished_at: datetime, interval_minutes: int) -> datetime:
        require_aware(finished_at, "finished_at")
        if interval_minutes <= 0:
            raise ValueError("collection interval must be positive")
        return finished_at + timedelta(minutes=interval_minutes)

    def after_failure(self, finished_at: datetime, consecutive_failures: int) -> datetime:
        require_aware(finished_at, "finished_at")
        if consecutive_failures <= 0:
            raise ValueError("failure count must be positive")
        index = min(consecutive_failures - 1, len(self.failure_backoff_minutes) - 1)
        return finished_at + timedelta(minutes=self.failure_backoff_minutes[index])

    def review_needed(self, consecutive_failures: int) -> bool:
        return consecutive_failures >= self.failure_review_threshold
