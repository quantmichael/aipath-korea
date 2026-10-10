"""Concrete Agent table repositories with an injected Supabase client.

Importing this module requires no credentials and performs no network access.
"""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from agent_collect.schemas.models import (
    CollectionMethod,
    CollectionRun,
    RawDocument,
    Source,
    SourceType,
)
from agent_collect.schemas.status import RunStatus, SourceStatus


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("database timestamp lacks timezone")
    return parsed


def _first(query: Any) -> dict[str, Any] | None:
    return next(iter(query.limit(1).execute().data or []), None)


class SupabaseSourceRepository:
    def __init__(self, client: Any) -> None:
        self.client = client

    def get(self, source_id: UUID) -> Source | None:
        row = _first(self.client.table("agent_sources").select("*").eq("id", str(source_id)))
        return self._from_row(row) if row else None

    def find_by_canonical_url(self, canonical_url: str) -> Source | None:
        row = _first(
            self.client.table("agent_sources").select("*").eq("canonical_url", canonical_url)
        )
        return self._from_row(row) if row else None

    def save(self, source: Source) -> Source:
        if source.canonical_url is None:
            raise ValueError("source requires canonical_url")
        previous = self.get(source.id)
        if previous is None and (source.status is not SourceStatus.CANDIDATE or source.is_active):
            raise ValueError("new sources must enter as inactive CANDIDATE")
        if previous is None and any(key in source.provenance for key in (
            "source_review", "approval", "approved_at", "reviewer", "review_reason", "activation"
        )):
            raise ValueError("new sources cannot contain review metadata")
        if previous is not None and (previous.status != source.status or
                                     previous.source_type != source.source_type or
                                     previous.is_active != source.is_active or
                                     previous.provenance.get("source_review") !=
                                     source.provenance.get("source_review") or
                                     (previous.status in {SourceStatus.APPROVED, SourceStatus.ACTIVE} and
                                      (previous.canonical_url != source.canonical_url or
                                       previous.confidence != source.confidence or
                                       previous.collection_method != source.collection_method or
                                       previous.collection_config != source.collection_config or
                                       previous.collection_interval_minutes != source.collection_interval_minutes))):
            raise ValueError("lifecycle changes require SourceReviewRepository")
        row = {
            "id": str(source.id),
            "name": source.name,
            "source_url": source.url,
            "canonical_url": source.canonical_url,
            "source_type": source.source_type.value,
            "collection_method": source.collection_method.value if source.collection_method else None,
            "status": source.status.value,
            "is_active": source.is_active,
            "category_hint": source.category_hint,
            "country_code": source.country_code,
            "language_code": source.language_code,
            "collection_interval_minutes": source.collection_interval_minutes,
            "discovery_method": source.discovery_method,
            "agent_rationale": source.agent_rationale,
            "last_collected_at": _iso(source.last_collected_at),
            "last_success_at": _iso(source.last_success_at),
            "last_failure_at": _iso(source.last_failure_at),
            "confidence": source.confidence,
            "provenance": source.provenance,
            "collection_config": source.collection_config,
            "discovered_at": _iso(source.discovered_at),
            "updated_at": _iso(datetime.now(timezone.utc)),
        }
        # INSERT and UPDATE are intentionally separate: an upsert executes the
        # INSERT trigger even when an existing APPROVED/ACTIVE row conflicts.
        if previous is None:
            self.client.table("agent_sources").insert(row).execute()
        else:
            self.client.table("agent_sources").update(row).eq("id", str(source.id)).execute()
        return source

    @staticmethod
    def _from_row(row: dict[str, Any]) -> Source:
        return Source(
            id=UUID(row["id"]),
            name=row["name"],
            url=row["source_url"],
            canonical_url=row["canonical_url"],
            source_type=SourceType(row["source_type"]),
            status=SourceStatus(row["status"]),
            collection_method=(CollectionMethod(row["collection_method"])
                               if row.get("collection_method") else None),
            is_active=row["is_active"],
            category_hint=row.get("category_hint"),
            country_code=row.get("country_code"),
            language_code=row.get("language_code"),
            collection_interval_minutes=row.get("collection_interval_minutes"),
            discovery_method=row.get("discovery_method"),
            agent_rationale=row.get("agent_rationale"),
            last_collected_at=_datetime(row.get("last_collected_at")),
            last_success_at=_datetime(row.get("last_success_at")),
            last_failure_at=_datetime(row.get("last_failure_at")),
            confidence=row.get("confidence"),
            provenance=row.get("provenance") or {},
            collection_config=row.get("collection_config") or {},
            discovered_at=_datetime(row["discovered_at"]),
        )


class SupabaseCollectionRunRepository:
    def __init__(self, client: Any) -> None:
        self.client = client

    def get(self, run_id: UUID) -> CollectionRun | None:
        row = _first(self.client.table("agent_collection_runs").select("*").eq("id", str(run_id)))
        return self._from_row(row) if row else None

    def save(self, run: CollectionRun) -> CollectionRun:
        row = {
            "id": str(run.id),
            "source_id": str(run.source_id),
            "status": run.status.value,
            "collection_method": run.collection_method.value if run.collection_method else None,
            "source_config_snapshot": run.source_config_snapshot,
            "started_at": _iso(run.started_at),
            "finished_at": _iso(run.finished_at),
            "found_count": run.found_count,
            "stored_count": run.stored_count,
            "failed_count": run.error_count,
            "error_summary": run.error_summary,
        }
        self.client.table("agent_collection_runs").upsert(row, on_conflict="id").execute()
        return run

    @staticmethod
    def _from_row(row: dict[str, Any]) -> CollectionRun:
        return CollectionRun(
            id=UUID(row["id"]), source_id=UUID(row["source_id"]),
            status=RunStatus(row["status"]), started_at=_datetime(row["started_at"]),
            finished_at=_datetime(row.get("finished_at")),
            collection_method=(CollectionMethod(row["collection_method"])
                               if row.get("collection_method") else None),
            found_count=row.get("found_count", 0), stored_count=row.get("stored_count", 0),
            error_count=row.get("failed_count", 0), error_summary=row.get("error_summary"),
            source_config_snapshot=row.get("source_config_snapshot") or {},
        )


class SupabaseRawDocumentRepository:
    def __init__(self, client: Any) -> None:
        self.client = client

    def get(self, document_id: UUID) -> RawDocument | None:
        row = _first(self.client.table("agent_raw_documents").select("*").eq("id", str(document_id)))
        return self._from_row(row) if row else None

    def save(self, document: RawDocument) -> RawDocument:
        row = {
            "id": str(document.id),
            "run_id": str(document.run_id),
            "source_id": str(document.source_id),
            "source_url": document.source_url,
            "canonical_url": document.canonical_url,
            "media_type": document.media_type,
            "content_ref": document.content_ref,
            "content_sha256": document.content_sha256,
            "http_status": document.http_status,
            "collected_at": _iso(document.collected_at),
            "provenance": document.provenance,
        }
        self.client.table("agent_raw_documents").upsert(row, on_conflict="id").execute()
        return document

    @staticmethod
    def _from_row(row: dict[str, Any]) -> RawDocument:
        return RawDocument(
            id=UUID(row["id"]), run_id=UUID(row["run_id"]),
            source_id=UUID(row["source_id"]), source_url=row["source_url"],
            canonical_url=row.get("canonical_url"),
            content_ref=row["content_ref"], content_sha256=row["content_sha256"],
            media_type=row.get("media_type"), http_status=row.get("http_status"),
            collected_at=_datetime(row["collected_at"]),
            provenance=row.get("provenance") or {},
        )
