from __future__ import annotations

from supabase import Client

from collect.models import OpportunityCandidate
from collect.repository import find_existing_opportunity


def mark_duplicate_if_needed(
    supabase: Client,
    candidate: OpportunityCandidate,
) -> bool:
    existing = find_existing_opportunity(supabase, candidate)

    if not existing:
        return False

    candidate.candidate_status = "duplicate"
    candidate.validation_errors.append(
        f"duplicate opportunity exists: {existing.get('slug')}"
    )
    return True
