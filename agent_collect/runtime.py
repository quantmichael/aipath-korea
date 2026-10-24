"""Runtime assembly for Agent-owned repositories; no Backend imports."""

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any, Mapping
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

from agent_collect.collectors.planner import CollectionPlanner
from agent_collect.adapters.service_db.duplicate_lookup import SupabaseServiceDuplicateLookup
from agent_collect.adapters.service_db.adapter import SupabaseServiceDBAdapter
from agent_collect.adapters.service_db.supabase_tables import SupabaseServiceTables
from agent_collect.collectors.runner import CollectionRunner
from agent_collect.collectors.security import CollectionTransportError
from agent_collect.config.settings import AgentSettings, load_settings
from agent_collect.discovery.evaluation import RuleBasedEvaluator
from agent_collect.discovery.auto_approval import (
    ApprovalScope, AutoApprovalAssessor, AutoApprovalExecutor, load_approval_scopes,
)
from agent_collect.discovery.budget import SearchBudgetGuard, SearchBudgetPolicy
from agent_collect.discovery.providers import SearchProvider, SearchRequestPolicy
from agent_collect.discovery.tavily import TavilySearchProvider
from agent_collect.discovery.scheduling import DiscoveryWorker
from agent_collect.discovery.planner import AutonomousDiscoveryPlanner, SearchPlan
from agent_collect.discovery.goal import GoalDrivenDiscoveryPlanner, OpenAIGoalPlanner
from agent_collect.discovery.goal_supabase import (
    GoalExecutionWorker, GoalPlanningWorker, SupabaseGoalContext, SupabaseGoalPlans,
)
from agent_collect.discovery.scheduling_supabase import SupabaseDiscoverySchedules
from agent_collect.discovery.models import DiscoveryQuery, QueryStatus, SearchResultStatus
from agent_collect.discovery.followup import FollowupLead, OfficialFollowupExplorer, ReviewedOfficialRoot
from agent_collect.schemas.models import Vertical
from agent_collect.schemas.status import SourceStatus
from agent_collect.discovery.runner import DiscoveryRunner, PromotionGate
from agent_collect.discovery.quality import SearchQualityEvaluator
from agent_collect.discovery.evidence_supabase import (
    SupabaseEvidenceIntegration, SupabasePreGateEvidence,
)
from agent_collect.discovery.search_only import SearchOnlyDiscoveryRunner
from agent_collect.budget.reservations import SupabaseBudgetReservations
from agent_collect.extraction.providers import ExtractionProvider
from agent_collect.extraction.providers import OpenAIExtractionProvider
from agent_collect.extraction.runner import ExtractionRunner
from agent_collect.normalization.runner import NormalizationRunner
from agent_collect.metadata.evidence import MetadataUsageRights, usage_references_replayed
from agent_collect.metadata.search_registration import (
    preview_scoped_direct_result, preview_scoped_followup_lead,
    publish_one_search_preview,
)
from agent_collect.metadata.supabase import SupabaseMetadataEvidence
from agent_collect.quality_gate.runner import QualityGateRunner
from agent_collect.registry.service import SourceRegistryService, canonicalize_url
from agent_collect.registry.approval import SourceApprovalService, source_is_collectible
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
    investigation_scopes: tuple[ApprovalScope, ...] = ()
    search_registration_permissions: tuple[tuple[ApprovalScope, MetadataUsageRights], ...] = ()

    @classmethod
    def connect(cls, environ: Mapping[str, str] | None = None) -> "AgentRuntime":
        client = create_agent_client(environ)
        env = os.environ if environ is None else environ
        raw_scopes = env.get("AGENT_DISCOVERY_INVESTIGATION_SCOPES_JSON")
        scopes = load_approval_scopes(raw_scopes) if raw_scopes else ()
        if len(scopes) > 5:
            raise ValueError("at most five investigation scopes are allowed")
        return cls(
            sources=SupabaseSourceRepository(client),
            runs=SupabaseCollectionRunRepository(client),
            documents=SupabaseRawDocumentRepository(client),
            settings=load_settings(environ=environ),
            investigation_scopes=scopes,
        )

    def collection_runner(self, raw_dir: Path, *, environ: Mapping[str, str] | None = None) -> CollectionRunner:
        auto_checker = None
        if self.settings.discovery_auto_approval_enabled:
            auto_checker = AutoApprovalAssessor(
                load_approval_scopes(self.settings.discovery_auto_approval_scopes_json),
                user_agent=self.settings.user_agent,
            ).revalidate_source
        return CollectionRunner(
            sources=self.sources,
            runs=self.runs,
            documents=self.documents,
            storage=self.raw_storage(raw_dir),
            planner=CollectionPlanner(),
            settings=self.settings,
            environ=environ,
            auto_permission_checker=auto_checker,
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

    def evidence_integration(self, raw_dir: Path) -> SupabaseEvidenceIntegration:
        """Assemble explicit, read-only PHASE 12J evidence inspection."""
        client = getattr(self.sources, "client", None)
        if client is None:
            raise RuntimeError("Evidence integration requires an Agent Supabase client")
        return SupabaseEvidenceIntegration(client, self.raw_storage(raw_dir))

    def metadata_evidence(self) -> SupabaseMetadataEvidence:
        """Assemble the metadata SDK without fetching pages or writing records."""
        client = getattr(self.sources, "client", None)
        if client is None:
            raise RuntimeError("Metadata evidence requires an Agent Supabase client")
        return SupabaseMetadataEvidence(client)

    def investigate_discovery_results(self, query_ids: tuple[str, ...]) -> dict[str, int]:
        """Bounded research before Source approval; no Candidate or approval writes."""
        counts = {"investigation_count": 0, "investigation_lead_count": 0,
                  "investigation_blocked_count": 0}
        client = getattr(self.sources, "client", None)
        repository = SupabaseDiscoveryRepository(client)
        explorer = OfficialFollowupExplorer(user_agent=self.settings.user_agent)
        for query_id in tuple(dict.fromkeys(query_ids))[:5]:
            query = repository.get_query(UUID(query_id))
            if query is None or query.status is not QueryStatus.SUCCEEDED:
                continue
            response = (client.table("agent_search_results").select("id", count="exact")
                        .eq("query_id", query_id).limit(21).execute())
            rows = response.data or []
            if response.count is None or response.count != len(rows) or len(rows) > 20:
                raise RuntimeError("investigation result scan incomplete")
            for row in rows:
                if counts["investigation_count"] >= 5:
                    return counts
                result = repository.get_result(UUID(row["id"]))
                if result is None or result.query_id != query.id:
                    continue
                scopes = [scope for scope in self.investigation_scopes
                          if scope.covers(result.original_url)]
                if len(scopes) != 1:
                    counts["investigation_blocked_count"] += 1
                    continue
                counts["investigation_count"] += 1
                outcome = explorer.investigate(result, scope=scopes[0],
                                               now=datetime.now(timezone.utc))
                counts["investigation_lead_count"] += len(outcome.leads)
                counts["investigation_blocked_count"] += bool(outcome.blocked)
        return counts

    def process_search_registration(self, query_ids: tuple[str, ...]) -> dict[str, int]:
        """Bounded opt-in handoff through reviewed scope, fresh page and Service 004.

        No scope/right pair means no HTTP or DB work. Production connection does
        not populate these pairs until a separate reviewed configuration exists.
        """
        counts = {"result_count": 0, "lead_count": 0, "eligible_count": 0,
                  "published_count": 0, "held_count": 0}
        if not self.search_registration_permissions or not query_ids:
            return counts
        client = getattr(self.sources, "client", None)
        if client is None:
            raise RuntimeError("search registration requires an Agent Supabase client")
        repository = SupabaseDiscoveryRepository(client)
        tables = SupabaseServiceTables(client)
        explorer = OfficialFollowupExplorer(user_agent=self.settings.user_agent)
        for query_id in tuple(dict.fromkeys(query_ids))[:5]:
            if counts["result_count"] >= 5 or counts["lead_count"] >= 5:
                break
            query = repository.get_query(UUID(query_id))
            if query is None or query.status is not QueryStatus.SUCCEEDED:
                continue
            response = (client.table("agent_search_results")
                        .select("id", count="exact").eq("query_id", query_id)
                        .limit(21).execute())
            rows = response.data or []
            if response.count is None or response.count != len(rows) or len(rows) > 20:
                raise RuntimeError("search registration result scan incomplete")
            for row in rows:
                if counts["result_count"] >= 5 or counts["lead_count"] >= 5:
                    break
                result = repository.get_result(UUID(row["id"]))
                if (result is None or result.query_id != query.id or
                        result.status is not SearchResultStatus.CANDIDATE):
                    continue
                counts["result_count"] += 1
                now = datetime.now(timezone.utc)
                matches = [(scope, rights) for scope, rights in self.search_registration_permissions
                           if (isinstance(scope, ApprovalScope) and
                               isinstance(rights, MetadataUsageRights) and
                               scope.reviewed_at <= now < scope.expires_at and
                               scope.covers(result.original_url) and
                               rights.metadata_permitted())]
                if len(matches) != 1:
                    counts["held_count"] += 1
                    continue
                scope, rights = matches[0]
                outcome = explorer.investigate(result, scope=scope, now=now)
                if outcome.blocked:
                    counts["held_count"] += 1
                    continue
                direct_url = canonicalize_url(result.canonical_url or result.original_url)
                direct_path = urlsplit(direct_url).path.rstrip("/")
                direct_parent = direct_path.rpartition("/")[0] + "/"
                direct = (direct_url in outcome.visited_urls and
                          direct_path and direct_parent == scope.path_prefix and
                          not urlsplit(direct_url).query)
                if not outcome.leads and not direct:
                    counts["held_count"] += 1
                    continue
                reviewed = ReviewedOfficialRoot(scope.origin, scope.reviewed_by,
                                                "reviewed explicit license", scope.path_prefix)
                evidence_root = ReviewedOfficialRoot(scope.origin, scope.reviewed_by,
                                                     "reviewed permission evidence")
                for lead in ((None,) if direct else ()) + outcome.leads:
                    if counts["lead_count"] >= 5:
                        break
                    if (lead is not None and
                            (lead.depth != 1 or lead.page_type != "INDIVIDUAL_NOTICE" or
                             not scope.covers(lead.url))):
                        counts["held_count"] += 1
                        continue
                    page_url = lead.url if lead is not None else direct_url
                    counts["lead_count"] += 1
                    try:
                        officiality = explorer._fetch(
                            scope.officiality_evidence_url, evidence_root,
                            request_policy=lambda url: url == scope.officiality_evidence_url)
                        terms = explorer._fetch(
                            scope.terms_url, evidence_root,
                            request_policy=lambda url: url == scope.terms_url)
                        page = explorer._fetch(page_url, reviewed,
                                               request_policy=scope.covers)
                        if (officiality.final_url != scope.officiality_evidence_url or
                                terms.final_url != scope.terms_url or
                                canonicalize_url(page.final_url) != canonicalize_url(page_url)):
                            counts["held_count"] += 1
                            continue
                    except (CollectionTransportError, ValueError, UnicodeError):
                        counts["held_count"] += 1
                        continue
                    checked_at = datetime.now(timezone.utc)
                    common = dict(
                        search_result=result, page=page.content,
                        scope=scope, rights=rights,
                        officiality_document=officiality.content,
                        terms_document=terms.content,
                        permission_checked_at=checked_at,
                        service_sources=tables.list_sources(),
                        service_categories=tables.list_categories(),
                        existing_rows=tables.list_opportunities(),
                        snapshot_complete=True, fetched_at=checked_at,
                        now=checked_at)
                    preview = (preview_scoped_followup_lead(lead=lead, **common)
                               if lead is not None else
                               preview_scoped_direct_result(page_url=page_url, **common))
                    if not preview.eligible:
                        counts["held_count"] += 1
                        continue
                    counts["eligible_count"] += 1
                    publication = publish_one_search_preview(
                        preview, tables, publish_enabled=self.settings.publish_enabled)
                    if publication.status == "PUBLISHED":
                        counts["published_count"] += 1
                    elif publication.status != "DISABLED":
                        counts["held_count"] += 1
        return counts

    def process_discovery_metadata(self, query_ids: tuple[str, ...]) -> dict[str, int]:
        """Handoff only newly searched, individually approved official notices.

        The global Automation lease surrounds this call. No approval, missing
        audit, changed permission document, or inaccessible page means no
        Candidate write. Follow-up stays within a separately approved root.
        """
        client = getattr(self.sources, "client", None)
        if client is None:
            raise RuntimeError("Metadata handoff requires an Agent Supabase client")
        counts = {"lead_count": 0, "processed_count": 0,
                  "review_required_count": 0, "publish_ready_count": 0}
        if not query_ids:
            return counts
        if self.investigation_scopes:
            try:
                counts.update(self.investigate_discovery_results(query_ids))
            except Exception:
                # Optional research must never interrupt the approved Metadata path.
                # Do not log exception text from HTTP or DB providers.
                counts["investigation_error_count"] = 1
        metadata = self.metadata_evidence()
        response = (client.table("agent_metadata_source_approvals")
                    .select("*", count="exact").eq("cohort", "operational")
                    .limit(21).execute())
        approvals = response.data or []
        if response.count is None or response.count > 20 or response.count != len(approvals):
            raise RuntimeError("metadata approval scan incomplete")
        if not approvals:
            return counts
        now = datetime.now(timezone.utc)
        permitted = []
        for approval in approvals:
            source = metadata.sources.get(UUID(approval["source_id"]))
            policy = metadata._policy(approval, cohort="operational")
            if (source is None or source.status not in {SourceStatus.APPROVED,
                                                        SourceStatus.ACTIVE} or
                    policy is None or policy.approved_principal_id is None or
                    policy.approval_event_id is None or
                    not policy.permits(source, approval["official_url"], now=now)[0]):
                continue
            permitted.append((approval, policy))
        if not permitted:
            return counts
        explorer = OfficialFollowupExplorer(user_agent=self.settings.user_agent)
        repository = SupabaseDiscoveryRepository(client)
        seen: set[tuple[str, str]] = set()
        explored: dict[tuple[str, str, str], tuple[FollowupLead, ...]] = {}
        for query_id in dict.fromkeys(query_ids):
            if counts["lead_count"] >= 5:
                break
            query = repository.get_query(UUID(query_id))
            if query is None or query.status is not QueryStatus.SUCCEEDED:
                continue
            found = (client.table("agent_search_results")
                     .select("id", count="exact").eq("query_id", query_id)
                     .limit(21).execute())
            rows = found.data or []
            if found.count is None or found.count > 20 or found.count != len(rows):
                raise RuntimeError("discovery result scan incomplete")
            for row in rows:
                if counts["lead_count"] >= 5:
                    break
                result = repository.get_result(UUID(row["id"]))
                if result is None or result.query_id != query.id:
                    continue
                result_url = canonicalize_url(result.canonical_url or result.original_url)
                for approval, policy in permitted:
                    if counts["lead_count"] >= 5:
                        break
                    target = canonicalize_url(approval["official_url"])
                    leads: tuple[FollowupLead, ...] = ()
                    if result_url == target:
                        leads = (FollowupLead(
                            approval["official_url"], result_url, 1, result.title,
                            "INDIVIDUAL_NOTICE", None, None, "UNKNOWN", "UNKNOWN",
                            None, "authenticated_metadata_approval"),)
                    else:
                        plan = result.provider_metadata.get("quality_followup_plan")
                        if (not isinstance(plan, dict) or
                                plan.get("kind") != "INDIVIDUAL_NOTICE_SEARCH" or
                                plan.get("source_search_result_id") != str(result.id)):
                            continue
                        root_source = self.sources.find_by_canonical_url(result_url)
                        if root_source is None or not source_is_collectible(root_source):
                            continue
                        reviewed = ReviewedOfficialRoot(
                            policy.scope_origin, policy.reviewer,
                            policy.organizer_relationship, policy.path_prefix)
                        if not reviewed.covers(result_url):
                            continue
                        scope = (result.id.hex, policy.scope_origin, policy.path_prefix)
                        if scope not in explored:
                            if len(explored) >= 5:
                                continue
                            explored[scope] = explorer.explore(
                                result, reviewed=reviewed,
                                now=datetime.now(timezone.utc)).leads
                        leads = explored[scope]
                    for lead in leads:
                        identity = (str(approval["id"]), canonicalize_url(lead.url))
                        if (lead.page_type != "INDIVIDUAL_NOTICE" or
                                identity[1] != target or identity in seen):
                            continue
                        seen.add(identity)
                        counts["lead_count"] += 1
                        reviewed = ReviewedOfficialRoot(
                            policy.scope_origin, policy.reviewer,
                            policy.organizer_relationship, policy.path_prefix)
                        try:
                            # Both GETs use the existing robots-aware, DNS-pinned
                            # transport. The permission URL is exact and hashed.
                            permission_root = ReviewedOfficialRoot(
                                policy.scope_origin, policy.reviewer,
                                policy.organizer_relationship)
                            permission = explorer._fetch(policy.permission_url, permission_root)
                            checked_at = datetime.now(timezone.utc)
                            if (hashlib.sha256(permission.content).hexdigest() !=
                                    policy.permission_digest):
                                counts["review_required_count"] += 1
                                continue
                            page = explorer._fetch(lead.url, reviewed)
                            fetched_at = datetime.now(timezone.utc)
                        except (CollectionTransportError, ValueError, UnicodeError):
                            counts["review_required_count"] += 1
                            continue
                        try:
                            outcome = metadata.process_followup_lead(
                                lead=lead, approval_id=UUID(approval["id"]), facts=None,
                                page=page.content, fetched_at=fetched_at,
                                permission_document=permission.content,
                                permission_checked_at=checked_at,
                                request_id=uuid5(NAMESPACE_URL,
                                                 f"metadata:{approval['id']}:{lead.url}"),
                                decision_id=uuid5(NAMESPACE_URL,
                                                  f"metadata:{query_id}:{approval['id']}:{lead.url}"),
                                now=datetime.now(timezone.utc))
                        except ValueError:
                            counts["review_required_count"] += 1
                            continue
                        if outcome.stored_candidate_id is None:
                            counts["review_required_count"] += 1
                        else:
                            counts["processed_count"] += 1
                            if outcome.outcome.gate.status.value == "PUBLISH_READY":
                                counts["publish_ready_count"] += 1
        return counts

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
            quality_gate=self.quality_gate_runner(require_official_evidence=True,
                                                  raw_dir=raw_dir),
            policy=self.processing_policy(), worker_id=worker_id,
        )

    def discovery_runner(
        self, provider: SearchProvider, *, repository: DiscoveryRepository | None = None,
        search_only: bool = False,
    ) -> DiscoveryRunner:
        """Assemble Discovery without running it; search_only has no Source write path."""
        if not self.settings.discovery_enabled and provider.name != "tavily":
            raise RuntimeError("Discovery feature flag is disabled")
        if self.settings.discovery_search_provider and provider.name != self.settings.discovery_search_provider:
            raise ValueError("configured Discovery provider does not match")
        if repository is None:
            client = getattr(self.sources, "client", None)
            if client is None:
                raise RuntimeError("Discovery repository or Supabase client required")
            repository = SupabaseDiscoveryRepository(client)
        if search_only and provider.name != "tavily":
            raise ValueError("search-only execution requires Tavily")
        registry = None if search_only else SourceRegistryService(self.sources)
        auto_assessor = auto_executor = None
        if self.settings.discovery_auto_approval_enabled and not search_only:
            client = getattr(self.sources, "client", None)
            if client is None:
                raise RuntimeError("auto approval requires audited Supabase Source review")
            auto_assessor = AutoApprovalAssessor(
                load_approval_scopes(self.settings.discovery_auto_approval_scopes_json),
                user_agent=self.settings.user_agent,
            )
            def schedule_confirmed(source):
                rows = (client.table("agent_source_schedules")
                        .select("source_id").eq("source_id", str(source.id))
                        .limit(1).execute().data or [])
                return len(rows) == 1 and rows[0].get("source_id") == str(source.id)

            auto_executor = AutoApprovalExecutor(
                self.source_approval_service(), schedule_verifier=schedule_confirmed)
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
        evaluator = RuleBasedEvaluator(
            official_domain_hints=self.settings.discovery_official_domain_hints,
        )
        policy = SearchRequestPolicy(
            max_results=self.settings.discovery_max_results,
            timeout_seconds=self.settings.discovery_request_timeout_seconds,
        )
        reservations = (SupabaseBudgetReservations(getattr(self.sources, "client"))
                        if provider.name == "tavily" and
                        getattr(self.sources, "client", None) is not None
                        else None)
        if search_only:
            return SearchOnlyDiscoveryRunner(
                provider=provider, repository=repository,
                find_source=self.sources.find_by_canonical_url,
                evaluator=evaluator, budget_guard=budget_guard,
                reservations=reservations, policy=policy,
            )
        return DiscoveryRunner(
            provider=provider, repository=repository, registry=registry,
            evaluator=evaluator,
            gate=PromotionGate(registry, threshold=self.settings.discovery_promotion_threshold),
            policy=policy,
            budget_guard=budget_guard,
            auto_assessor=auto_assessor,
            auto_executor=auto_executor,
            reservations=reservations,
            quality_evaluator=SearchQualityEvaluator(),
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

    def discovery_plan_preview(self, *, now: datetime | None = None) -> SearchPlan:
        """Read-only proposal using existing schedules and the persistent usage ledger."""
        client = getattr(self.sources, "client", None)
        if client is None:
            raise RuntimeError("Discovery planning requires an Agent Supabase client")
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        policy = SearchBudgetPolicy(
            daily_limit=self.settings.discovery_daily_search_limit,
            monthly_limit=self.settings.discovery_monthly_search_limit,
            cooldown_hours=self.settings.discovery_query_cooldown_hours,
            timezone_name=self.settings.discovery_budget_timezone,
            warning_threshold=self.settings.discovery_monthly_warning_threshold,
            critical_threshold=self.settings.discovery_monthly_critical_threshold,
        )
        local = now.astimezone(ZoneInfo(policy.timezone_name))
        month_start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        since = min(month_start.astimezone(timezone.utc),
                    now - timedelta(hours=policy.cooldown_hours))
        schedules = tuple(self.discovery_schedule_repository().list_all())
        queries = tuple(SupabaseDiscoveryRepository(client).list_queries_since("tavily", since))
        return AutonomousDiscoveryPlanner(
            policy=policy, daily_plan_limit=min(3, policy.daily_limit)).plan(
            now=now, existing_schedules=schedules, recent_queries=queries)

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

    def quality_gate_runner(self, *, require_official_evidence: bool = True,
                            raw_dir: Path | None = None) -> QualityGateRunner:
        """Assemble a manual Agent quality run; Service access is SELECT-only."""
        client = getattr(self.sources, "client", None)
        if client is None:
            raise RuntimeError("Quality Gate requires an Agent Supabase client")
        assessor = (SupabasePreGateEvidence(client, self.raw_storage(raw_dir))
                    if require_official_evidence and raw_dir is not None else None)
        return QualityGateRunner(
            sources=self.sources, documents=self.documents,
            quality=SupabaseQualityRepository(client),
            service_duplicates=SupabaseServiceDuplicateLookup(client),
            settings=self.settings,
            evidence_assessor=assessor,
            require_official_evidence=require_official_evidence,
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

    def metadata_publication_inputs(self, candidate_id: UUID) -> dict[str, Any]:
        """Fetch transient evidence only for a currently approved ACTIVE Source."""
        empty = {"page": None, "page_url": None, "fetched_at": None,
                 "permission_document": None, "permission_checked_at": None}
        if not (self.settings.publish_enabled and self.settings.publish_worker_enabled):
            raise RuntimeError("Metadata publishing is disabled")
        source, policy = self.metadata_evidence().publication_context(candidate_id)
        now = datetime.now(timezone.utc)
        if (source is None or source.provenance.get("synthetic_only") is True or
                not source_is_collectible(source) or policy is None or
                policy.approved_principal_id is None or policy.approval_event_id is None or
                policy.usage_rights is None or not policy.usage_rights.metadata_permitted() or
                not policy.permits(source, policy.official_url, now=now)[0]):
            return empty
        explorer = OfficialFollowupExplorer(user_agent=self.settings.user_agent)
        permission_root = ReviewedOfficialRoot(
            policy.scope_origin, policy.reviewer, policy.organizer_relationship)
        reviewed = ReviewedOfficialRoot(
            policy.scope_origin, policy.reviewer, policy.organizer_relationship,
            policy.path_prefix)
        try:
            permission = explorer._fetch(
                policy.permission_url, permission_root,
                request_policy=lambda target: target == policy.permission_url)
            checked_at = datetime.now(timezone.utc)
            if (permission.final_url != policy.permission_url or
                    hashlib.sha256(permission.content).hexdigest() != policy.permission_digest or
                    not usage_references_replayed(policy, permission.content)):
                return empty
            page = explorer._fetch(
                policy.official_url, reviewed,
                request_policy=lambda target: target == policy.official_url)
            if page.final_url != policy.official_url:
                return empty
            return {"page": page.content, "page_url": page.final_url,
                    "fetched_at": datetime.now(timezone.utc),
                    "permission_document": permission.content,
                    "permission_checked_at": checked_at}
        except (CollectionTransportError, ValueError, UnicodeError, OSError):
            # Existing assessment records a hold; no page is invented or persisted.
            return empty

    def publish_worker(self, *, worker_id: str,
                       automated_metadata: bool = False) -> PublishWorker:
        client = getattr(self.sources, "client", None)
        if client is None:
            raise RuntimeError("publish worker requires Agent Supabase client")
        return PublishWorker(
            jobs=SupabasePublishJobs(client), ledger=SupabasePublishLedger(client),
            adapter=self.service_db_adapter(), worker_id=worker_id,
            max_jobs=self.settings.publish_max_jobs_per_run,
            lease_seconds=self.settings.publish_lease_seconds,
            metadata=self.metadata_evidence(),
            metadata_publish_enabled=(self.settings.publish_enabled and
                                      self.settings.publish_worker_enabled),
            metadata_inputs=self.metadata_publication_inputs if automated_metadata else None,
        )

    def automation_runtime(self, raw_dir: Path, *, worker_id: str) -> AutomationRuntime:
        client = getattr(self.sources, "client", None)
        if client is None:
            raise RuntimeError("automation requires Agent Supabase client")
        return AutomationRuntime(
            enabled=self.settings.automation_enabled,
            worker_enabled={
                "ai_plan": self.settings.ai_discovery_planner_enabled,
                "ai_execute": self.settings.ai_discovery_execution_enabled,
                "discovery": self.settings.discovery_worker_enabled and self.settings.discovery_enabled,
                "collection": self.settings.collection_worker_enabled,
                "processing": self.settings.processing_worker_enabled,
                "publish": self.settings.publish_worker_enabled and self.settings.publish_enabled,
            },
            workers={
                "ai_plan": lambda: GoalPlanningWorker(
                    context=SupabaseGoalContext(client), plans=SupabaseGoalPlans(client),
                    budget=SupabaseBudgetReservations(client),
                    planner=GoalDrivenDiscoveryPlanner(OpenAIGoalPlanner(
                        os.environ.get("OPENAI_API_KEY"), model=self.settings.extraction_model)),
                    enabled=self.settings.ai_discovery_planner_enabled),
                "ai_execute": lambda: GoalExecutionWorker(
                    plans=SupabaseGoalPlans(client),
                    runner=self.discovery_runner(TavilySearchProvider(
                        os.environ.get("AGENT_TAVILY_API_KEY"))),
                    enabled=self.settings.ai_discovery_execution_enabled),
                "discovery": lambda: self.discovery_worker(worker_id=worker_id),
                "collection": lambda: self.collection_worker(raw_dir, worker_id=worker_id),
                "processing": lambda: self.processing_worker(raw_dir, worker_id=worker_id),
                "publish": lambda: self.publish_worker(worker_id=worker_id,
                                                       automated_metadata=True),
            },
            lease=SupabaseAutomationLease(client), worker_id=worker_id,
            lease_seconds=self.settings.automation_lease_seconds,
            metadata_handoff=self.process_discovery_metadata,
            search_registration_handoff=(self.process_search_registration
                                         if self.search_registration_permissions else None),
        )
