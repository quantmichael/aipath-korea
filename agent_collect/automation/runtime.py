"""Fail-closed automation switch, DB lease, and isolated one-shot workers."""

from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol
from uuid import UUID, uuid4


class AutomationLease(Protocol):
    def acquire(self, *, worker_id: str, claim_id: UUID, lease_seconds: int) -> bool: ...
    def release(self, claim_id: UUID) -> bool: ...
    def record_summary(self, details: dict[str, Any]) -> None: ...


@dataclass(frozen=True)
class AutomationSummary:
    run_id: UUID
    started_at: datetime
    finished_at: datetime
    dry_run: bool
    status: str
    results: dict[str, Any]
    errors: dict[str, str]


class AutomationRuntime:
    def __init__(self, *, enabled: bool, worker_enabled: dict[str, bool],
                 workers: dict[str, Callable[[], Any]], lease: AutomationLease,
                 worker_id: str, lease_seconds: int = 3600,
                 metadata_handoff: Callable[[tuple[str, ...]], Any] | None = None) -> None:
        if not worker_id.strip() or not 1 <= lease_seconds <= 86400:
            raise ValueError("invalid automation policy")
        self.enabled, self.worker_enabled, self.workers = enabled, worker_enabled, workers
        self.lease, self.worker_id, self.lease_seconds = lease, worker_id, lease_seconds
        self.metadata_handoff = metadata_handoff

    def status(self) -> dict[str, Any]:
        names = ("discovery", "collection", "processing", "publish")
        if self.worker_enabled.get("ai_plan") or self.worker_enabled.get("ai_execute"):
            names = ("ai_plan", "ai_execute", *names)
        return {"automation_enabled": self.enabled,
                "workers": {name: bool(self.worker_enabled.get(name, False))
                for name in names}}

    def run_once(self, *, dry_run: bool = False) -> AutomationSummary:
        run_id = uuid4()
        started = datetime.now(timezone.utc)
        results: dict[str, Any] = {}
        errors: dict[str, str] = {}
        if not self.enabled:
            return AutomationSummary(run_id, started, datetime.now(timezone.utc),
                                     dry_run, "DISABLED", results, errors)
        claim_id = uuid4()
        acquired = False
        if not dry_run:
            acquired = self.lease.acquire(worker_id=self.worker_id,
                                          claim_id=claim_id,
                                          lease_seconds=self.lease_seconds)
            if not acquired:
                return AutomationSummary(run_id, started, datetime.now(timezone.utc),
                                         False, "LOCKED", results, errors)
        try:
            discovery_query_ids: list[str] = []
            for name in ("ai_plan", "ai_execute", "discovery", "collection", "processing", "publish"):
                if not self.worker_enabled.get(name, False):
                    continue
                try:
                    results[name] = self.workers[name]().run_once(dry_run=dry_run)
                    if name == "publish" and getattr(results[name], "failed_count", 0):
                        errors[name] = "PUBLISH_INCOMPLETE"
                    if not dry_run and name == "ai_execute":
                        outcome = results[name]
                        if isinstance(outcome, dict) and outcome.get("status") == "SUCCEEDED" and outcome.get("query_id"):
                            discovery_query_ids.append(outcome["query_id"])
                    elif not dry_run and name == "discovery":
                        discovery_query_ids.extend(getattr(results[name], "query_ids", ()))
                except Exception as error:
                    errors[name] = type(error).__name__
                if (not dry_run and self.metadata_handoff is not None and
                        (name == "discovery" or
                         name == "ai_execute" and not self.worker_enabled.get("discovery", False)) and
                        discovery_query_ids):
                    try:
                        results["metadata"] = self.metadata_handoff(
                            tuple(dict.fromkeys(discovery_query_ids)))
                    except Exception as error:
                        errors["metadata"] = type(error).__name__
            summary = AutomationSummary(run_id, started, datetime.now(timezone.utc),
                                        dry_run, "PARTIAL" if errors else "COMPLETED",
                                        results, errors)
            if not dry_run:
                self.lease.record_summary({
                    "run_id": str(run_id), "worker_id": self.worker_id,
                    "started_at": started.isoformat(),
                    "finished_at": summary.finished_at.isoformat(),
                    "status": summary.status,
                    "workers": {name: {
                        field.name: getattr(result, field.name) for field in fields(result)
                        if field.name.endswith("_count") and
                        isinstance(getattr(result, field.name), int)
                    } if is_dataclass(result) else {} for name, result in results.items()},
                    "discovered_count": getattr(results.get("discovery"), "discovered_count", 0),
                    "candidate_count": getattr(results.get("discovery"), "candidate_count", 0),
                    "auto_approved_count": getattr(results.get("discovery"), "auto_approved_count", 0),
                    "review_required_count": getattr(results.get("discovery"), "review_required_count", 0),
                    "rejected_count": getattr(results.get("discovery"), "rejected_count", 0),
                    "collection_scheduled_count": getattr(results.get("discovery"), "collection_scheduled_count", 0),
                    "collection_success_count": getattr(results.get("collection"), "success_count", 0),
                    "metadata": results.get("metadata", {}),
                    "errors": errors,
                })
            return summary
        finally:
            if acquired:
                self.lease.release(claim_id)
