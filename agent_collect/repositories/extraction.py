"""Agent-only candidate and usage ledger port for Conference extraction."""

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from agent_collect.extraction.schema import StoredExtraction


class ExtractionRepository(Protocol):
    def find_by_raw_fingerprint(self, raw_document_id: UUID, fingerprint: str) -> StoredExtraction | None: ...
    def find_by_fingerprint(self, fingerprint: str) -> StoredExtraction | None: ...
    def find_by_content_hash(self, content_hash: str) -> StoredExtraction | None: ...
    def save(self, row: StoredExtraction) -> StoredExtraction: ...
    def count_attempts_since(self, since: datetime) -> int: ...
    def count_attempts_for_fingerprint(self, fingerprint: str) -> int: ...
    def log(self, *, event_type: str, source_id: UUID, run_id: UUID,
            details: dict[str, Any], input_tokens: int | None = None,
            output_tokens: int | None = None) -> None: ...


class InMemoryExtractionRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, StoredExtraction] = {}
        self.logs: list[dict[str, Any]] = []

    def find_by_raw_fingerprint(self, raw_document_id: UUID, fingerprint: str) -> StoredExtraction | None:
        return next((row for row in self.rows.values()
                     if row.raw_document_id == raw_document_id and row.fingerprint == fingerprint), None)

    def find_by_fingerprint(self, fingerprint: str) -> StoredExtraction | None:
        return next((row for row in self.rows.values() if row.fingerprint == fingerprint), None)

    def find_by_content_hash(self, content_hash: str) -> StoredExtraction | None:
        return next((row for row in self.rows.values()
                     if row.provenance.get("content_sha256") == content_hash), None)

    def save(self, row: StoredExtraction) -> StoredExtraction:
        existing = self.find_by_raw_fingerprint(row.raw_document_id, row.fingerprint)
        if existing is not None and row.provenance.get("cache_reused"):
            return existing
        if existing is not None and existing.id != row.id:
            raise ValueError("raw extraction fingerprint already exists")
        self.rows[row.id] = row
        return row

    def count_attempts_since(self, since: datetime) -> int:
        return sum(log["event_type"] == "EXTRACTION_LLM_ATTEMPT" and log["created_at"] >= since
                   for log in self.logs)

    def count_attempts_for_fingerprint(self, fingerprint: str) -> int:
        return sum(log["event_type"] == "EXTRACTION_LLM_ATTEMPT" and
                   log["details"].get("fingerprint") == fingerprint for log in self.logs)

    def log(self, *, event_type: str, source_id: UUID, run_id: UUID,
            details: dict[str, Any], input_tokens: int | None = None,
            output_tokens: int | None = None) -> None:
        from datetime import datetime, timezone
        self.logs.append({"id": uuid4(), "event_type": event_type,
                          "source_id": source_id, "run_id": run_id,
                          "details": details, "input_tokens": input_tokens,
                          "output_tokens": output_tokens,
                          "created_at": datetime.now(timezone.utc)})
