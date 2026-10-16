"""Fail-closed LLM extraction guard backed by Agent candidate/log records."""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo

from agent_collect.extraction.schema import StoredExtraction
from agent_collect.repositories.extraction import ExtractionRepository


class ExtractionBlockReason(StrEnum):
    DUPLICATE_RAW = "DUPLICATE_RAW_DOCUMENT"
    DUPLICATE_CONTENT = "DUPLICATE_CONTENT_VERSION"
    DAILY_LIMIT = "DAILY_LLM_LIMIT_REACHED"
    MONTHLY_LIMIT = "MONTHLY_LLM_LIMIT_REACHED"
    PROVIDER_DISABLED = "LLM_PROVIDER_DISABLED"
    MISSING_CREDENTIAL = "MISSING_LLM_CREDENTIAL"
    INPUT_TOO_LARGE = "INPUT_TOO_LARGE"
    RETRY_LIMIT = "RETRY_LIMIT_REACHED"


@dataclass(frozen=True, slots=True)
class ExtractionBudgetPolicy:
    daily_limit: int = 5
    monthly_limit: int = 100
    max_retries: int = 1
    max_input_chars: int = 12000
    timezone_name: str = "Asia/Seoul"

    def __post_init__(self) -> None:
        if min(self.daily_limit, self.monthly_limit, self.max_input_chars) <= 0 or self.max_retries < 0:
            raise ValueError("invalid extraction budget")
        ZoneInfo(self.timezone_name)


@dataclass(frozen=True, slots=True)
class ExtractionBudgetDecision:
    allowed: bool
    reason: ExtractionBlockReason | None
    daily_usage: int
    monthly_usage: int
    cached: StoredExtraction | None = None
    previous_attempts: int = 0


class ExtractionBudgetGuard:
    def __init__(self, repository: ExtractionRepository, *, policy: ExtractionBudgetPolicy,
                 provider_enabled: bool, credential_present: bool) -> None:
        self.repository = repository
        self.policy = policy
        self.provider_enabled = provider_enabled
        self.credential_present = credential_present

    def check(self, *, raw_document_id: UUID, fingerprint: str, input_chars: int,
              retry_count: int = 0, now: datetime | None = None) -> ExtractionBudgetDecision:
        now = now or datetime.now(timezone.utc)
        zone = ZoneInfo(self.policy.timezone_name)
        local = now.astimezone(zone)
        day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = day_start.replace(day=1)
        # DB read failure propagates, preventing the provider call.
        day = self.repository.count_attempts_since(day_start.astimezone(timezone.utc))
        month = self.repository.count_attempts_since(month_start.astimezone(timezone.utc))
        previous_attempts = self.repository.count_attempts_for_fingerprint(fingerprint)
        cached = self.repository.find_by_raw_fingerprint(raw_document_id, fingerprint)
        if cached is not None:
            reason = ExtractionBlockReason.DUPLICATE_RAW
        else:
            cached = self.repository.find_by_fingerprint(fingerprint)
            if cached is not None:
                reason = ExtractionBlockReason.DUPLICATE_CONTENT
            elif not self.provider_enabled:
                reason = ExtractionBlockReason.PROVIDER_DISABLED
            elif not self.credential_present:
                reason = ExtractionBlockReason.MISSING_CREDENTIAL
            elif input_chars > self.policy.max_input_chars:
                reason = ExtractionBlockReason.INPUT_TOO_LARGE
            elif max(retry_count, previous_attempts) > self.policy.max_retries:
                reason = ExtractionBlockReason.RETRY_LIMIT
            elif day >= self.policy.daily_limit:
                reason = ExtractionBlockReason.DAILY_LIMIT
            elif month >= self.policy.monthly_limit:
                reason = ExtractionBlockReason.MONTHLY_LIMIT
            else:
                reason = None
        return ExtractionBudgetDecision(reason is None, reason, day, month, cached,
                                        previous_attempts)
