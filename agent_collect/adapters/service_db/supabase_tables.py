"""The only Service table I/O implementation used by Agent publication."""

from typing import Any
from uuid import UUID


class SupabaseServiceTables:
    def __init__(self, client: Any, *, max_scan: int = 500) -> None:
        if max_scan <= 0:
            raise ValueError("max_scan must be positive")
        self.client = client
        self.max_scan = max_scan
        self.read_count = 0
        self.write_count = 0

    def _bounded(self, table: str, columns: str) -> list[dict[str, Any]]:
        self.read_count += 1
        response = (self.client.table(table).select(columns, count="exact")
                    .limit(self.max_scan + 1).execute())
        rows = response.data or []
        if response.count is None or response.count > self.max_scan or len(rows) > self.max_scan:
            raise RuntimeError(f"{table} scan incomplete")
        return rows

    def list_sources(self) -> list[dict[str, Any]]:
        return self._bounded("sources", "id,name,homepage_url,is_active")

    def get_conference_category(self) -> dict[str, Any] | None:
        self.read_count += 1
        rows = (self.client.table("categories").select("id,slug,is_active")
                .eq("slug", "conference").limit(2).execute().data or [])
        if len(rows) != 1 or not rows[0].get("is_active"):
            return None
        return rows[0]

    def list_tags(self, slugs: tuple[str, ...]) -> list[dict[str, Any]]:
        if not slugs:
            return []
        self.read_count += 1
        return (self.client.table("tags").select("id,slug")
                .in_("slug", list(slugs)).execute().data or [])

    def list_opportunities(self) -> list[dict[str, Any]]:
        return self._bounded("opportunities",
                             "id,source_id,external_id,title,organizer,official_url,event_start_at,slug,status")

    def insert_opportunity(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.write_count += 1
        rows = self.client.table("opportunities").insert(payload).execute().data or []
        if len(rows) != 1:
            raise RuntimeError("opportunity insert response not singular")
        return rows[0]

    def get_opportunity(self, record_id: UUID) -> dict[str, Any] | None:
        self.read_count += 1
        rows = (self.client.table("opportunities").select("*")
                .eq("id", str(record_id)).limit(1).execute().data or [])
        return rows[0] if rows else None
