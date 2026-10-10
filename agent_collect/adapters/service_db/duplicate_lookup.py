"""Explicit read-only Service opportunity lookup for deduplication.

This adapter exposes SELECT only. It is separate from the future publish port.
"""

from typing import Any, Protocol

from agent_collect.quality_gate.models import DuplicateRecord


class ServiceDuplicateLookup(Protocol):
    def list_records(self) -> list[DuplicateRecord]: ...


class SupabaseServiceDuplicateLookup:
    def __init__(self, client: Any, *, max_records: int = 500) -> None:
        if max_records <= 0:
            raise ValueError("max_records must be positive")
        self.client = client
        self.max_records = max_records
        self.read_count = 0

    def list_records(self) -> list[DuplicateRecord]:
        self.read_count += 1
        result = (self.client.table("opportunities")
                  .select("id,title,organizer,official_url,event_start_at", count="exact")
                  .limit(self.max_records + 1).execute())
        rows = result.data or []
        if result.count is None or result.count > self.max_records or len(rows) > self.max_records:
            raise RuntimeError("service duplicate scan incomplete")
        return [DuplicateRecord(
            id=row["id"], origin="service", title=row.get("title"),
            organizer=row.get("organizer"), official_url=row.get("official_url"),
            registration_url=None, canonical_url=None, event_start=row.get("event_start_at"),
        ) for row in rows]
