"""Runtime assembly for Agent-owned repositories; no Backend imports."""

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from agent_collect.collectors.planner import CollectionPlanner
from agent_collect.adapters.service_db.duplicate_lookup import SupabaseServiceDuplicateLookup
from agent_collect.adapters.service_db.adapter import SupabaseServiceDBAdapter
from agent_collect.adapters.service_db.supabase_tables import SupabaseServiceTables
from agent_collect.collectors.runner import CollectionRunner
from agent_collect.config.settings import AgentSettings, load_settings
from agent_collect.discovery.evaluation import RuleBasedEvaluator
from agent_collect.discovery.budget import SearchBudgetGuard, SearchBudgetPolicy
from agent_collect.discovery.providers import SearchProvider, SearchRequestPolicy
from agent_collect.discovery.tavily import TavilySearchProvider
from agent_collect.discovery.scheduling import DiscoveryWorker
from agent_collect.discovery.scheduling_supabase import SupabaseDiscoverySchedules
from agent_collect.discovery.models import DiscoveryQuery
from agent_collect.schemas.models import Vertical
from agent_collect.discovery.runner import DiscoveryRunner, PromotionGate
from agent_collect.extraction.providers import ExtractionProvider
from agent_collect.extraction.providers import OpenAIExtractionProvider
from agent_collect.extraction.runner import ExtractionRunner
from agent_collect.normalization.runner import NormalizationRunner
from agent_collect.quality_gate.runner import QualityGateRunner
from agent_collect.registry.service import SourceRegistryService
from agent_collect.registry.approval import SourceApprovalService
from agent_collect.repositories.discovery import DiscoveryRepository
from agent_collect.repositories.discovery_supabase import SupabaseDiscoveryRepository
from agent_collect.repositories.extraction import ExtractionRepository
from agent_collect.repositories.extraction_supabase import SupabaseExtractionRepository
from agent_collect.repositories.normalization_supabase import SupabaseNormalizationRepository
from agent_collect.repositories.source_review_supabase import SupabaseSourceReviewRepository
from agent_collect.repositories.quality_supabase import SupabaseQualityRepository
from agent_collect.repositories.publish_ledger import SupabasePublishLedger
from agent_collect.repositories.supabase import (
    SupabaseCollectionRunRepository,
    SupabaseRawDocumentRepository,
    SupabaseSourceRepository,
)
from agent_collect.storage.raw import LocalRawContentStorage
from agent_collect.storage.supabase import SupabaseRawContentStorage
from agent_collect.scheduling.policy import CollectionSchedulePolicy
from agent_collect.scheduling.supabase import SupabaseCollectionScheduleRepository
from agent_collect.scheduling.worker import CollectionWorker
from agent_collect.processing.models import ProcessingPolicy
from agent_collect.processing.supabase import SupabaseProcessingRepository
from agent_collect.processing.worker import ProcessingWorker
from agent_collect.publishing.worker import PublishWorker
from agent_collect.publishing.supabase import SupabasePublishJobs, SupabaseAutomationLease
from agent_collect.automation.runtime import AutomationRuntime


@dataclass(frozen=True, slots=True)
class SupabaseConnectionConfig:
    url: str
    service_key: str


def resolve_supabase_config(environ: Mapping[str, str] | None = None) -> SupabaseConnectionConfig:
    """Prefer Agent credentials; permit existing server credentials during transition."""
    env = os.environ if environ is None else environ
    url = env.get("AGENT_SUPABASE_URL") or env.get("SUPABASE_URL")
    key = env.get("AGENT_SUPABASE_SERVICE_KEY") or env.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        raise RuntimeError(
            "missing AGENT_SUPABASE_URL/SUPABASE_URL or "
            "AGENT_SUPABASE_SERVICE_KEY/SUPABASE_SECRET_KEY"
        )
    return SupabaseConnectionConfig(url=url, service_key=key)


def create_agent_client(environ: Mapping[str, str] | None = None) -> Any:
    config = resolve_supabase_config(environ)
    try:
        from supabase import create_client
    except ImportError as error:
        raise RuntimeError("Supabase client is not installed; install project requirements") from error
    return create_client(config.url, config.service_key)


@dataclass(slots=True)
class AgentRuntime:
    sources: SupabaseSourceRepository
    runs: SupabaseCollectionRunRepository
    documents: SupabaseRawDocumentRepository
    settings: AgentSettings

    @classmethod
    def connect(cls, environ: Mapping[str, str] | None = None) -> "AgentRuntime":
        client = create_agent_client(environ)
        return cls(
            sources=SupabaseSourceRepository(client),
            runs=SupabaseCollectionRunRepository(client),
            documents=SupabaseRawDocumentRepository(client),
            settings=load_settings(environ=environ),
        )

    def collection_runner(self, raw_dir: Path, *, environ: Mapping[str, str] | None = None) -> CollectionRunner:
        return CollectionRunner(
            sources=self.sources,
            runs=self.runs,
            documents=self.documents,
            storage=self.raw_storage(raw_dir),
            planner=CollectionPlanner(),
            settings=self.settings,
            environ=environ,
        )

    def raw_storage(self, raw_dir: Path):
        if self.settings.raw_storage_provider == "supabase":
            client = getattr(self.sources, "client", None)
            if client is None:
                raise RuntimeError("Supabase raw storage requires an Agent client")
            return SupabaseRawContentStorage(
                client, self.settings.raw_storage_bucket,
                retention_days=self.settings.raw_retention_days,
            )
        return LocalRawContentStorage(raw_dir, retention_days=self.settings.raw_retention_days)

    def collection_schedule_policy(self) -> CollectionSchedulePolicy:
        return CollectionSchedulePolicy(
            self.settings.collection_default_interval_minutes,
            self.settings.collection_lease_seconds,
            self.settings.collection_max_jobs_per_run,
            self.settings.collection_max_concurrent_jobs,
            self.settings.collection_max_concurrent_per_domain,
            self.settings.collection_failure_backoff_minutes,
            self.settings.collection_failure_review_threshold,
        )

    def collection_schedule_repository(self) -> SupabaseCollectionScheduleRepository:
        client = getattr(self.sources, "client", None)
        if client is None:
            raise RuntimeError("Collection scheduling requires an Agent Supabase client")
        return SupabaseCollectionScheduleRepository(client, self.sources)

    def collection_worker(self, raw_dir: Path, *, worker_id: str) -> CollectionWorker:
        return CollectionWorker(
            schedules=self.collection_schedule_repository(),
            runner=self.collection_runner(raw_dir),
            policy=self.collection_schedule_policy(), worker_id=worker_id,
        )

    def processing_policy(self) -> ProcessingPolicy:
        return ProcessingPolicy(
            self.settings.processing_version,
            self.settings.processing_max_jobs_per_run,
            self.settings.processing_lease_seconds,
            self.settings.processing_retry_backoff_minutes,
            self.settings.processing_max_attempts,
        )

    def processing_repository(self) -> SupabaseProcessingRepository:
        client = getattr(self.sources, "client", None)
        if client is None:
            raise RuntimeError("processing queue requires an Agent Supabase client")
        return SupabaseProcessingRepository(client)

    def processing_worker(self, raw_dir: Path, *, worker_id: str,
                          provider: ExtractionProvider | None = None) -> ProcessingWorker:
        provider = provider or OpenAIExtractionProvider(
            os.environ.get("OPENAI_API_KEY"), model=self.settings.extraction_model)
        return ProcessingWorker(
            jobs=self.processing_repository(), documents=self.documents,
            storage=self.raw_storage(raw_dir),
            extractor=self.extraction_runner(provider, raw_dir),
            normalizer=self.normalization_runner(),
            quality_gate=self.quality_gate_runner(),
            policy=self.processing_policy(), worker_id=worker_id,
        )

    def discovery_runner(
        self, provider: SearchProvider, *, repository: DiscoveryRepository | None = None,
    ) -> DiscoveryRunner:
        """Assemble manual Discovery; no search is executed by construction."""
        if not self.settings.discovery_enabled and provider.name != "tavily":
            raise RuntimeError("Discovery feature flag is disabled")
        if self.settings.discovery_search_provider and provider.name != self.settings.discovery_search_provider:
            raise ValueError("configured Discovery provider does not match")
        if repository is None:
            client = getattr(self.sources, "client", None)
            if client is None:
                raise RuntimeError("Discovery repository or Supabase client required")
            repository = SupabaseDiscoveryRepository(client)
        registry = SourceRegistryService(self.sources)
        budget_guard = None
        if provider.name == "tavily":
            budget_guard = SearchBudgetGuard(
                repository, policy=SearchBudgetPolicy(
                    daily_limit=self.settings.discovery_daily_search_limit,
                    monthly_limit=self.settings.discovery_monthly_search_limit,
                    cooldown_hours=self.settings.discovery_query_cooldown_hours,
                    timezone_name=self.settings.discovery_budget_timezone,
                    warning_threshold=self.settings.discovery_monthly_warning_threshold,
                    critical_threshold=self.settings.discovery_monthly_critical_threshold,
                ), provider_enabled=self.settings.discovery_enabled,
                credential_present=isinstance(provider, TavilySearchProvider) and provider.credential_present,
            )
        return DiscoveryRunner(
            provider=provider, repository=repository, registry=registry,
            evaluator=RuleBasedEvaluator(
                official_domain_hints=self.settings.discovery_official_domain_hints,
            ),
            gate=PromotionGate(registry, threshold=self.settings.discovery_promotion_threshold),
            policy=SearchRequestPolicy(
                max_results=self.settings.discovery_max_results,
                timeout_seconds=self.settings.discovery_request_timeout_seconds,
            ),
            budget_guard=budget_guard,
        )

    def discovery_worker(self, *, worker_id: str) -> DiscoveryWorker:
        client = getattr(self.sources, "client", None)
        if client is None:
            raise RuntimeError("Discovery scheduling requires an Agent Supabase client")
        provider = TavilySearchProvider(os.environ.get("AGENT_TAVILY_API_KEY"))
        return DiscoveryWorker(
            schedules=SupabaseDiscoverySchedules(client),
            runner=self.discovery_runner(provider), worker_id=worker_id,
            max_jobs=self.settings.discovery_max_jobs_per_run,
            lease_seconds=self.settings.discovery_lease_seconds,
            failure_backoff_minutes=self.settings.discovery_failure_backoff_minutes,
            budget_delay_minutes=self.settings.discovery_budget_delay_minutes,
        )

    def discovery_schedule_repository(self) -> SupabaseDiscoverySchedules:
        client = getattr(self.sources, "client", None)
        if client is None:
            raise RuntimeError("Discovery scheduling requires an Agent Supabase client")
        return SupabaseDiscoverySchedules(client)

    def automation_status_live(self) -> dict[str, Any]:
        """Bounded read-only status; an unavailable migration is reported, not hidden."""
        client = getattr(self.sources, "client", None)
        if client is None:
            raise RuntimeError("Automation status requires an Agent Supabase client")
        status: dict[str, Any] = {}
        checks = {
            "discovery_due": lambda: len(self.discovery_schedule_repository().list_due(100)),
            "collection_due": lambda: len(self.collection_schedule_repository().list_due(100)),
            "processing_due": lambda: len(self.processing_repository().preview(
                self.processing_policy().version,
                f"supabase:{self.settings.raw_storage_bucket}:" if self.settings.raw_storage_provider == "supabase" else "local:",
                100)),
            "publish_due": lambda: len(SupabasePublishJobs(client).preview(100)),
            "automation_lease": lambda: (client.table("agent_runtime_leases")
                                         .select("worker_id,expires_at")
                                         .eq("name", "automation").limit(1).execute().data or []),
            "discovery_last_success": lambda: (client.table("agent_discovery_schedules")
                                               .select("last_success_at")
                                               .not_.is_("last_success_at", "null")
                                               .order("last_success_at", desc=True)
                                               .limit(1).execute().data or []),
            "discovery_last_failure": lambda: (client.table("agent_discovery_schedules")
                                               .select("last_failure_at")
                                               .not_.is_("last_failure_at", "null")
                                               .order("last_failure_at", desc=True)
                                               .limit(1).execute().data or []),
        }
        for name, check in checks.items():
            try:
                status[name] = check()
            except Exception as error:
                status[name] = {"unavailable": type(error).__name__}
        try:
            provider = TavilySearchProvider(os.environ.get("AGENT_TAVILY_API_KEY"))
            decision = self.discovery_runner(provider).budget_guard.check(
                DiscoveryQuery(uuid4(), "__automation_status_probe__", "tavily",
                               datetime.now(timezone.utc), vertical_hint=Vertical.CONFERENCE))
            status["discovery_budget"] = {
                "daily_usage": decision.daily_usage,
                "monthly_usage": decision.monthly_usage,
                "level": decision.level.value,
            }
        except Exception as error:
            status["discovery_budget"] = {"unavailable": type(error).__name__}
        return status

    def extraction_runner(
        self, provider: ExtractionProvider, raw_dir: Path, *,
        repository: ExtractionRepository | None = None,
    ) -> ExtractionRunner:
        """Assemble Conference extraction; construction performs no LLM call."""
        if repository is None:
            client = getattr(self.documents, "client", None)
            if client is None:
                raise RuntimeError("Extraction repository or Supabase client required")
            repository = SupabaseExtractionRepository(client)
        return ExtractionRunner(
            documents=self.documents,
            storage=self.raw_storage(raw_dir),
            repository=repository, provider=provider, settings=self.settings,
        )

    def quality_gate_runner(self) -> QualityGateRunner:
        """Assemble a manual Agent quality run; Service access is SELECT-only."""
        client = getattr(self.sources, "client", None)
        if client is None:
            raise RuntimeError("Quality Gate requires an Agent Supabase client")
        return QualityGateRunner(
            sources=self.sources, documents=self.documents,
            quality=SupabaseQualityRepository(client),
            service_duplicates=SupabaseServiceDuplicateLookup(client),
            settings=self.settings,
        )

    def normalization_runner(self) -> NormalizationRunner:
        """Explicit Agent Candidate normalization; no LLM or Service access."""
        client = getattr(self.sources, "client", None)
        if client is None:
            raise RuntimeError("Normalization requires an Agent Supabase client")
        return NormalizationRunner(SupabaseNormalizationRepository(client))

    def source_approval_service(self) -> SourceApprovalService:
        """Manual review boundary; requires Agent migration 004 and service-role access."""
        client = getattr(self.sources, "client", None)
        if client is None:
            raise RuntimeError("Source review requires an Agent Supabase client")
        return SourceApprovalService(SupabaseSourceReviewRepository(client))

    def service_db_adapter(self) -> SupabaseServiceDBAdapter:
        """Manual dry-run/publish boundary; construction performs no writes."""
        client = getattr(self.sources, "client", None)
        if client is None:
            raise RuntimeError("Service adapter requires a Supabase client")
        return SupabaseServiceDBAdapter(
            tables=SupabaseServiceTables(client), ledger=SupabasePublishLedger(client),
            agent_sources=self.sources, publish_threshold=self.settings.publish_threshold,
            publish_enabled=self.settings.publish_enabled,
        )

    def publish_worker(self, *, worker_id: str) -> PublishWorker:
        client = getattr(self.sources, "client", None)
        if client is None:
            raise RuntimeError("publish worker requires Agent Supabase client")
        return PublishWorker(
            jobs=SupabasePublishJobs(client), ledger=SupabasePublishLedger(client),
            adapter=self.service_db_adapter(), worker_id=worker_id,
            max_jobs=self.settings.publish_max_jobs_per_run,
            lease_seconds=self.settings.publish_lease_seconds,
        )

    def automation_runtime(self, raw_dir: Path, *, worker_id: str) -> AutomationRuntime:
        client = getattr(self.sources, "client", None)
        if client is None:
            raise RuntimeError("automation requires Agent Supabase client")
        return AutomationRuntime(
            enabled=self.settings.automation_enabled,
            worker_enabled={
                "discovery": self.settings.discovery_worker_enabled and self.settings.discovery_enabled,
                "collection": self.settings.collection_worker_enabled,
                "processing": self.settings.processing_worker_enabled,
                "publish": self.settings.publish_worker_enabled and self.settings.publish_enabled,
            },
            workers={
                "discovery": lambda: self.discovery_worker(worker_id=worker_id),
                "collection": lambda: self.collection_worker(raw_dir, worker_id=worker_id),
                "processing": lambda: self.processing_worker(raw_dir, worker_id=worker_id),
                "publish": lambda: self.publish_worker(worker_id=worker_id),
            },
            lease=SupabaseAutomationLease(client), worker_id=worker_id,
            lease_seconds=self.settings.automation_lease_seconds,
        )
