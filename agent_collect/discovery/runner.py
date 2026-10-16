"""Manual discovery application service; does not collect or publish content."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import re
from uuid import uuid4
from agent_collect.budget.reservations import BudgetReservations

from agent_collect.discovery.evaluation import SourceEvaluator
from agent_collect.discovery.auto_approval import AutoApprovalAssessor, AutoApprovalExecutor
from agent_collect.discovery.budget import SearchBudgetGuard
from agent_collect.discovery.filtering import CandidateFilter
from agent_collect.discovery.models import (
    DiscoveryQuery, EvaluationDecision, QueryStatus, SearchResultStatus,
    SourceCandidate, SourceCandidateStatus, SourceEvaluation,
)
from agent_collect.discovery.providers import SearchProvider, SearchProviderError, SearchRequestPolicy
from agent_collect.discovery.quality import SearchQualityEvaluator, listing_followup_plan
from agent_collect.collectors.security import UnsafeTargetError, validate_target_url
from agent_collect.registry.approval import source_is_collectible
from agent_collect.registry.service import SourceRegistryService, canonicalize_url
from agent_collect.repositories.discovery import DiscoveryRepository
from agent_collect.schemas.models import CollectionMethod, Source, SourceType


@dataclass(frozen=True, slots=True)
class DiscoveryOutcome:
    query: DiscoveryQuery
    candidate_ids: tuple[str, ...]
    promoted_source_ids: tuple[str, ...]
    auto_approved_count: int = 0
    review_required_count: int = 0
    rejected_count: int = 0
    collection_scheduled_count: int = 0
    approval_failure_reasons: tuple[str, ...] = ()


class PromotionGate:
    def __init__(self, registry: SourceRegistryService, *, threshold: float) -> None:
        if not 0 <= threshold <= 1:
            raise ValueError("threshold must be between 0 and 1")
        self.registry = registry
        self.threshold = threshold

    def promote(self, candidate: SourceCandidate, evaluation: SourceEvaluation) -> Source | None:
        if candidate.status is not SourceCandidateStatus.ACCEPTED:
            return None
        if candidate.evaluation_decision is not EvaluationDecision.ACCEPT:
            return None
        if evaluation.candidate_id != candidate.id or evaluation.decision is not EvaluationDecision.ACCEPT:
            return None
        if candidate.confidence != evaluation.confidence or evaluation.confidence < self.threshold:
            return None
        signals = set(evaluation.reasons)
        if not (signals & {"configured_official_domain_hint", "official_source_signal"}):
            return None
        if not (signals & {"ai_related", "vertical_related"}) or not evaluation.evidence:
            return None
        try:
            validate_target_url(candidate.candidate_url)
            if canonicalize_url(candidate.candidate_url) != candidate.canonical_url:
                return None
        except (UnsafeTargetError, ValueError, UnicodeError):
            return None
        if self.registry.repository.find_by_canonical_url(candidate.canonical_url) is not None:
            return None
        registered = self.registry.register(
            name=candidate.title, url=candidate.candidate_url,
            source_type=(SourceType.OFFICIAL if "auto_scope_approved" in signals
                         and candidate.provenance.get("auto_approval", {}).get("eligible") is True
                         else SourceType.UNKNOWN),
            collection_method=evaluation.suggested_method or CollectionMethod.HTTP_HTML,
            discovered_at=candidate.discovered_at, discovery_method="search_provider",
            agent_rationale="; ".join(evaluation.reasons),
            confidence=evaluation.confidence,
            provenance={
                **candidate.provenance,
                "discovery_query_id": str(candidate.query_id),
                "search_result_id": str(candidate.search_result_id),
                "source_candidate_id": str(candidate.id),
                "original_url": candidate.original_url,
                "candidate_url": candidate.candidate_url,
                "canonical_url": candidate.canonical_url,
                "evaluation_decision": evaluation.decision.value,
                "evaluation_reasons": list(evaluation.reasons),
                "evaluation_evidence": list(evaluation.evidence),
                "discovery_officiality_signals": [
                    reason for reason in evaluation.reasons
                    if reason in {"configured_official_domain_hint", "official_source_signal"}
                ],
                "discovery_signal_is_approval": False,
            },
        )
        return registered.source if registered.created else None


class DiscoveryRunner:
    def __init__(
        self, *, provider: SearchProvider, repository: DiscoveryRepository,
        registry: SourceRegistryService, evaluator: SourceEvaluator,
        gate: PromotionGate, policy: SearchRequestPolicy | None = None,
        budget_guard: SearchBudgetGuard | None = None,
        auto_assessor: AutoApprovalAssessor | None = None,
        auto_executor: AutoApprovalExecutor | None = None,
        reservations: BudgetReservations | None = None,
        candidate_provenance: dict[str, object] | None = None,
        quality_evaluator: SearchQualityEvaluator | None = None,
    ) -> None:
        self.provider = provider
        self.repository = repository
        self.registry = registry
        self.evaluator = evaluator
        self.gate = gate
        self.policy = policy or SearchRequestPolicy()
        if provider.name == "tavily" and budget_guard is None:
            raise ValueError("Tavily requires SearchBudgetGuard")
        if (auto_assessor is None) != (auto_executor is None):
            raise ValueError("auto approval requires both assessment and audited transition")
        self.budget_guard = budget_guard
        self.reservations = reservations
        self.candidate_provenance = dict(candidate_provenance or {})
        self.quality_evaluator = quality_evaluator
        self.auto_assessor, self.auto_executor = auto_assessor, auto_executor

    def run(self, query: DiscoveryQuery) -> DiscoveryOutcome:
        if query.status is not QueryStatus.PENDING:
            raise ValueError("discovery query must be pending")
        if query.provider != self.provider.name:
            raise ValueError("query provider does not match configured provider")
        if self.budget_guard is not None:
            decision = self.budget_guard.check(query)
            if not decision.allowed:
                # 003 requires completed_at to be null when no execution began.
                blocked = self.repository.save_query(replace(
                    query, status=QueryStatus.FAILED, error_summary=decision.reason.value,
                    provenance={**query.provenance, "budget_blocked": True,
                                "budget_block_reason": decision.reason.value,
                                "budget_daily_usage": decision.daily_usage,
                                "budget_monthly_usage": decision.monthly_usage,
                                "budget_level": decision.level.value},
                ))
                return DiscoveryOutcome(blocked, (), ())
            query = replace(query, provenance={
                **query.provenance, "budget_blocked": False,
                "budget_daily_usage_before": decision.daily_usage,
                "budget_monthly_usage_before": decision.monthly_usage,
                "budget_level_before": decision.level.value,
            })
        reservation_id = None
        if self.provider.name == "tavily" and self.reservations is not None:
            # This RPC is the final authority. The Python guard above is only
            # a cheap preflight and may race another worker.
            reservation_id = self.reservations.reserve(
                kind="tavily", scope="discovery", key=f"discovery:{query.id}",
                query_text=query.text)
            query = replace(query, provenance={**query.provenance,
                                               "budget_reservation_id": str(reservation_id)})
        query = self.repository.save_query(replace(
            query, status=QueryStatus.RUNNING, started_at=datetime.now(timezone.utc),
        ))
        if reservation_id is not None:
            # A failed acknowledgement must never reach the billable provider.
            try:
                self.reservations.dispatch(reservation_id)
            except Exception:
                self.repository.save_query(replace(
                    query, status=QueryStatus.FAILED,
                    completed_at=datetime.now(timezone.utc),
                    error_summary="budget_dispatch_failed",
                ))
                raise
        candidate_ids: list[str] = []
        promoted_ids: list[str] = []
        promoted_domains: set[str] = set()
        result_count = 0
        approved_count = scheduled_count = review_count = rejected_count = 0
        approval_failures: list[str] = []
        try:
            found = self.provider.search(query, policy=self.policy)
            if len(found) > self.policy.max_results:
                raise SearchProviderError("provider_result_limit_exceeded", retryable=False)
            url_seen: set[str] = set()
            candidate_filter = CandidateFilter(self.registry.repository,
                                               existing_candidate_urls=url_seen)
            for result in found:
                if result.query_id != query.id or result.provider != self.provider.name:
                    raise ValueError("provider returned result for wrong query/provider")
                result_count += 1
                self.repository.save_result(result)
                decision = candidate_filter.evaluate(result)
                if decision.canonical_url and self.repository.find_candidate_by_canonical_url(decision.canonical_url):
                    decision = replace(decision, accepted=False,
                                       reasons=decision.reasons + ("existing_candidate",))
                result = replace(
                    result, canonical_url=decision.canonical_url,
                    domain=decision.domain,
                    status=(SearchResultStatus.CANDIDATE if decision.accepted
                            else SearchResultStatus.FILTERED),
                    filter_reasons=decision.reasons,
                )
                if self.quality_evaluator is not None:
                    assessment = self.quality_evaluator.assess(
                        result, now=datetime.now(timezone.utc))
                    followup = listing_followup_plan(result, assessment)
                    result = replace(result, provider_metadata={
                        **result.provider_metadata,
                        "quality": assessment.audit_record(candidate_persisted=None),
                        **({"quality_followup_plan": followup} if followup else {}),
                    })
                self.repository.save_result(result)
                if not decision.accepted:
                    continue
                url_seen.add(decision.canonical_url)
                candidate = SourceCandidate(
                    id=uuid4(), query_id=query.id, search_result_id=result.id,
                    original_url=result.original_url, candidate_url=result.original_url,
                    canonical_url=decision.canonical_url, domain=decision.domain,
                    title=result.title, snippet=result.snippet,
                    discovered_at=result.discovered_at,
                    provenance={"provider": result.provider, "rank": result.rank,
                                "query_text": query.text,
                                "query_vertical": query.vertical_hint.value,
                                "query_vertical_is_approval": False,
                                **self.candidate_provenance},
                )
                self.repository.save_candidate(candidate)
                candidate_ids.append(str(candidate.id))
                evaluation = self.evaluator.evaluate(candidate)
                if evaluation.candidate_id != candidate.id:
                    raise ValueError("evaluation candidate mismatch")
                assessment = None
                if self.auto_assessor is not None:
                    try:
                        assessment = self.auto_assessor.assess(candidate, evaluation)
                    except Exception:
                        # A failed permission probe cannot approve collection.
                        candidate = replace(candidate, provenance={
                            **candidate.provenance, "auto_approval": {
                                "eligible": False, "reason": "permission_check_failed",
                                "policy_version": "official-scope-v1",
                            },
                        })
                        evaluation = replace(
                            evaluation, decision=(EvaluationDecision.REJECT
                                                  if evaluation.decision is EvaluationDecision.REJECT
                                                  else EvaluationDecision.REVIEW),
                            confidence=min(evaluation.confidence, 0.55),
                            reasons=tuple(dict.fromkeys(evaluation.reasons +
                                                         ("auto_approval_held:permission_check_failed",))),
                        )
                        approval_failures.append("permission_check_failed")
                    if assessment is not None:
                        candidate = replace(candidate, provenance={
                            **candidate.provenance, "auto_approval": assessment.provenance(),
                        })
                    if assessment is not None and assessment.eligible:
                        evaluation = replace(
                            evaluation, decision=EvaluationDecision.ACCEPT,
                            confidence=max(evaluation.confidence, self.gate.threshold),
                            reasons=tuple(dict.fromkeys(evaluation.reasons + (
                                "official_source_signal", "auto_scope_approved",
                                "robots_allowed", "explicit_license_snapshot_match"))),
                            evidence=evaluation.evidence + (
                                f"auto_scope:{assessment.scope_id}",
                                f"terms_sha256:{assessment.terms_sha256}"),
                        )
                    elif assessment is not None:
                        # A search hint cannot override a failed permission check.
                        evaluation = replace(
                            evaluation, decision=(EvaluationDecision.REJECT
                                                  if evaluation.decision is EvaluationDecision.REJECT
                                                  else EvaluationDecision.REVIEW),
                            confidence=min(evaluation.confidence, 0.55),
                            reasons=tuple(dict.fromkeys(evaluation.reasons +
                                                     ("auto_approval_held:" + assessment.reason,))),
                        )
                        approval_failures.append(assessment.reason)
                status = {
                    EvaluationDecision.ACCEPT: SourceCandidateStatus.ACCEPTED,
                    EvaluationDecision.REVIEW: SourceCandidateStatus.REVIEW_REQUIRED,
                    EvaluationDecision.REJECT: SourceCandidateStatus.REJECTED,
                }[evaluation.decision]
                candidate = self.repository.save_candidate(replace(
                    candidate, status=status, evaluation_decision=evaluation.decision,
                    confidence=evaluation.confidence, reasons=evaluation.reasons,
                    evidence=evaluation.evidence,
                    collection_method=evaluation.suggested_method,
                    evaluated_at=evaluation.evaluated_at,
                ))
                if status is SourceCandidateStatus.REVIEW_REQUIRED:
                    review_count += 1
                elif status is SourceCandidateStatus.REJECTED:
                    rejected_count += 1
                # Multiple pages may be evidence, but one run must not register
                # multiple Source rows for a single host.
                source = None if candidate.domain in promoted_domains else self.gate.promote(candidate, evaluation)
                if source is not None:
                    promoted_domains.add(candidate.domain)
                    self.repository.save_candidate(replace(
                        candidate, status=SourceCandidateStatus.PROMOTED,
                        promoted_source_id=source.id, promoted_at=datetime.now(timezone.utc),
                    ))
                    promoted_ids.append(str(source.id))
                    if assessment is not None and assessment.eligible:
                        try:
                            self.auto_executor.activate(source, assessment)
                            approved_count += 1
                            if self.auto_executor.schedule_verifier is not None:
                                scheduled_count += 1
                        except Exception as error:
                            review_count += 1
                            approval_failures.append("activation_failed:" + type(error).__name__)
                            current = self.registry.repository.get(source.id)
                            if current is not None and source_is_collectible(current):
                                raise RuntimeError("auto approval failed with collectible Source") from error
                            # The audited Source remains inactive/held. Keep the
                            # registered Source link and record a review reason.
                            self.repository.save_candidate(replace(
                                candidate, status=SourceCandidateStatus.PROMOTED,
                                promoted_source_id=source.id,
                                promoted_at=datetime.now(timezone.utc),
                                provenance={**candidate.provenance, "auto_approval": {
                                    **assessment.provenance(), "activated": False,
                                    "failure": type(error).__name__,
                                }},
                            ))
        except Exception as error:
            if reservation_id is not None:
                uncertain = (isinstance(error, SearchProviderError) and
                             error.code in {"timeout", "network_failure"})
                raw_code = (error.code if isinstance(error, SearchProviderError)
                            else type(error).__name__)
                budget_code = re.sub(r"[^A-Z0-9_]", "_", raw_code.upper())[:80] or "SEARCH_FAILURE"
                self.reservations.finish(
                    reservation_id, status="UNKNOWN" if uncertain else "FAILED",
                    error_code=budget_code)
            self.repository.save_query(replace(
                query, status=QueryStatus.FAILED,
                completed_at=datetime.now(timezone.utc), result_count=result_count,
                candidate_count=len(candidate_ids), promoted_count=len(promoted_ids),
                error_summary=(error.code if isinstance(error, SearchProviderError)
                               else type(error).__name__),
            ))
            raise
        query = self.repository.save_query(replace(
            query, status=QueryStatus.SUCCEEDED,
            completed_at=datetime.now(timezone.utc), result_count=result_count,
            candidate_count=len(candidate_ids), promoted_count=len(promoted_ids),
        ))
        if reservation_id is not None:
            self.reservations.finish(reservation_id, status="SUCCEEDED")
        return DiscoveryOutcome(query, tuple(candidate_ids), tuple(promoted_ids),
                                approved_count, review_count, rejected_count,
                                scheduled_count, tuple(approval_failures))
