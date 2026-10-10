"""Agent Candidate normalization storage contract."""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from agent_collect.extraction.schema import StoredExtraction


@dataclass(frozen=True, slots=True)
class NormalizationState:
    extraction: StoredExtraction
    normalized_payload: dict[str, Any]
    updated_at: str | None = None


class NormalizationRepository(Protocol):
    def get(self, candidate_id: UUID) -> NormalizationState | None: ...
    def save(self, candidate_id: UUID, *, extracted_payload: dict[str, Any],
             normalized_payload: dict[str, Any], audit: dict[str, Any]) -> NormalizationState: ...


class InMemoryNormalizationRepository:
    def __init__(self, rows: list[StoredExtraction] | None = None) -> None:
        self.rows = {row.id: NormalizationState(row, {}) for row in rows or []}

    def get(self, candidate_id: UUID) -> NormalizationState | None:
        return self.rows.get(candidate_id)

    def save(self, candidate_id: UUID, *, extracted_payload: dict[str, Any],
             normalized_payload: dict[str, Any], audit: dict[str, Any]) -> NormalizationState:
        state = self.rows[candidate_id]
        if state.extraction.payload != extracted_payload:
            raise ValueError("extracted_payload changed during normalization")
        if state.normalized_payload and state.normalized_payload != normalized_payload:
            raise ValueError("existing normalization differs")
        if state.normalized_payload:
            return state
        from dataclasses import replace
        stored = replace(state.extraction, provenance={**state.extraction.provenance,
                                                        "normalization": audit})
        updated = NormalizationState(stored, normalized_payload, state.updated_at)
        self.rows[candidate_id] = updated
        return updated
