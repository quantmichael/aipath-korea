from __future__ import annotations

from collect.models import OpportunityCandidate
from collect.verify.required_fields import validate_candidate


def apply_validation(candidate: OpportunityCandidate) -> OpportunityCandidate:
    errors = validate_candidate(candidate)
    candidate.validation_errors.extend(errors)

    if candidate.candidate_status != "duplicate":
        candidate.candidate_status = "needs_review" if errors else "verified"

    return candidate
