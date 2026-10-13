"""Bounded Supabase context, plan audit, and claimed plan execution adapters."""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from agent_collect.budget.reservations import BudgetAlreadyReserved, BudgetReservations
from agent_collect.discovery.goal import GoalDrivenDiscoveryPlanner, PlanningContext
from agent_collect.discovery.models import DiscoveryQuery, QueryStatus
from agent_collect.discovery.runner import DiscoveryRunner
from agent_collect.discovery.scheduling_supabase import SupabaseDiscoverySchedules
from agent_collect.repositories.discovery_supabase import SupabaseDiscoveryRepository
from agent_collect.schemas.models import Vertical


class SupabaseGoalContext:
    def __init__(self, client: Any) -> None:
        self.client = client

    def build(self, *, now: datetime | None = None) -> PlanningContext:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("context time must be timezone-aware")
        # Fail closed on any missing ledger. No untrusted snippets, raw HTML,
        # Source descriptions, or provider instructions are sent to OpenAI.
        usage = self.client.rpc("agent_provider_budget_usage", {"p_kind": "tavily"}).execute().data
        if not isinstance(usage, dict) or any(type(usage.get(k)) is not int for k in ("daily", "monthly")):
            raise RuntimeError("search budget usage unavailable")
        schedules = tuple(SupabaseDiscoverySchedules(self.client).list_all())
        since = now - timedelta(days=30)
        recent = tuple(SupabaseDiscoveryRepository(self.client).list_queries_since("tavily", since)[-100:])
        candidate_rows = (self.client.table("agent_source_candidates")
                          .select("status,query_id,promoted_source_id").order("created_at", desc=True)
                          .limit(100).execute().data or [])
        result_rows = (self.client.table("agent_search_results")
                       .select("query_id,filter_reasons,provider_metadata").order("created_at", desc=True)
                       .limit(100).execute().data or [])
        schedule_rows = (self.client.table("agent_source_schedules")
                         .select("source_id").order("updated_at", desc=True)
                         .limit(100).execute().data or [])
        source_rows = (self.client.table("agent_sources")
                       .select("canonical_url").order("created_at", desc=True)
                       .limit(100).execute().data or [])
        domains = tuple(dict.fromkeys(host for row in source_rows
                                      if row.get("canonical_url")
                                      for host in [urlsplit(row["canonical_url"]).hostname or ""]
                                      if re.fullmatch(r"[a-z0-9.-]{1,253}", host)))[:30]
        categories = {str(q.id): q.vertical_hint.value for q in recent}
        candidates = tuple({"status": str(row.get("status", "")),
                            "promoted_source_id": str(row.get("promoted_source_id") or ""),
                            "category": categories.get(str(row.get("query_id")), "unknown")}
                           for row in candidate_rows if isinstance(row, dict))
        filters = tuple(tuple(str(reason) for reason in row.get("filter_reasons") or [])
                        for row in result_rows if isinstance(row, dict))
        quality_rows = []
        for row in result_rows:
            if not isinstance(row, dict):
                continue
            metadata = row.get("provider_metadata")
            audit = metadata.get("quality") if isinstance(metadata, dict) else None
            audit = audit if isinstance(audit, dict) else {}
            quality_rows.append({
                "category": categories.get(str(row.get("query_id")), "unknown"),
                "classification": audit.get("classification"),
                "official_source": audit.get("official_source"),
                "individual_opportunity": audit.get("individual_opportunity"),
                "confidence": audit.get("confidence"),
                "verified_new_opportunity": audit.get("verified_new_opportunity"),
                "evidence_verified": audit.get("evidence_verified"),
            })
        quality = tuple(quality_rows)
        connected = tuple(str(row["source_id"]) for row in schedule_rows
                          if isinstance(row, dict) and row.get("source_id"))
        failures = tuple(str(row.error_summary)[:80] for row in recent
                         if row.status is QueryStatus.FAILED and row.error_summary)[:20]
        return PlanningContext(now, recent, schedules, candidates, domains, failures,
                               usage["daily"], usage["monthly"], filters, connected, quality)


class SupabaseGoalPlans:
    def __init__(self, client: Any) -> None:
        self.client = client

    def start(self, *, day: str, reservation_id: UUID, model: str,
              context_digest: str) -> UUID:
        result = self.client.rpc("agent_start_ai_plan", {
            "p_day": day, "p_reservation_id": str(reservation_id),
            "p_model": model, "p_context_digest": context_digest,
        }).execute().data
        if not isinstance(result, str):
            raise RuntimeError("AI plan start unconfirmed")
        return UUID(result)

    def finish(self, plan_id: UUID, *, queries: list[dict[str, Any]],
               error_code: str | None = None,
               blocked_reasons: tuple[str, ...] = ()) -> None:
        result = self.client.rpc("agent_finish_ai_plan", {
            "p_plan_id": str(plan_id), "p_queries": queries,
            "p_error_code": error_code,
            "p_blocked_reasons": list(blocked_reasons),
        }).execute().data
        if result is not True:
            raise RuntimeError("AI plan completion unconfirmed")

    def claim_query(self, claim_id: UUID) -> dict[str, Any] | None:
        rows = self.client.rpc("agent_claim_ai_plan_query", {
            "p_claim_id": str(claim_id),
        }).execute().data or []
        return rows[0] if rows else None

    def claim_query_exact(self, plan_id: UUID, claim_id: UUID) -> dict[str, Any] | None:
        rows = self.client.rpc("agent_claim_ai_plan_query_exact", {
            "p_plan_id": str(plan_id), "p_claim_id": str(claim_id),
        }).execute().data or []
        if len(rows) > 1:
            raise RuntimeError("exact plan claim returned multiple queries")
        return rows[0] if rows else None

    def finish_query(self, claim_id: UUID, *, status: str,
                     query_id: UUID | None = None) -> None:
        result = self.client.rpc("agent_finish_ai_plan_query", {
            "p_claim_id": str(claim_id), "p_status": status,
            "p_query_id": str(query_id) if query_id else None,
        }).execute().data
        if result is not True:
            raise RuntimeError("AI plan query completion unconfirmed")


class GoalPlanningWorker:
    """At most one billable plan per KST day; no search is performed here."""

    def __init__(self, *, context: SupabaseGoalContext, plans: SupabaseGoalPlans,
                 budget: BudgetReservations, planner: GoalDrivenDiscoveryPlanner,
                 enabled: bool) -> None:
        self.context, self.plans, self.budget, self.planner = context, plans, budget, planner
        self.enabled = enabled

    def run_once(self, *, dry_run: bool = False) -> dict[str, Any]:
        if not self.enabled or dry_run:
            return {"status": "DISABLED" if not self.enabled else "DRY_RUN", "planned_query_count": 0}
        context = self.context.build()
        day = context.at.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
        data = context.prompt_data()
        digest = hashlib.sha256(json.dumps(data, sort_keys=True,
                                           ensure_ascii=False).encode()).hexdigest()
        if not self.planner.provider.credential_present:
            raise RuntimeError("planner credential missing")
        try:
            reservation_id = self.budget.reserve(kind="openai", scope="planner",
                                                 key=f"planner:{day}")
        except BudgetAlreadyReserved:
            return {"status": "ALREADY_ATTEMPTED", "planned_query_count": 0}
        # Start record and dispatch must both commit before the external call.
        self.budget.dispatch(reservation_id)
        plan_id = self.plans.start(day=day, reservation_id=reservation_id,
                                   model=self.planner.provider.model, context_digest=digest)
        try:
            payload = self.planner.provider.generate(data)
        except Exception as error:
            code = type(error).__name__.upper()[:80]
            uncertain = "TIMEOUT" in code or "CONNECTION" in code
            self.budget.finish(reservation_id,
                               status="UNKNOWN" if uncertain else "FAILED", error_code=code)
            self.plans.finish(plan_id, queries=[], error_code=code)
            raise
        self.budget.finish(reservation_id, status="SUCCEEDED")
        try:
            plan = self.planner.plan(context, payload)
        except Exception:
            self.plans.finish(plan_id, queries=[], error_code="INVALID_PLAN")
            raise
        if not plan.queries:
            # A valid but fully blocked plan is auditable and has no search.
            self.plans.finish(plan_id, queries=[], error_code="NO_SAFE_QUERIES",
                              blocked_reasons=plan.blocked_reasons)
            return {"status": "HELD", "planned_query_count": 0,
                    "blocked_count": len(plan.blocked_reasons)}
        self.plans.finish(plan_id, queries=[row.as_record() for row in plan.queries],
                          blocked_reasons=plan.blocked_reasons)
        return {"status": "READY", "planned_query_count": len(plan.queries),
                "blocked_count": len(plan.blocked_reasons), "plan_id": str(plan_id)}


class GoalExecutionWorker:
    """Claims one stored plan item and invokes the existing DiscoveryRunner."""

    def __init__(self, *, plans: SupabaseGoalPlans, runner: DiscoveryRunner,
                 enabled: bool, plan_id: UUID | None = None) -> None:
        self.plans, self.runner, self.enabled = plans, runner, enabled
        self.plan_id = plan_id

    def run_once(self, *, dry_run: bool = False) -> dict[str, Any]:
        if not self.enabled or dry_run:
            return {"status": "DISABLED" if not self.enabled else "DRY_RUN", "executed_query_count": 0}
        claim_id = uuid4()
        row = (self.plans.claim_query_exact(self.plan_id, claim_id)
               if self.plan_id is not None else self.plans.claim_query(claim_id))
        if row is None:
            return {"status": "EMPTY", "executed_query_count": 0}
        if self.plan_id is not None and row.get("plan_id") != str(self.plan_id):
            raise RuntimeError("exact plan claim returned a different plan")
        query = DiscoveryQuery(
            id=UUID(row["id"]), text=row["query_text"], provider="tavily",
            created_at=datetime.now(timezone.utc),
            vertical_hint=Vertical(row["vertical_hint"]),
            provenance={"ai_plan_id": row["plan_id"], "ai_plan_claim_id": str(claim_id),
                        "new_category": row.get("new_category")},
        )
        try:
            outcome = self.runner.run(query)
            succeeded = outcome.query.status is QueryStatus.SUCCEEDED
            self.plans.finish_query(claim_id, status="SUCCEEDED" if succeeded else "FAILED",
                                    query_id=query.id)
            return {"status": "SUCCEEDED" if succeeded else "HELD", "executed_query_count": int(succeeded),
                    "query_id": str(query.id), "candidate_count": len(outcome.candidate_ids)}
        except Exception:
            try:
                persisted = self.runner.repository.get_query(query.id)
            except Exception:
                persisted = None
            self.plans.finish_query(claim_id, status="FAILED",
                                    query_id=query.id if persisted else None)
            raise
