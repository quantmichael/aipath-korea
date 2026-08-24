from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client

from collect.models import CollectionRunSummary, OpportunityCandidate, Source

load_dotenv()


def create_supabase_client() -> Client:
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_secret_key = os.environ.get("SUPABASE_SECRET_KEY")

    if not supabase_url or not supabase_secret_key:
        raise RuntimeError("Supabase 환경변수가 설정되지 않았습니다.")

    return create_client(supabase_url, supabase_secret_key)


def fetch_active_sources(
    supabase: Client,
    *,
    limit: int,
    method: str | None = None,
) -> list[Source]:
    query = (
        supabase.table("sources")
        .select(
            """
            id,
            name,
            homepage_url,
            collection_url,
            collection_method,
            source_type,
            last_collected_at
            """
        )
        .eq("is_active", True)
        .not_.is_("collection_url", "null")
        .order("last_collected_at", desc=False, nullsfirst=True)
        .limit(limit)
    )

    if method:
        query = query.eq("collection_method", method)

    rows = query.execute().data or []

    return [
        Source(
            id=row["id"],
            name=row["name"],
            homepage_url=row.get("homepage_url"),
            collection_url=row.get("collection_url"),
            collection_method=row.get("collection_method", "manual"),
            source_type=row.get("source_type", "regular"),
        )
        for row in rows
    ]


def fetch_category_map(supabase: Client) -> dict[str, str]:
    rows = (
        supabase.table("categories")
        .select("id, slug")
        .eq("is_active", True)
        .execute()
        .data
        or []
    )

    return {row["slug"]: row["id"] for row in rows}


def create_collection_run(
    supabase: Client,
    source: Source,
) -> str | None:
    row = {
        "source_id": source.id,
        "collection_method": source.collection_method,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    response = supabase.table("collection_runs").insert(row).execute()
    data = response.data or []
    return data[0]["id"] if data else None


def finish_collection_run(
    supabase: Client,
    run_id: str | None,
    summary: CollectionRunSummary,
) -> None:
    if not run_id:
        return

    supabase.table("collection_runs").update(
        {
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "status": summary.status,
            "found_count": summary.found_count,
            "inserted_count": summary.inserted_count,
            "updated_count": summary.updated_count,
            "duplicate_count": summary.duplicate_count,
            "failed_count": summary.failed_count,
            "error_message": summary.error_message,
        }
    ).eq("id", run_id).execute()


def update_source_collection_status(
    supabase: Client,
    source_id: str,
    *,
    status: str,
) -> None:
    supabase.table("sources").update(
        {
            "last_collected_at": datetime.now(timezone.utc).isoformat(),
            "last_collection_status": status,
        }
    ).eq("id", source_id).execute()


def find_existing_opportunity(
    supabase: Client,
    candidate: OpportunityCandidate,
) -> dict[str, Any] | None:
    if candidate.source_id and candidate.external_id:
        rows = (
            supabase.table("opportunities")
            .select("id, slug, official_url")
            .eq("source_id", candidate.source_id)
            .eq("external_id", candidate.external_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if rows:
            return rows[0]

    if candidate.official_url:
        rows = (
            supabase.table("opportunities")
            .select("id, slug, official_url")
            .eq("official_url", candidate.official_url)
            .limit(1)
            .execute()
            .data
            or []
        )
        if rows:
            return rows[0]

    if candidate.slug:
        rows = (
            supabase.table("opportunities")
            .select("id, slug, official_url")
            .eq("slug", candidate.slug)
            .limit(1)
            .execute()
            .data
            or []
        )
        if rows:
            return rows[0]

    return None


def upsert_candidate(
    supabase: Client,
    candidate: OpportunityCandidate,
    *,
    collection_run_id: str | None,
) -> tuple[str, str]:
    record = candidate.to_record(collection_run_id)

    existing_id = None

    if candidate.official_url:
        rows = (
            supabase.table("opportunity_candidates")
            .select("id")
            .eq("official_url", candidate.official_url)
            .limit(1)
            .execute()
            .data
            or []
        )

        if rows:
            existing_id = rows[0]["id"]

    if existing_id:
        response = (
            supabase.table("opportunity_candidates")
            .update(record)
            .eq("id", existing_id)
            .execute()
        )
        data = response.data or []
        return (data[0]["id"] if data else existing_id, "updated")

    response = supabase.table("opportunity_candidates").insert(record).execute()
    data = response.data or []
    return (data[0]["id"] if data else "", "inserted")


def fetch_promotable_candidates(
    supabase: Client,
    *,
    limit: int,
    candidate_id: str | None = None,
) -> list[dict[str, Any]]:
    query = (
        supabase.table("opportunity_candidates")
        .select("*")
        .eq("candidate_status", "verified")
        .order("discovered_at", desc=False)
        .limit(limit)
    )

    if candidate_id:
        query = query.eq("id", candidate_id)

    return query.execute().data or []


def find_existing_opportunity_for_record(
    supabase: Client,
    record: dict[str, Any],
) -> dict[str, Any] | None:
    candidate = OpportunityCandidate(
        source_id=record.get("source_id"),
        external_id=record.get("external_id"),
        official_url=record.get("official_url"),
        slug=record.get("slug"),
    )
    return find_existing_opportunity(supabase, candidate)


def upsert_draft_opportunity(
    supabase: Client,
    candidate: dict[str, Any],
) -> tuple[str, str]:
    existing = find_existing_opportunity_for_record(supabase, candidate)
    record = _candidate_to_opportunity_record(candidate)

    if existing:
        response = (
            supabase.table("opportunities")
            .update(record)
            .eq("id", existing["id"])
            .execute()
        )
        data = response.data or []
        return (data[0]["id"] if data else existing["id"], "updated")

    response = supabase.table("opportunities").insert(record).execute()
    data = response.data or []
    return (data[0]["id"] if data else "", "inserted")


def mark_candidate_promoted(
    supabase: Client,
    candidate_id: str,
) -> None:
    supabase.table("opportunity_candidates").update(
        {
            "candidate_status": "promoted",
        }
    ).eq("id", candidate_id).execute()


def _candidate_to_opportunity_record(candidate: dict[str, Any]) -> dict[str, Any]:
    title = candidate.get("title") or "제목 확인 필요"
    summary = candidate.get("summary") or "공식 원문 확인 후 요약이 필요합니다."
    organizer = candidate.get("organizer") or "공식 원문 확인 필요"

    return {
        "source_id": candidate.get("source_id"),
        "category_id": candidate.get("category_id"),
        "external_id": candidate.get("external_id"),
        "title": title,
        "slug": candidate.get("slug"),
        "summary": summary,
        "description": candidate.get("description"),
        "organizer": organizer,
        "target_audience": candidate.get("target_audience"),
        "difficulty": candidate.get("difficulty"),
        "format": candidate.get("format"),
        "region": candidate.get("region"),
        "venue": candidate.get("venue"),
        "price_type": candidate.get("price_type") or "unknown",
        "price_text": candidate.get("price_text"),
        "application_start_at": candidate.get("application_start_at"),
        "application_deadline_at": candidate.get("application_deadline_at"),
        "event_start_at": candidate.get("event_start_at"),
        "event_end_at": candidate.get("event_end_at"),
        "official_url": candidate.get("official_url"),
        "image_url": candidate.get("image_url"),
        "status": "draft",
        "is_featured": False,
        "published_at": None,
        "last_verified_at": candidate.get("last_verified_at"),
    }
