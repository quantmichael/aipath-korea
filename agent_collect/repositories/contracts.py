"""Persistence interfaces used by application services."""

from typing import Protocol
from uuid import UUID

from agent_collect.schemas.models import CollectionRun, RawDocument, Source


class SourceRepository(Protocol):
    def get(self, source_id: UUID) -> Source | None: ...

    def find_by_canonical_url(self, canonical_url: str) -> Source | None: ...

    def save(self, source: Source) -> Source: ...


class CollectionRunRepository(Protocol):
    def get(self, run_id: UUID) -> CollectionRun | None: ...

    def save(self, run: CollectionRun) -> CollectionRun: ...


class RawDocumentRepository(Protocol):
    def get(self, document_id: UUID) -> RawDocument | None: ...

    def save(self, document: RawDocument) -> RawDocument: ...
