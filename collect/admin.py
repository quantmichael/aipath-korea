from __future__ import annotations

from collect.models import PromotionResult
from collect.repository import (
    create_supabase_client,
    fetch_candidate_review_rows,
    fetch_candidates_by_ids,
    mark_candidate_promoted,
    publish_opportunity,
    update_candidate_statuses,
    upsert_draft_opportunity,
)


def list_review_candidates(
    *,
    limit: int = 100,
    candidate_status: str | None = None,
    source_id: str | None = None,
) -> dict:
    supabase = create_supabase_client()
    candidates = fetch_candidate_review_rows(
        supabase,
        limit=limit,
        candidate_status=candidate_status,
        source_id=source_id,
    )

    counts: dict[str, int] = {}
    for candidate in candidates:
        status = candidate.get("candidate_status") or "unknown"
        counts[status] = counts.get(status, 0) + 1

    return {
        "count": len(candidates),
        "counts": counts,
        "candidates": candidates,
    }


def apply_candidate_action(
    *,
    candidate_ids: list[str],
    action: str,
) -> dict:
    supabase = create_supabase_client()
    candidate_ids = _unique_ids(candidate_ids)

    if action == "reject":
        updated_count = update_candidate_statuses(
            supabase,
            candidate_ids,
            candidate_status="rejected",
        )
        return {"action": action, "updated_count": updated_count, "errors": []}

    if action == "hold":
        updated_count = update_candidate_statuses(
            supabase,
            candidate_ids,
            candidate_status="needs_review",
        )
        return {"action": action, "updated_count": updated_count, "errors": []}

    if action == "verify":
        updated_count = update_candidate_statuses(
            supabase,
            candidate_ids,
            candidate_status="verified",
        )
        return {"action": action, "updated_count": updated_count, "errors": []}

    if action == "publish":
        result = _publish_candidates(supabase, candidate_ids)
        return {"action": action, **result.to_dict()}

    return {
        "action": action,
        "updated_count": 0,
        "errors": [f"unsupported action: {action}"],
    }


def _publish_candidates(supabase, candidate_ids: list[str]) -> PromotionResult:
    candidates = fetch_candidates_by_ids(supabase, candidate_ids)
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

            opportunity_id, _ = upsert_draft_opportunity(supabase, candidate)
            if not opportunity_id:
                result.failed_count += 1
                result.errors.append(f"{candidate['id']}: opportunity save failed")
                continue

            publish_opportunity(supabase, opportunity_id)
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


def _unique_ids(candidate_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []

    for candidate_id in candidate_ids:
        if candidate_id in seen:
            continue

        seen.add(candidate_id)
        unique.append(candidate_id)

    return unique
