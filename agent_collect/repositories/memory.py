"""In-memory repository implementations for offline workflows and tests."""

from uuid import UUID

from agent_collect.schemas.models import CollectionRun, RawDocument, Source
from agent_collect.schemas.status import RunStatus


class InMemorySourceRepository:
    def __init__(self) -> None:
        self._rows: dict[UUID, Source] = {}

    def get(self, source_id: UUID) -> Source | None:
        return self._rows.get(source_id)

    def find_by_canonical_url(self, canonical_url: str) -> Source | None:
        return next(
            (source for source in self._rows.values() if source.canonical_url == canonical_url),
            None,
        )

    def save(self, source: Source) -> Source:
        if source.canonical_url is None:
            raise ValueError("source requires canonical_url")
        existing = self.find_by_canonical_url(source.canonical_url)
        if existing is not None and existing.id != source.id:
            raise ValueError("canonical_url already registered")
        previous = self._rows.get(source.id)
        if previous is None and source.is_active:
            raise ValueError("direct import of ACTIVE source is forbidden")
        if previous is not None and (previous.status != source.status or
                                     previous.source_type != source.source_type or
                                     previous.is_active != source.is_active or
                                     previous.provenance.get("source_review") !=
                                     source.provenance.get("source_review") or
                                     (previous.status.value in {"APPROVED", "ACTIVE"} and
                                      (previous.canonical_url != source.canonical_url or
                                       previous.confidence != source.confidence or
                                       previous.collection_method != source.collection_method or
                                       previous.collection_config != source.collection_config or
                                       previous.collection_interval_minutes != source.collection_interval_minutes))):
            raise ValueError("lifecycle changes require SourceReviewRepository")
        self._rows[source.id] = source
        return source

    def _save_reviewed(self, source: Source) -> None:
        if source.id not in self._rows:
            raise KeyError("source not found")
        self._rows[source.id] = source


class InMemoryCollectionRunRepository:
    def __init__(self) -> None:
        self._rows: dict[UUID, CollectionRun] = {}

    def get(self, run_id: UUID) -> CollectionRun | None:
        return self._rows.get(run_id)

    def save(self, run: CollectionRun) -> CollectionRun:
        if run.source_config_snapshot.get("trigger") == "scheduled":
            previous = self._rows.get(run.id)
            if previous is not None and (previous.source_id != run.source_id or
                    previous.source_config_snapshot != run.source_config_snapshot or
                    (previous.status in {RunStatus.SUCCEEDED, RunStatus.FAILED} and
                     (run.status != previous.status or run.finished_at != previous.finished_at))):
                raise ValueError("terminal scheduled Run and claim/window identity are immutable")
            window = run.source_config_snapshot["schedule_window_id"]
            if any(existing.id != run.id and existing.source_id == run.source_id and
                   existing.source_config_snapshot.get("trigger") == "scheduled" and
                   existing.source_config_snapshot.get("schedule_window_id") == window
                   for existing in self._rows.values()):
                raise ValueError("scheduled CollectionRun window already exists")
        self._rows[run.id] = run
        return run


class InMemoryRawDocumentRepository:
    def __init__(self) -> None:
        self._rows: dict[UUID, RawDocument] = {}

    def get(self, document_id: UUID) -> RawDocument | None:
        return self._rows.get(document_id)

    def save(self, document: RawDocument) -> RawDocument:
        self._rows[document.id] = document
        return document
