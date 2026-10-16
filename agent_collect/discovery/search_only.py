"""Discovery execution with no capability to register or approve a Source.

This runner is for explicitly invoked searches. It persists only the
existing query, result, and provider-budget records. It is never
wired into the scheduled Cloud Run automation.
"""

from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from agent_collect.budget.reservations import BudgetReservations
from agent_collect.discovery.budget import SearchBudgetGuard
from agent_collect.discovery.evaluation import SourceEvaluator
from agent_collect.discovery.followup import OfficialFollowupExplorer, ReviewedOfficialRoot
from agent_collect.discovery.goal_supabase import GoalExecutionWorker, SupabaseGoalPlans
from agent_collect.discovery.models import (
    DiscoveryQuery, EvaluationDecision, SourceCandidate, SourceCandidateStatus,
    SourceEvaluation,
)
from agent_collect.discovery.providers import SearchProvider, SearchRequestPolicy
from agent_collect.discovery.quality import (
    QualityClass, SearchQualityEvaluator, listing_followup_plan, quality_metrics,
)
from agent_collect.discovery.runner import DiscoveryRunner
from agent_collect.repositories.discovery import DiscoveryRepository
from agent_collect.schemas.models import Source


class _SourceLookup:
    """Expose just the lookup used by CandidateFilter, never Source.save."""

    def __init__(self, find: Callable[[str], Source | None]) -> None:
        self.find_by_canonical_url = find


class _LookupRegistry:
    def __init__(self, lookup: _SourceLookup) -> None:
        self.repository = lookup


class _NoPromotionGate:
    threshold = 1.0

    def promote(self, candidate: SourceCandidate, evaluation: SourceEvaluation) -> None:
        return None


class _ReviewOnlyEvaluator:
    def __init__(self, delegate: SourceEvaluator) -> None:
        self.delegate = delegate

    def evaluate(self, candidate: SourceCandidate) -> SourceEvaluation:
        evaluation = self.delegate.evaluate(candidate)
        if evaluation.decision is not EvaluationDecision.ACCEPT:
            return evaluation
        return replace(
            evaluation, decision=EvaluationDecision.REVIEW,
            confidence=min(evaluation.confidence, 0.55),
            reasons=tuple(dict.fromkeys(evaluation.reasons +
                                        ("search_only_source_review_required",))),
        )


class _NoCandidateWrites:
    """Keep evaluations in memory; never register Source Candidates in the DB."""

    def __init__(self, repository: DiscoveryRepository,
                 quality: SearchQualityEvaluator,
                 followup: OfficialFollowupExplorer | None = None,
                 reviewed_roots: tuple[ReviewedOfficialRoot, ...] = (),
                 find_source: Callable[[str], Source | None] | None = None) -> None:
        self.repository = repository
        self.quality = quality
        self.followup = followup
        self.reviewed_roots = {row.origin: row for row in reviewed_roots}
        self.find_source = find_source
        self.candidates: dict[UUID, SourceCandidate] = {}
        self.followup_candidates: dict[UUID, SourceCandidate] = {}
        self.assessments = {}

    def save_result(self, result):
        if result.status.value != "DISCOVERED":
            if (result.canonical_url and
                    not any(reason in {"duplicate_url_in_query", "existing_candidate",
                                       "existing_source"} for reason in result.filter_reasons) and
                    self.repository.has_prior_result_url(result.canonical_url, result.id)):
                result = replace(result, filter_reasons=result.filter_reasons +
                                 ("previous_search_result",))
            assessment = self.quality.assess(result, now=datetime.now(timezone.utc))
            self.assessments[result.id] = assessment
            followup = listing_followup_plan(result, assessment)
            followup_audit = {}
            origin = f"https://{urlsplit(result.original_url).hostname}"
            reviewed = self.reviewed_roots.get(origin)
            if (self.followup is not None and reviewed is not None and
                    assessment.classification not in {QualityClass.DUPLICATE,
                                                      QualityClass.NEWS_ARTICLE,
                                                      QualityClass.SOCIAL_POST}):
                outcome = self.followup.explore(result, reviewed=reviewed,
                                                now=datetime.now(timezone.utc))
                followup_audit = outcome.audit_record()
                for lead in outcome.leads:
                    if lead.page_type != "INDIVIDUAL_NOTICE":
                        continue
                    if (self.repository.find_candidate_by_canonical_url(lead.url) is not None or
                            (self.find_source is not None and self.find_source(lead.url) is not None) or
                            any(item.canonical_url == lead.url
                                for item in self.followup_candidates.values())):
                        continue
                    candidate = SourceCandidate(
                        id=uuid4(), query_id=result.query_id, search_result_id=result.id,
                        original_url=lead.url, candidate_url=lead.url,
                        canonical_url=lead.url,
                        domain=urlsplit(lead.url).hostname or "", title=lead.link_text,
                        snippet="", discovered_at=result.discovered_at,
                        status=SourceCandidateStatus.REVIEW_REQUIRED,
                        evaluation_decision=EvaluationDecision.REVIEW,
                        confidence=0.45,
                        reasons=("official_followup_requires_review",),
                        evidence=(f"root_search_result:{result.id}",),
                        provenance={"execution_mode": "SEARCH_ONLY",
                                    "source_approval_eligible": False,
                                    "followup_depth": lead.depth})
                    self.followup_candidates[candidate.id] = candidate
            result = replace(result, provider_metadata={
                **result.provider_metadata,
                "quality": {**assessment.audit_record(), **followup_audit},
                **({"quality_followup_plan": followup} if followup else {}),
            })
        return self.repository.save_result(result)

    def save_candidate(self, candidate: SourceCandidate) -> SourceCandidate:
        self.candidates[candidate.id] = candidate
        return candidate

    def get_candidate(self, candidate_id: UUID) -> SourceCandidate | None:
        return self.candidates.get(candidate_id)

    def find_candidate_by_canonical_url(self, canonical_url: str) -> SourceCandidate | None:
        return self.repository.find_candidate_by_canonical_url(canonical_url)

    def __getattr__(self, name: str):
        return getattr(self.repository, name)


class SearchOnlyDiscoveryRunner(DiscoveryRunner):
    """Reuse Discovery's atomic budget and audit path without Source writes."""

    def __init__(self, *, provider: SearchProvider, repository: DiscoveryRepository,
                 find_source: Callable[[str], Source | None], evaluator: SourceEvaluator,
                 budget_guard: SearchBudgetGuard, reservations: BudgetReservations,
                 policy: SearchRequestPolicy | None = None,
                 quality: SearchQualityEvaluator | None = None,
                 followup: OfficialFollowupExplorer | None = None,
                 reviewed_roots: tuple[ReviewedOfficialRoot, ...] = ()) -> None:
        if provider.name != "tavily" or budget_guard is None or reservations is None:
            raise ValueError("search-only execution requires guarded Tavily and atomic budget")
        if reviewed_roots and followup is None:
            raise ValueError("reviewed roots require a bounded follow-up explorer")
        super().__init__(
            provider=provider, repository=_NoCandidateWrites(
                repository, quality or SearchQualityEvaluator(), followup, reviewed_roots,
                find_source),
            registry=_LookupRegistry(_SourceLookup(find_source)),
            evaluator=_ReviewOnlyEvaluator(evaluator), gate=_NoPromotionGate(),
            policy=policy, budget_guard=budget_guard, reservations=reservations,
            auto_assessor=None, auto_executor=None,
            candidate_provenance={"execution_mode": "SEARCH_ONLY",
                                  "source_approval_eligible": False},
        )

    def run(self, query: DiscoveryQuery):
        prior_ids = set(self.repository.assessments)
        outcome = super().run(replace(
            query, provenance={**query.provenance, "execution_mode": "SEARCH_ONLY",
                               "source_approval_eligible": False,
                               "source_candidates_persisted": 0}))
        assessments = tuple(value for key, value in self.repository.assessments.items()
                            if key not in prior_ids)
        self.last_quality_metrics = quality_metrics(assessments)
        return outcome


class ExactPlanSearchWorker(GoalExecutionWorker):
    """Manual-only exact-plan worker, limited to two local claim attempts."""

    def __init__(self, *, plans: SupabaseGoalPlans, runner: SearchOnlyDiscoveryRunner,
                 plan_id: UUID, enabled: bool = False) -> None:
        if not isinstance(runner, SearchOnlyDiscoveryRunner) or not isinstance(plan_id, UUID):
            raise ValueError("exact plan search requires a search-only runner and Plan UUID")
        super().__init__(plans=plans, runner=runner, enabled=enabled, plan_id=plan_id)
        self._claim_attempts = 0

    def run_once(self, *, dry_run: bool = False):
        if self.enabled and not dry_run:
            if self._claim_attempts >= 2:
                return {"status": "LIMIT_REACHED", "executed_query_count": 0}
            self._claim_attempts += 1
        return super().run_once(dry_run=dry_run)
