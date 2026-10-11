from __future__ import annotations

from collect.models import PromotionResult
from collect.repository import (
    create_supabase_client,
    fetch_promotable_candidates,
    mark_candidate_promoted,
    upsert_draft_opportunity,
)
from opportunity_status.evidence import build_collect_evidence


def promote_candidates(
    *,
    limit: int = 10,
    candidate_id: str | None = None,
) -> PromotionResult:
    supabase = create_supabase_client()
    candidates = fetch_promotable_candidates(
        supabase,
        limit=limit,
        candidate_id=candidate_id,
    )
    result = PromotionResult(selected_count=len(candidates))

    for candidate in candidates:
        try:
            missing = _missing_required_fields(candidate)

            if missing:
                result.skipped_count += 1
                result.errors.append(
                    f"{candidate['id']}: missing {', '.join(missing)}"
                )
                continue

            opportunity_id, disposition = upsert_draft_opportunity(supabase, candidate)
            if not opportunity_id:
                raise RuntimeError("Opportunity write returned no ID")
            if disposition == "protected_existing":
                result.skipped_count += 1
                result.errors.append(
                    f"{candidate['id']}: existing non-draft Opportunity protected"
                )
                continue
            supabase.rpc(
                "record_opportunity_status_evidence",
                {"p_opportunity_id": opportunity_id,
                 "p_evidence": build_collect_evidence(candidate)},
            ).execute()
            mark_candidate_promoted(supabase, candidate["id"])
            result.promoted_count += 1
            result.promoted_candidates.append(candidate["id"])

        except Exception as error:
            result.failed_count += 1
            result.errors.append(f"{candidate.get('id')}: {error}")

    return result


def _missing_required_fields(candidate: dict) -> list[str]:
    required_fields = [
        "source_id",
        "category_id",
        "title",
        "slug",
        "official_url",
    ]

    return [
        field
        for field in required_fields
        if not candidate.get(field)
    ]
