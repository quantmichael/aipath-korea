"""Non-secret app policy in TOML; deployment overrides via environment."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo

from agent_collect.schemas.validation import require_confidence, require_text


@dataclass(frozen=True, slots=True)
class AgentSettings:
    verification_threshold: float
    publish_threshold: float
    max_retries: int
    collection_timeout_seconds: int
    max_response_bytes: int
    max_redirects: int
    retry_backoff_seconds: float
    raw_retention_days: int
    user_agent: str
    llm_model: str
    discovery_enabled: bool
    llm_enabled: bool
    publish_enabled: bool
    discovery_promotion_threshold: float = 0.90
    discovery_auto_approval_enabled: bool = False
    ai_discovery_planner_enabled: bool = False
    ai_discovery_execution_enabled: bool = False
    discovery_auto_approval_scopes_json: str = "[]"
    discovery_search_provider: str = ""
    discovery_official_domain_hints: tuple[str, ...] = ()
    discovery_max_results: int = 5
    discovery_request_timeout_seconds: int = 10
    discovery_daily_search_limit: int = 30
    discovery_monthly_search_limit: int = 1000
    discovery_query_cooldown_hours: int = 24
    discovery_budget_timezone: str = "Asia/Seoul"
    discovery_monthly_warning_threshold: int = 800
    discovery_monthly_critical_threshold: int = 950
    discovery_default_interval_minutes: int = 1440
    discovery_max_jobs_per_run: int = 1
    discovery_lease_seconds: int = 120
    discovery_failure_backoff_minutes: tuple[int, ...] = (60, 360, 1440)
    discovery_budget_delay_minutes: int = 1440
    extraction_daily_llm_limit: int = 5
    extraction_monthly_llm_limit: int = 100
    extraction_max_retries: int = 1
    extraction_max_input_chars: int = 12000
    extraction_max_evidence_chars: int = 240
    extraction_max_output_tokens: int = 1600
    extraction_timeout_seconds: int = 30
    extraction_budget_timezone: str = "Asia/Seoul"
    extraction_model: str = "gpt-5.4-nano"
    dedup_similarity_threshold: float = 0.88
    collection_default_interval_minutes: int = 720
    collection_lease_seconds: int = 1800
    collection_max_jobs_per_run: int = 5
    collection_max_concurrent_jobs: int = 1
    collection_max_concurrent_per_domain: int = 1
    collection_failure_backoff_minutes: tuple[int, ...] = (30, 120, 360, 1440)
    collection_failure_review_threshold: int = 4
    raw_storage_provider: str = "local"
    raw_storage_bucket: str = "agent-raw"
    processing_version: str = "conference-processing-v1"
    processing_max_jobs_per_run: int = 1
    processing_lease_seconds: int = 300
    processing_retry_backoff_minutes: tuple[int, ...] = (15, 60, 360)
    processing_max_attempts: int = 4
    app_env: str = "production"
    automation_enabled: bool = False
    discovery_worker_enabled: bool = False
    collection_worker_enabled: bool = False
    processing_worker_enabled: bool = False
    publish_worker_enabled: bool = False
    publish_max_jobs_per_run: int = 1
    publish_lease_seconds: int = 300
    automation_lease_seconds: int = 3600

    def __post_init__(self) -> None:
        if self.app_env not in {"development", "production"}:
            raise ValueError("invalid application environment")
        require_confidence(self.verification_threshold, "verification_threshold")
        require_confidence(self.publish_threshold, "publish_threshold")
        require_confidence(self.dedup_similarity_threshold, "dedup_similarity_threshold")
        require_confidence(self.discovery_promotion_threshold, "discovery_promotion_threshold")
        if self.discovery_auto_approval_enabled and self.discovery_auto_approval_scopes_json.strip() == "[]":
            raise ValueError("auto approval requires reviewed scopes")
        if self.ai_discovery_execution_enabled and not self.ai_discovery_planner_enabled:
            raise ValueError("AI Discovery execution requires the planner")
        if not 1 <= self.discovery_max_results <= 20 or self.discovery_request_timeout_seconds <= 0:
            raise ValueError("discovery result limit/timeout settings are invalid")
        if min(self.discovery_daily_search_limit, self.discovery_monthly_search_limit,
               self.discovery_query_cooldown_hours) <= 0:
            raise ValueError("discovery budget limits/cooldown must be positive")
        if not 0 <= self.discovery_monthly_warning_threshold <= self.discovery_monthly_critical_threshold <= self.discovery_monthly_search_limit:
            raise ValueError("discovery budget warning thresholds are invalid")
        ZoneInfo(self.discovery_budget_timezone)
        if (self.discovery_default_interval_minutes < self.discovery_query_cooldown_hours * 60 or
                self.discovery_max_jobs_per_run not in range(1, 101) or
                not 1 <= self.discovery_lease_seconds <= 86400 or
                not self.discovery_failure_backoff_minutes or
                any(value < 1 for value in self.discovery_failure_backoff_minutes) or
                self.discovery_budget_delay_minutes < 1):
            raise ValueError("invalid Discovery scheduling policy")
        if self.discovery_lease_seconds <= self.discovery_request_timeout_seconds:
            raise ValueError("Discovery lease must exceed provider timeout")
        if min(self.extraction_daily_llm_limit, self.extraction_monthly_llm_limit,
               self.extraction_max_input_chars, self.extraction_max_evidence_chars,
               self.extraction_max_output_tokens, self.extraction_timeout_seconds) <= 0 or self.extraction_max_retries < 0:
            raise ValueError("extraction budget/size settings are invalid")
        ZoneInfo(self.extraction_budget_timezone)
        require_text(self.extraction_model, "extraction_model")
        if self.publish_threshold < self.verification_threshold:
            raise ValueError("publish_threshold must be >= verification_threshold")
        if self.max_retries < 0 or self.collection_timeout_seconds <= 0:
            raise ValueError("retry/timeout settings are invalid")
        if self.max_response_bytes <= 0 or self.max_redirects < 0:
            raise ValueError("response/redirect limits are invalid")
        if self.raw_storage_provider not in {"local", "supabase"}:
            raise ValueError("invalid raw storage provider")
        require_text(self.raw_storage_bucket, "raw_storage_bucket")
        require_text(self.processing_version, "processing_version")
        if (self.processing_max_jobs_per_run not in range(1, 101) or
                self.processing_lease_seconds not in range(1, 86401) or
                self.processing_max_attempts < 1 or
                not self.processing_retry_backoff_minutes or
                any(value < 1 for value in self.processing_retry_backoff_minutes)):
            raise ValueError("invalid processing policy")
        if (self.publish_max_jobs_per_run not in range(1, 101) or
                not 1 <= self.publish_lease_seconds <= 86400 or
                not 1 <= self.automation_lease_seconds <= 86400):
            raise ValueError("invalid publish/automation lease policy")
        if self.retry_backoff_seconds < 0 or self.raw_retention_days <= 0:
            raise ValueError("retry backoff/retention settings are invalid")
        require_text(self.user_agent, "user_agent")
        if self.llm_enabled:
            require_text(self.llm_model, "llm_model")
        from agent_collect.scheduling.policy import CollectionSchedulePolicy
        CollectionSchedulePolicy(
            self.collection_default_interval_minutes, self.collection_lease_seconds,
            self.collection_max_jobs_per_run, self.collection_max_concurrent_jobs,
            self.collection_max_concurrent_per_domain,
            self.collection_failure_backoff_minutes,
            self.collection_failure_review_threshold,
        )
        # No lease heartbeat exists in this foundation. Keep the lease above
        # the configured HTTP timeout envelope (retries and redirects).
        if self.collection_lease_seconds <= (
            self.collection_timeout_seconds * (self.max_retries + 1) * (self.max_redirects + 1)
        ):
            raise ValueError("collection lease is shorter than the HTTP timeout envelope")
        if self.processing_lease_seconds <= self.extraction_timeout_seconds * (self.extraction_max_retries + 1):
            raise ValueError("processing lease is shorter than the extraction timeout envelope")


def _boolean(value: str, name: str) -> bool:
    parsed = value.lower().strip()
    if parsed in {"true", "1", "yes"}:
        return True
    if parsed in {"false", "0", "no"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def load_settings(
    *,
    config_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> AgentSettings:
    path = config_path or Path(__file__).with_name("defaults.toml")
    with path.open("rb") as handle:
        config = tomllib.load(handle)
    env = os.environ if environ is None else environ
    policy = config["policy"]
    flags = config["features"]
    app_env = "development" if env.get("APP_ENV", "").strip().lower() == "development" else "production"
    budget_profile = config["extraction_budget_profiles"][app_env]

    def value(name: str, default: object) -> object:
        return env.get(f"AGENT_{name.upper()}", default)

    def flag(name: str) -> bool:
        raw = value(name, flags[name])
        return raw if isinstance(raw, bool) else _boolean(str(raw), name)

    return AgentSettings(
        verification_threshold=float(value("verification_threshold", policy["verification_threshold"])),
        publish_threshold=float(value("publish_threshold", policy["publish_threshold"])),
        max_retries=int(value("max_retries", policy["max_retries"])),
        collection_timeout_seconds=int(value("collection_timeout_seconds", policy["collection_timeout_seconds"])),
        max_response_bytes=int(value("max_response_bytes", policy["max_response_bytes"])),
        max_redirects=int(value("max_redirects", policy["max_redirects"])),
        retry_backoff_seconds=float(value("retry_backoff_seconds", policy["retry_backoff_seconds"])),
        raw_retention_days=int(value("raw_retention_days", policy["raw_retention_days"])),
        user_agent=str(value("user_agent", policy["user_agent"])),
        llm_model=str(value("llm_model", policy["llm_model"])),
        discovery_enabled=flag("discovery_enabled"),
        llm_enabled=flag("llm_enabled"),
        publish_enabled=flag("publish_enabled"),
        discovery_promotion_threshold=float(value(
            "discovery_promotion_threshold", policy["discovery_promotion_threshold"])),
        discovery_auto_approval_enabled=flag("discovery_auto_approval_enabled"),
        ai_discovery_planner_enabled=flag("ai_discovery_planner_enabled"),
        ai_discovery_execution_enabled=flag("ai_discovery_execution_enabled"),
        discovery_auto_approval_scopes_json=str(value(
            "discovery_auto_approval_scopes_json", policy["discovery_auto_approval_scopes_json"])),
        discovery_search_provider=str(value(
            "discovery_search_provider", policy["discovery_search_provider"])),
        discovery_official_domain_hints=tuple(
            part.strip().lower() for part in str(value(
                "discovery_official_domain_hints",
                ",".join(policy["discovery_official_domain_hints"])) ).split(",") if part.strip()
        ),
        discovery_max_results=int(value("discovery_max_results", policy["discovery_max_results"])),
        discovery_request_timeout_seconds=int(value(
            "discovery_request_timeout_seconds", policy["discovery_request_timeout_seconds"])),
        discovery_daily_search_limit=int(value("discovery_daily_search_limit", policy["discovery_daily_search_limit"])),
        discovery_monthly_search_limit=int(value("discovery_monthly_search_limit", policy["discovery_monthly_search_limit"])),
        discovery_query_cooldown_hours=int(value("discovery_query_cooldown_hours", policy["discovery_query_cooldown_hours"])),
        discovery_budget_timezone=str(value("discovery_budget_timezone", policy["discovery_budget_timezone"])),
        discovery_monthly_warning_threshold=int(value("discovery_monthly_warning_threshold", policy["discovery_monthly_warning_threshold"])),
        discovery_monthly_critical_threshold=int(value("discovery_monthly_critical_threshold", policy["discovery_monthly_critical_threshold"])),
        discovery_default_interval_minutes=int(value("discovery_default_interval_minutes", policy["discovery_default_interval_minutes"])),
        discovery_max_jobs_per_run=int(value("discovery_max_jobs_per_run", policy["discovery_max_jobs_per_run"])),
        discovery_lease_seconds=int(value("discovery_lease_seconds", policy["discovery_lease_seconds"])),
        discovery_failure_backoff_minutes=tuple(int(part.strip()) for part in str(value(
            "discovery_failure_backoff_minutes", ",".join(str(x) for x in policy["discovery_failure_backoff_minutes"])
        )).split(",") if part.strip()),
        discovery_budget_delay_minutes=int(value("discovery_budget_delay_minutes", policy["discovery_budget_delay_minutes"])),
        extraction_daily_llm_limit=int(value("extraction_daily_llm_limit", budget_profile["daily_llm_limit"])),
        extraction_monthly_llm_limit=int(value("extraction_monthly_llm_limit", budget_profile["monthly_llm_limit"])),
        extraction_max_retries=int(value("extraction_max_retries", policy["extraction_max_retries"])),
        extraction_max_input_chars=int(value("extraction_max_input_chars", policy["extraction_max_input_chars"])),
        extraction_max_evidence_chars=int(value("extraction_max_evidence_chars", policy["extraction_max_evidence_chars"])),
        extraction_max_output_tokens=int(value("extraction_max_output_tokens", policy["extraction_max_output_tokens"])),
        extraction_timeout_seconds=int(value("extraction_timeout_seconds", policy["extraction_timeout_seconds"])),
        extraction_budget_timezone=str(value("extraction_budget_timezone", policy["extraction_budget_timezone"])),
        extraction_model=str(value("extraction_model", policy["extraction_model"])),
        dedup_similarity_threshold=float(value("dedup_similarity_threshold", policy["dedup_similarity_threshold"])),
        collection_default_interval_minutes=int(value(
            "collection_default_interval_minutes", policy["collection_default_interval_minutes"])),
        collection_lease_seconds=int(value("collection_lease_seconds", policy["collection_lease_seconds"])),
        collection_max_jobs_per_run=int(value("collection_max_jobs_per_run", policy["collection_max_jobs_per_run"])),
        collection_max_concurrent_jobs=int(value(
            "collection_max_concurrent_jobs", policy["collection_max_concurrent_jobs"])),
        collection_max_concurrent_per_domain=int(value(
            "collection_max_concurrent_per_domain", policy["collection_max_concurrent_per_domain"])),
        collection_failure_backoff_minutes=tuple(int(part.strip()) for part in str(value(
            "collection_failure_backoff_minutes", ",".join(str(x) for x in policy["collection_failure_backoff_minutes"])
        )).split(",") if part.strip()),
        collection_failure_review_threshold=int(value(
            "collection_failure_review_threshold", policy["collection_failure_review_threshold"])),
        raw_storage_provider=str(value("raw_storage_provider", policy["raw_storage_provider"])),
        raw_storage_bucket=str(value("raw_storage_bucket", policy["raw_storage_bucket"])),
        processing_version=str(value("processing_version", policy["processing_version"])),
        processing_max_jobs_per_run=int(value("processing_max_jobs_per_run", policy["processing_max_jobs_per_run"])),
        processing_lease_seconds=int(value("processing_lease_seconds", policy["processing_lease_seconds"])),
        processing_retry_backoff_minutes=tuple(int(part.strip()) for part in str(value(
            "processing_retry_backoff_minutes", ",".join(str(x) for x in policy["processing_retry_backoff_minutes"])
        )).split(",") if part.strip()),
        processing_max_attempts=int(value("processing_max_attempts", policy["processing_max_attempts"])),
        publish_max_jobs_per_run=int(value("publish_max_jobs_per_run", policy["publish_max_jobs_per_run"])),
        publish_lease_seconds=int(value("publish_lease_seconds", policy["publish_lease_seconds"])),
        automation_lease_seconds=int(value("automation_lease_seconds", policy["automation_lease_seconds"])),
        automation_enabled=flag("automation_enabled"),
        discovery_worker_enabled=flag("discovery_worker_enabled"),
        collection_worker_enabled=flag("collection_worker_enabled"),
        processing_worker_enabled=flag("processing_worker_enabled"),
        publish_worker_enabled=flag("publish_worker_enabled"),
        app_env=app_env,
    )
