"""Persistent, fail-closed search budget policy using Agent Discovery query rows."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from zoneinfo import ZoneInfo

from agent_collect.discovery.models import DiscoveryQuery
from agent_collect.repositories.discovery import DiscoveryRepository


class BudgetLevel(StrEnum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    HARD_STOP = "HARD_STOP"


class BudgetBlockReason(StrEnum):
    DAILY_LIMIT_REACHED = "DAILY_LIMIT_REACHED"
    MONTHLY_LIMIT_REACHED = "MONTHLY_LIMIT_REACHED"
    QUERY_COOLDOWN = "QUERY_COOLDOWN"
    PROVIDER_DISABLED = "PROVIDER_DISABLED"
    MISSING_CREDENTIAL = "MISSING_CREDENTIAL"


@dataclass(frozen=True, slots=True)
class SearchBudgetPolicy:
    daily_limit: int = 30
    monthly_limit: int = 1000
    cooldown_hours: int = 24
    timezone_name: str = "Asia/Seoul"
    warning_threshold: int = 800
    critical_threshold: int = 950

    def __post_init__(self) -> None:
        if min(self.daily_limit, self.monthly_limit, self.cooldown_hours) <= 0:
            raise ValueError("search limits and cooldown must be positive")
        if not 0 <= self.warning_threshold <= self.critical_threshold <= self.monthly_limit:
            raise ValueError("search warning thresholds must be ordered within monthly limit")
        ZoneInfo(self.timezone_name)


@dataclass(frozen=True, slots=True)
class SearchBudgetDecision:
    allowed: bool
    reason: BudgetBlockReason | None
    daily_usage: int
    monthly_usage: int
    level: BudgetLevel


def normalize_query(text: str) -> str:
    return " ".join(text.casefold().split())


class SearchBudgetGuard:
    def __init__(self, repository: DiscoveryRepository, *, policy: SearchBudgetPolicy,
                 provider_enabled: bool, credential_present: bool) -> None:
        self.repository = repository
        self.policy = policy
        self.provider_enabled = provider_enabled
        self.credential_present = credential_present

    def check(self, query: DiscoveryQuery, *, now: datetime | None = None) -> SearchBudgetDecision:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        local_now = now.astimezone(ZoneInfo(self.policy.timezone_name))
        day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = day_start.replace(day=1)
        cooldown_start = now - timedelta(hours=self.policy.cooldown_hours)
        since = min(month_start.astimezone(timezone.utc), cooldown_start.astimezone(timezone.utc))
        # A failed read is deliberately not caught: no provider call is safe when
        # the persistent usage ledger cannot be consulted.
        rows = self.repository.list_queries_since(query.provider, since)
        attempted = [row for row in rows if row.started_at is not None]
        daily = sum(row.started_at.astimezone(ZoneInfo(self.policy.timezone_name)) >= day_start
                    for row in attempted)
        monthly = sum(row.started_at.astimezone(ZoneInfo(self.policy.timezone_name)) >= month_start
                      for row in attempted)
        if monthly >= self.policy.monthly_limit:
            level = BudgetLevel.HARD_STOP
        elif monthly >= self.policy.critical_threshold:
            level = BudgetLevel.CRITICAL
        elif monthly >= self.policy.warning_threshold:
            level = BudgetLevel.WARNING
        else:
            level = BudgetLevel.NORMAL
        reason = None
        if not self.provider_enabled:
            reason = BudgetBlockReason.PROVIDER_DISABLED
        elif not self.credential_present:
            reason = BudgetBlockReason.MISSING_CREDENTIAL
        elif daily >= self.policy.daily_limit:
            reason = BudgetBlockReason.DAILY_LIMIT_REACHED
        elif monthly >= self.policy.monthly_limit:
            reason = BudgetBlockReason.MONTHLY_LIMIT_REACHED
        elif any(row.started_at >= cooldown_start and
                 normalize_query(row.text) == normalize_query(query.text)
                 for row in attempted):
            reason = BudgetBlockReason.QUERY_COOLDOWN
        return SearchBudgetDecision(reason is None, reason, daily, monthly, level)
