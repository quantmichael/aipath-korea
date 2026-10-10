"""Atomic Source review transition port and offline implementation."""

from threading import RLock
from typing import Protocol
from uuid import UUID

from agent_collect.registry.approval import (
    SourceReviewCommand, SourceReviewEvent, decide_source_transition,
)
from agent_collect.repositories.memory import InMemorySourceRepository
from agent_collect.schemas.models import Source


class SourceReviewRepository(Protocol):
    def get_source(self, source_id: UUID) -> Source | None: ...
    def transition(self, command: SourceReviewCommand) -> Source: ...
    def list_events(self, source_id: UUID) -> list[SourceReviewEvent]: ...


class InMemorySourceReviewRepository:
    def __init__(self, sources: InMemorySourceRepository) -> None:
        self.sources = sources
        self._events: dict[UUID, list[SourceReviewEvent]] = {}
        self._decisions: dict[tuple[UUID, UUID], SourceReviewEvent] = {}
        self._lock = RLock()

    def get_source(self, source_id: UUID) -> Source | None:
        return self.sources.get(source_id)

    def transition(self, command: SourceReviewCommand) -> Source:
        with self._lock:
            previous = self._decisions.get((command.source_id, command.decision_id))
            if previous is not None:
                if (previous.command.decision is not command.decision or
                        previous.command.target_status is not command.target_status or
                        previous.command.source_type is not command.source_type or
                        previous.command.reviewer != command.reviewer or
                        previous.command.reason != command.reason or
                        previous.command.officiality_confirmed is not command.officiality_confirmed or
                        previous.command.collection_allowed is not command.collection_allowed):
                    raise ValueError("decision_id reused with different review decision")
                return self.sources.get(command.source_id)
            source = self.sources.get(command.source_id)
            if source is None:
                raise KeyError("source not found")
            updated, event = decide_source_transition(source, command)
            self.sources._save_reviewed(updated)
            self._events.setdefault(source.id, []).append(event)
            self._decisions[(source.id, command.decision_id)] = event
            return updated

    def list_events(self, source_id: UUID) -> list[SourceReviewEvent]:
        return list(self._events.get(source_id, []))
