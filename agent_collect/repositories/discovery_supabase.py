"""Supabase row mapping for Agent-only Discovery tables; injected client."""

from datetime import datetime
from typing import Any
from uuid import UUID

from agent_collect.discovery.models import (
    DiscoveryQuery, EvaluationDecision, QueryStatus, SearchResult,
    SearchResultStatus, SourceCandidate, SourceCandidateStatus,
)
from agent_collect.schemas.models import CollectionMethod, Vertical


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _one(query: Any) -> dict[str, Any] | None:
    return next(iter(query.limit(1).execute().data or []), None)


class SupabaseDiscoveryRepository:
    def __init__(self, client: Any) -> None:
        self.client = client

    def save_query(self, query: DiscoveryQuery) -> DiscoveryQuery:
        self.client.table("agent_discovery_queries").upsert({
            "id": str(query.id), "query_text": query.text, "provider": query.provider,
            "vertical_hint": query.vertical_hint.value, "status": query.status.value,
            "created_at": _iso(query.created_at), "started_at": _iso(query.started_at),
            "completed_at": _iso(query.completed_at), "result_count": query.result_count,
            "candidate_count": query.candidate_count, "promoted_count": query.promoted_count,
            "error_summary": query.error_summary, "provenance": query.provenance,
            **({"schedule_id": str(query.schedule_id),
                "schedule_window_id": str(query.schedule_window_id),
                "schedule_claim_id": str(query.schedule_claim_id),
                "schedule_worker_id": query.schedule_worker_id} if query.schedule_id else {}),
        }, on_conflict="id").execute()
        return query

    def get_query(self, query_id: UUID) -> DiscoveryQuery | None:
        row = _one(self.client.table("agent_discovery_queries").select("*").eq("id", str(query_id)))
        return self._query(row) if row else None

    def list_queries_since(self, provider: str, since: datetime) -> list[DiscoveryQuery]:
        # PostgREST defaults to a 1,000-row page. Paginate so budget checks never
        # silently undercount as usage grows.
        rows: list[DiscoveryQuery] = []
        offset = 0
        while True:
            page = (self.client.table("agent_discovery_queries").select("*")
                    .eq("provider", provider).gte("created_at", _iso(since))
                    .order("created_at").order("id").range(offset, offset + 999)
                    .execute().data or [])
            rows.extend(self._query(row) for row in page)
            if len(page) < 1000:
                return rows
            offset += len(page)

    @staticmethod
    def _query(row: dict[str, Any]) -> DiscoveryQuery:
        return DiscoveryQuery(
            id=UUID(row["id"]), text=row["query_text"], provider=row["provider"],
            vertical_hint=Vertical(row["vertical_hint"]), status=QueryStatus(row["status"]),
            created_at=_dt(row["created_at"]), started_at=_dt(row.get("started_at")),
            completed_at=_dt(row.get("completed_at")), result_count=row["result_count"],
            candidate_count=row["candidate_count"], promoted_count=row["promoted_count"],
            error_summary=row.get("error_summary"), provenance=row.get("provenance") or {},
            schedule_id=UUID(row["schedule_id"]) if row.get("schedule_id") else None,
            schedule_window_id=UUID(row["schedule_window_id"]) if row.get("schedule_window_id") else None,
            schedule_claim_id=UUID(row["schedule_claim_id"]) if row.get("schedule_claim_id") else None,
            schedule_worker_id=row.get("schedule_worker_id"),
        )

    def save_result(self, result: SearchResult) -> SearchResult:
        self.client.table("agent_search_results").upsert({
            "id": str(result.id), "query_id": str(result.query_id), "provider": result.provider,
            "rank": result.rank, "title": result.title, "original_url": result.original_url,
            "snippet": result.snippet, "canonical_url": result.canonical_url,
            "domain": result.domain, "discovered_at": _iso(result.discovered_at),
            "status": result.status.value, "filter_reasons": list(result.filter_reasons),
            "provider_metadata": result.provider_metadata,
        }, on_conflict="id").execute()
        return result

    def get_result(self, result_id: UUID) -> SearchResult | None:
        row = _one(self.client.table("agent_search_results").select("*").eq("id", str(result_id)))
        return SearchResult(
            id=UUID(row["id"]), query_id=UUID(row["query_id"]), provider=row["provider"],
            rank=row["rank"], title=row["title"], original_url=row["original_url"],
            snippet=row["snippet"], canonical_url=row.get("canonical_url"),
            domain=row.get("domain"), discovered_at=_dt(row["discovered_at"]),
            status=SearchResultStatus(row["status"]),
            filter_reasons=tuple(row.get("filter_reasons") or ()),
            provider_metadata=row.get("provider_metadata") or {},
        ) if row else None

    def has_prior_result_url(self, canonical_url: str, result_id: UUID) -> bool:
        return _one(self.client.table("agent_search_results").select("id")
                    .eq("canonical_url", canonical_url).neq("id", str(result_id))) is not None

    def save_candidate(self, candidate: SourceCandidate) -> SourceCandidate:
        self.client.table("agent_source_candidates").upsert({
            "id": str(candidate.id), "query_id": str(candidate.query_id),
            "search_result_id": str(candidate.search_result_id),
            "original_url": candidate.original_url, "candidate_url": candidate.candidate_url,
            "canonical_url": candidate.canonical_url, "domain": candidate.domain,
            "title": candidate.title, "snippet": candidate.snippet,
            "discovered_at": _iso(candidate.discovered_at), "status": candidate.status.value,
            "evaluation_decision": candidate.evaluation_decision.value if candidate.evaluation_decision else None,
            "confidence": candidate.confidence, "reasons": list(candidate.reasons),
            "evidence": list(candidate.evidence),
            "collection_method": candidate.collection_method.value if candidate.collection_method else None,
            "evaluated_at": _iso(candidate.evaluated_at),
            "promoted_source_id": str(candidate.promoted_source_id) if candidate.promoted_source_id else None,
            "promoted_at": _iso(candidate.promoted_at), "provenance": candidate.provenance,
        }, on_conflict="id").execute()
        return candidate

    def get_candidate(self, candidate_id: UUID) -> SourceCandidate | None:
        row = _one(self.client.table("agent_source_candidates").select("*").eq("id", str(candidate_id)))
        return self._candidate(row) if row else None

    def find_candidate_by_canonical_url(self, canonical_url: str) -> SourceCandidate | None:
        row = _one(self.client.table("agent_source_candidates").select("*").eq("canonical_url", canonical_url))
        return self._candidate(row) if row else None

    @staticmethod
    def _candidate(row: dict[str, Any]) -> SourceCandidate:
        return SourceCandidate(
            id=UUID(row["id"]), query_id=UUID(row["query_id"]),
            search_result_id=UUID(row["search_result_id"]), original_url=row["original_url"],
            candidate_url=row["candidate_url"], canonical_url=row["canonical_url"],
            domain=row["domain"], title=row["title"], snippet=row["snippet"],
            discovered_at=_dt(row["discovered_at"]), status=SourceCandidateStatus(row["status"]),
            evaluation_decision=(EvaluationDecision(row["evaluation_decision"])
                                 if row.get("evaluation_decision") else None),
            confidence=row.get("confidence"), reasons=tuple(row.get("reasons") or ()),
            evidence=tuple(row.get("evidence") or ()),
            collection_method=(CollectionMethod(row["collection_method"])
                               if row.get("collection_method") else None),
            evaluated_at=_dt(row.get("evaluated_at")),
            promoted_source_id=(UUID(row["promoted_source_id"])
                                if row.get("promoted_source_id") else None),
            promoted_at=_dt(row.get("promoted_at")), provenance=row.get("provenance") or {},
        )
