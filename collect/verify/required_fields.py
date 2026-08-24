from __future__ import annotations

from collect.models import OpportunityCandidate


def validate_candidate(candidate: OpportunityCandidate) -> list[str]:
    errors: list[str] = []

    if not candidate.title:
        errors.append("title is required")

    if not candidate.source_id:
        errors.append("source_id is required")

    if not candidate.official_url:
        errors.append("official_url is required")

    if not candidate.category_id:
        errors.append("category_id could not be determined")

    if not candidate.slug:
        errors.append("slug is required")

    return errors
