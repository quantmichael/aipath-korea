"""Explicit Extract → Normalize boundary over an Agent Candidate."""

from datetime import datetime, timezone
from uuid import UUID

from agent_collect.normalization.engine import normalize_extracted_payload
from agent_collect.repositories.normalization import NormalizationRepository, NormalizationState


class NormalizationRunner:
    def __init__(self, repository: NormalizationRepository) -> None:
        self.repository = repository

    def run(self, candidate_id: UUID) -> NormalizationState:
        state = self.repository.get(candidate_id)
        if state is None:
            raise KeyError("candidate not found")
        result = normalize_extracted_payload(state.extraction.payload)
        if state.normalized_payload:
            if (state.normalized_payload != result.payload or
                    (state.extraction.provenance.get("normalization") or {}).get("input_sha256")
                    != result.input_sha256):
                raise ValueError("existing normalization differs")
            return state
        audit = {
            "version": result.payload["normalization_version"],
            "method": "deterministic",
            "input_sha256": result.input_sha256,
            "normalized_at": datetime.now(timezone.utc).isoformat(),
            "status": result.payload["status"],
            "reason_codes": result.payload["reason_codes"],
        }
        return self.repository.save(candidate_id, extracted_payload=state.extraction.payload,
                                    normalized_payload=result.payload, audit=audit)
