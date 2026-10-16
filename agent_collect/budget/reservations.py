"""Fail-closed adapter for the Agent 011 atomic provider budget RPCs."""

from typing import Any, Protocol
from uuid import UUID


class BudgetAlreadyReserved(RuntimeError):
    """The same idempotency key was used previously; do not call the provider."""


class BudgetReservations(Protocol):
    def reserve(self, *, kind: str, scope: str, key: str,
                query_text: str | None = None) -> UUID: ...
    def dispatch(self, reservation_id: UUID) -> None: ...
    def finish(self, reservation_id: UUID, *, status: str,
               error_code: str | None = None) -> None: ...


class SupabaseBudgetReservations:
    def __init__(self, client: Any) -> None:
        self.client = client

    def reserve(self, *, kind: str, scope: str, key: str,
                query_text: str | None = None) -> UUID:
        result = self.client.rpc("agent_reserve_provider_budget", {
            "p_kind": kind, "p_scope": scope, "p_key": key,
            "p_query_text": query_text,
        }).execute().data
        if result is None:
            raise BudgetAlreadyReserved("budget reservation already exists")
        if not isinstance(result, str):
            raise RuntimeError("budget reservation unconfirmed")
        return UUID(result)

    def dispatch(self, reservation_id: UUID) -> None:
        result = self.client.rpc("agent_dispatch_provider_budget", {
            "p_id": str(reservation_id),
        }).execute().data
        if result is not True:
            raise RuntimeError("budget dispatch unconfirmed")

    def finish(self, reservation_id: UUID, *, status: str,
               error_code: str | None = None) -> None:
        result = self.client.rpc("agent_finish_provider_budget", {
            "p_id": str(reservation_id), "p_status": status,
            "p_error_code": error_code,
        }).execute().data
        if result is not True:
            raise RuntimeError("budget completion unconfirmed")
