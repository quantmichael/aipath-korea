"""Agent-only queue authorization, result persistence and publish audit."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class QueueAuthorization:
    candidate_id: UUID
    verification_id: UUID
    status: str
    snapshot: dict[str, Any]
    verification_result: str
    gate_status: str
    service_opportunity_id: UUID | None = None
    service_slug: str | None = None
    service_status: str | None = None
    published_at: datetime | None = None
    attempt_count: int = 0


class PublishLedger(Protocol):
    def get(self, candidate_id: UUID) -> QueueAuthorization | None: ...
    def claim(self, candidate_id: UUID, *, prior_attempts: int) -> bool: ...
    def mark_published(self, candidate_id: UUID, *, service_id: UUID, slug: str,
                       service_status: str, published_at: datetime) -> None: ...
    def mark_failed(self, candidate_id: UUID, *, code: str) -> None: ...
    def log(self, candidate_id: UUID, *, event_type: str, details: dict[str, Any]) -> None: ...


class SupabasePublishLedger:
    def __init__(self, client: Any) -> None:
        self.client = client
        self._claims: dict[UUID, tuple[UUID, UUID]] = {}

    def get(self, candidate_id: UUID) -> QueueAuthorization | None:
        rows = (self.client.table("agent_publish_queue").select("*")
                .eq("candidate_id", str(candidate_id)).limit(1).execute().data or [])
        if not rows:
            return None
        row = rows[0]
        verifications = (self.client.table("agent_verifications")
                         .select("candidate_id,result,checks")
                         .eq("id", row["verification_id"]).limit(1).execute().data or [])
        if not verifications or verifications[0]["candidate_id"] != str(candidate_id):
            raise RuntimeError("queue verification FK mismatch")
        verification = verifications[0]
        return QueueAuthorization(
            candidate_id=candidate_id, verification_id=UUID(row["verification_id"]),
            status=row["status"], snapshot=row["publish_candidate"],
            verification_result=verification["result"],
            gate_status=(verification.get("checks") or {}).get("gate_decision", {}).get("status", ""),
            service_opportunity_id=(UUID(row["service_opportunity_id"])
                                    if row.get("service_opportunity_id") else None),
            service_slug=row.get("service_slug"), service_status=row.get("service_status"),
            published_at=(datetime.fromisoformat(row["published_at"].replace("Z", "+00:00"))
                          if row.get("published_at") else None),
            attempt_count=row.get("attempt_count") or 0,
        )

    def claim(self, candidate_id: UUID, *, prior_attempts: int) -> bool:
        rows = (self.client.table("agent_publish_queue").select("id")
                .eq("candidate_id", str(candidate_id)).limit(1).execute().data or [])
        if len(rows) != 1:
            return False
        claim_id = uuid4()
        claimed = self.client.rpc("agent_claim_publish", {
            "p_queue_id": rows[0]["id"], "p_worker_id": f"manual-adapter-{claim_id}",
            "p_claim_id": str(claim_id), "p_lease_seconds": 300,
        }).execute().data or []
        if not claimed:
            return False
        self._claims[candidate_id] = UUID(rows[0]["id"]), claim_id
        return True

    def _complete(self, candidate_id: UUID, *, outcome: str,
                  service_id: UUID | None = None, slug: str | None = None,
                  service_status: str | None = None, published_at: datetime | None = None,
                  error_code: str | None = None) -> None:
        if candidate_id not in self._claims:
            if not self.claim(candidate_id, prior_attempts=0):
                raise RuntimeError("publish claim required")
        queue_id, claim_id = self._claims[candidate_id]
        result = self.client.rpc("agent_finish_publish", {
            "p_queue_id": str(queue_id), "p_claim_id": str(claim_id),
            "p_outcome": outcome, "p_service_id": str(service_id) if service_id else None,
            "p_slug": slug, "p_service_status": service_status,
            "p_published_at": published_at.isoformat() if published_at else None,
            "p_error_code": error_code,
        }).execute().data
        if result != outcome:
            raise RuntimeError("publish completion outcome mismatch")
        self._claims.pop(candidate_id, None)

    def mark_published(self, candidate_id: UUID, *, service_id: UUID, slug: str,
                       service_status: str, published_at: datetime) -> None:
        self._complete(candidate_id, outcome="PUBLISHED", service_id=service_id,
                       slug=slug, service_status=service_status, published_at=published_at)

    def mark_failed(self, candidate_id: UUID, *, code: str) -> None:
        self._complete(candidate_id, outcome="FAILED", error_code=code)

    def log(self, candidate_id: UUID, *, event_type: str, details: dict[str, Any]) -> None:
        self.client.table("agent_logs").insert({
            "id": str(uuid4()), "candidate_id": str(candidate_id),
            "level": "ERROR" if event_type == "SERVICE_PUBLISH_FAILED" else "INFO",
            "event_type": event_type, "details": details,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
