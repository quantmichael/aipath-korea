"""Application service: Source → Run → Collect → Storage → RawDocument."""

import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Mapping
from uuid import UUID, uuid4

from agent_collect.collectors.contracts import CollectionRequest, CollectionResult
from agent_collect.collectors.planner import CollectionPlanner
from agent_collect.config.settings import AgentSettings
from agent_collect.registry.service import SourceRegistryService, canonicalize_url
from agent_collect.repositories.contracts import (
    CollectionRunRepository,
    RawDocumentRepository,
    SourceRepository,
)
from agent_collect.schemas.models import CollectionRun, RawDocument
from agent_collect.schemas.status import RunStatus, SourceStatus
from agent_collect.registry.approval import source_is_collectible
from agent_collect.storage.raw import RawContentStorage


@dataclass(frozen=True, slots=True)
class RunOutcome:
    run: CollectionRun
    raw_document: RawDocument | None
    collection_result: CollectionResult | None
    error: str | None

    @property
    def succeeded(self) -> bool:
        return self.run.status is RunStatus.SUCCEEDED


class CollectionRunner:
    def __init__(
        self,
        *,
        sources: SourceRepository,
        runs: CollectionRunRepository,
        documents: RawDocumentRepository,
        storage: RawContentStorage,
        planner: CollectionPlanner,
        settings: AgentSettings,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.sources = sources
        self.runs = runs
        self.documents = documents
        self.storage = storage
        self.planner = planner
        self.settings = settings
        self.environ = os.environ if environ is None else environ
        self.registry = SourceRegistryService(sources)

    def run_source(
        self, source_id: UUID, *, trigger: str = "manual",
        worker_id: str | None = None, claim_id: UUID | None = None,
        schedule_window_id: UUID | None = None,
    ) -> RunOutcome:
        if trigger not in {"manual", "scheduled"}:
            raise ValueError("invalid collection trigger")
        if trigger == "scheduled" and (not worker_id or claim_id is None or schedule_window_id is None):
            raise ValueError("scheduled collection requires worker, claim and window ids")
        if trigger == "manual" and any(value is not None for value in
                                       (worker_id, claim_id, schedule_window_id)):
            raise ValueError("manual collection cannot carry a schedule claim")
        source = self.sources.get(source_id)
        if source is None:
            raise KeyError(f"unknown source: {source_id}")
        run = CollectionRun(
            id=uuid4(), source_id=source.id, status=RunStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
            collection_method=source.collection_method,
            source_config_snapshot={
                "request_url": source.collection_config.get("request_url", source.url),
                "header_names": sorted(source.collection_config.get("headers", {})),
                "query_param_names": sorted(source.collection_config.get("query_params", {})),
                **({"trigger": "scheduled", "worker_id": worker_id,
                    "claim_id": str(claim_id), "schedule_window_id": str(schedule_window_id)}
                   if trigger == "scheduled" else {}),
            },
        )
        self.runs.save(run)
        result: CollectionResult | None = None
        raw: RawDocument | None = None
        stored_ref: str | None = None
        raw_persisted = False
        try:
            if not source_is_collectible(source):
                raise ValueError("source lacks reviewed ACTIVE approval")
            collector = self.planner.plan(source)
            config = source.collection_config
            headers = dict(config.get("headers", {}))
            for name, variable in config.get("header_env", {}).items():
                value = self.environ.get(variable)
                if not value:
                    raise ValueError(f"missing header environment variable: {variable}")
                headers[name] = value
            request = CollectionRequest(
                requested_url=str(config.get("request_url", source.url)),
                timeout_seconds=self.settings.collection_timeout_seconds,
                max_response_bytes=self.settings.max_response_bytes,
                max_redirects=self.settings.max_redirects,
                max_retries=self.settings.max_retries,
                retry_backoff_seconds=self.settings.retry_backoff_seconds,
                user_agent=self.settings.user_agent,
                headers=headers,
                query_params=dict(config.get("query_params", {})),
            )
            result = collector.collect(source, request)
            if result.error is not None:
                raise ValueError(f"{result.error.code}: {result.error.message}")
            receipt = self.storage.put(
                result.content,
                expected_sha256=result.content_hash,
                stored_at=result.collected_at,
            )
            stored_ref = receipt.ref
            raw = RawDocument(
                id=uuid4(), run_id=run.id, source_id=source.id,
                source_url=result.final_url or result.requested_url,
                canonical_url=canonicalize_url(result.final_url or result.requested_url),
                content_ref=receipt.ref,
                content_sha256=receipt.content_sha256,
                collected_at=result.collected_at,
                media_type=result.content_type,
                http_status=result.http_status,
                provenance={
                    "requested_url": result.requested_url,
                    "final_url": result.final_url,
                    "collection_method": source.collection_method.value,
                    "elapsed_seconds": result.elapsed_time,
                    "metadata": result.metadata,
                    "retention_policy": {
                        "days": self.settings.raw_retention_days,
                        "expires_at": (result.collected_at + timedelta(
                            days=self.settings.raw_retention_days)).isoformat(),
                    },
                },
            )
            self.documents.save(raw)
            raw_persisted = True
            finished_at = datetime.now(timezone.utc)
            self.registry.record_collection_result(source.id, succeeded=True,
                                                   collected_at=finished_at)
            finished = replace(
                run, status=RunStatus.SUCCEEDED,
                finished_at=finished_at,
                found_count=1, stored_count=1,
            )
            self.runs.save(finished)
            return RunOutcome(finished, raw, result, None)
        except Exception as error:
            if (stored_ref is not None and not raw_persisted and
                    getattr(self.storage, "delete_orphan_on_metadata_failure", True)):
                # A failed metadata write may have left unreferenced bytes.
                try:
                    self.storage.delete(stored_ref)
                except Exception:
                    pass  # Preserve the original failure in the run record.
            finished = replace(
                run, status=RunStatus.FAILED,
                finished_at=datetime.now(timezone.utc),
                found_count=1 if result and result.succeeded else 0,
                stored_count=1 if raw_persisted else 0,
                error_count=1, error_summary=str(error),
            )
            self.runs.save(finished)
            try:
                self.registry.record_collection_result(source.id, succeeded=False,
                                                       collected_at=finished.finished_at)
            except Exception:
                pass  # The FAILED run is the authoritative failure record.
            return RunOutcome(finished, raw if raw_persisted else None, result, str(error))
