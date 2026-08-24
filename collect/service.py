from __future__ import annotations

import os
from datetime import datetime, timezone

from collect.collectors.html import collect_html_source
from collect.models import CollectionResult, CollectionRunSummary, Source
from collect.normalize.category import infer_category_slug
from collect.normalize.opportunity import normalize_candidate
from collect.repository import (
    create_collection_run,
    create_supabase_client,
    fetch_active_sources,
    fetch_category_map,
    finish_collection_run,
    update_source_collection_status,
    upsert_candidate,
)
from collect.verify.duplicate import mark_duplicate_if_needed
from collect.verify.validation import apply_validation


def run_collection(
    *,
    max_sources: int | None = None,
    max_candidates_per_source: int | None = None,
    method: str | None = None,
) -> CollectionResult:
    supabase = create_supabase_client()
    max_sources = max_sources or int(os.environ.get("MAX_SOURCES_PER_RUN", "3"))
    max_candidates_per_source = max_candidates_per_source or int(
        os.environ.get("MAX_CANDIDATES_PER_SOURCE", "5")
    )

    sources = fetch_active_sources(
        supabase,
        limit=max_sources,
        method=method,
    )
    category_map = fetch_category_map(supabase)
    result = CollectionResult()

    for source in sources:
        summary = _run_source(
            supabase=supabase,
            source=source,
            category_map=category_map,
            max_candidates=max_candidates_per_source,
        )
        result.runs.append(summary)

    return result


def _run_source(
    *,
    supabase,
    source: Source,
    category_map: dict[str, str],
    max_candidates: int,
) -> CollectionRunSummary:
    summary = CollectionRunSummary(
        source_id=source.id,
        collection_method=source.collection_method,
    )
    run_id: str | None = None

    try:
        run_id = create_collection_run(supabase, source)

        candidates = _collect_candidates(
            source,
            max_candidates=max_candidates,
        )
        summary.found_count = len(candidates)

        for candidate in candidates:
            candidate = normalize_candidate(candidate)
            candidate.category_slug = (
                candidate.category_slug or infer_category_slug(candidate)
            )

            if candidate.category_slug:
                candidate.category_id = category_map.get(candidate.category_slug)

            if mark_duplicate_if_needed(supabase, candidate):
                summary.duplicate_count += 1

            candidate = apply_validation(candidate)

            _, save_action = upsert_candidate(
                supabase,
                candidate,
                collection_run_id=run_id,
            )

            if candidate.candidate_status == "duplicate":
                continue

            if candidate.validation_errors:
                summary.failed_count += 1
            elif save_action == "updated":
                summary.updated_count += 1
            else:
                summary.inserted_count += 1

        summary.status = _status_from_summary(summary)

    except Exception as error:
        summary.status = "failed"
        summary.error_message = str(error)
        summary.failed_count = max(summary.failed_count, 1)

    finally:
        summary.finished_at = datetime.now(timezone.utc)
        finish_collection_run(supabase, run_id, summary)
        update_source_collection_status(
            supabase,
            source.id,
            status=summary.status,
        )

    return summary


def _collect_candidates(
    source: Source,
    *,
    max_candidates: int,
):
    if source.collection_method == "html":
        return collect_html_source(
            source,
            max_candidates=max_candidates,
        )

    # Manual/API/RSS/AI collectors are intentionally not faked. They can be
    # plugged into this dispatch table as each source is validated.
    return []


def _status_from_summary(summary: CollectionRunSummary) -> str:
    if summary.error_message:
        return "failed"

    if summary.failed_count:
        return "partial"

    return "success"
