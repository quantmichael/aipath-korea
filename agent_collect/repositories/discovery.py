"""Discovery persistence port and in-memory implementation."""

from typing import Protocol
from datetime import datetime
from uuid import UUID

from agent_collect.discovery.models import DiscoveryQuery, SearchResult, SourceCandidate


class DiscoveryRepository(Protocol):
    def save_query(self, query: DiscoveryQuery) -> DiscoveryQuery: ...
    def get_query(self, query_id: UUID) -> DiscoveryQuery | None: ...
    def list_queries_since(self, provider: str, since: datetime) -> list[DiscoveryQuery]: ...
    def save_result(self, result: SearchResult) -> SearchResult: ...
    def get_result(self, result_id: UUID) -> SearchResult | None: ...
    def save_candidate(self, candidate: SourceCandidate) -> SourceCandidate: ...
    def get_candidate(self, candidate_id: UUID) -> SourceCandidate | None: ...
    def find_candidate_by_canonical_url(self, canonical_url: str) -> SourceCandidate | None: ...


class InMemoryDiscoveryRepository:
    def __init__(self) -> None:
        self.queries: dict[UUID, DiscoveryQuery] = {}
        self.results: dict[UUID, SearchResult] = {}
        self.candidates: dict[UUID, SourceCandidate] = {}

    def save_query(self, query: DiscoveryQuery) -> DiscoveryQuery:
        self.queries[query.id] = query
        return query

    def get_query(self, query_id: UUID) -> DiscoveryQuery | None:
        return self.queries.get(query_id)

    def list_queries_since(self, provider: str, since: datetime) -> list[DiscoveryQuery]:
        return [row for row in self.queries.values()
                if row.provider == provider and row.created_at >= since]

    def save_result(self, result: SearchResult) -> SearchResult:
        if result.query_id not in self.queries:
            raise ValueError("result requires persisted query")
        self.results[result.id] = result
        return result

    def get_result(self, result_id: UUID) -> SearchResult | None:
        return self.results.get(result_id)

    def save_candidate(self, candidate: SourceCandidate) -> SourceCandidate:
        result = self.results.get(candidate.search_result_id)
        if result is None or result.query_id != candidate.query_id:
            raise ValueError("candidate requires matching persisted search result")
        existing = self.find_candidate_by_canonical_url(candidate.canonical_url)
        if existing is not None and existing.id != candidate.id:
            raise ValueError("canonical candidate URL already exists")
        self.candidates[candidate.id] = candidate
        return candidate

    def get_candidate(self, candidate_id: UUID) -> SourceCandidate | None:
        return self.candidates.get(candidate_id)

    def find_candidate_by_canonical_url(self, canonical_url: str) -> SourceCandidate | None:
        return next((row for row in self.candidates.values()
                     if row.canonical_url == canonical_url), None)
