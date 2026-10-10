"""Dry-run-first, fail-closed Service publication boundary.

Only this module may request a Service Opportunity INSERT. No UPDATE/MERGE.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from agent_collect.adapters.service_db.contract import (
    PublishAction, PublishResult, ServicePublicationStatus,
)
from agent_collect.adapters.service_db.mapping import (
    ConflictDecision, ConflictStatus, ServiceTables, final_conflict, map_source,
    source_creation_eligible, stable_external_id, stable_slug,
)
from agent_collect.adapters.service_db.policy import (
    DeterministicPublicationStatusResolver, TemporalAssessment, assess_temporal,
)
from agent_collect.repositories.contracts import SourceRepository
from agent_collect.registry.approval import source_is_collectible
from agent_collect.registry.service import canonicalize_url
from agent_collect.dedup.engine import normalize_title
from agent_collect.repositories.publish_ledger import PublishLedger, QueueAuthorization
from agent_collect.schemas.models import DuplicateMethod, DuplicateResult, PublishCandidate, Vertical
from agent_collect.schemas.status import VerificationStatus


def _json(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json(item) for item in value]
    return value


def publish_candidate_from_snapshot(snapshot: dict[str, Any]) -> PublishCandidate:
    """Decode the queued v1 handoff; validation occurs in the domain model."""
    data = dict(snapshot)
    for name in ("agent_item_id", "agent_source_id", "run_id"):
        data[name] = UUID(data[name])
    for name in ("verified_at", "application_start_at", "application_deadline_at",
                 "event_start_at", "event_end_at", "discovered_at", "collected_at"):
        if data.get(name) is not None:
            data[name] = datetime.fromisoformat(data[name].replace("Z", "+00:00"))
    data["vertical"] = Vertical(data["vertical"])
    data["verification_status"] = VerificationStatus(data["verification_status"])
    duplicate = data["duplicate_result"]
    data["duplicate_result"] = DuplicateResult(
        item_id=UUID(duplicate["item_id"]), is_duplicate=duplicate["is_duplicate"],
        method=DuplicateMethod(duplicate["method"]), confidence=duplicate["confidence"],
        matched_ref=duplicate.get("matched_ref"),
    )
    data["evidence_refs"] = tuple(data["evidence_refs"])
    data["tag_slugs"] = tuple(data.get("tag_slugs") or ())
    return PublishCandidate(**data)


@dataclass(frozen=True, slots=True)
class ServicePublishPlan:
    candidate_id: UUID
    eligible: bool
    source_id: UUID | None
    source_mapping: str
    category_id: UUID | None
    publication_status: ServicePublicationStatus | None
    temporal: TemporalAssessment
    conflict: ConflictDecision
    matched_tag_ids: tuple[UUID, ...]
    omitted_fields: tuple[str, ...]
    reasons: tuple[str, ...]
    insert_payload: dict[str, Any] | None


class SupabaseServiceDBAdapter:
    """ServiceDBAdapter implementation over injected Service tables and Agent ledger."""

    def __init__(self, *, tables: ServiceTables, ledger: PublishLedger,
                 agent_sources: SourceRepository | None = None,
                 publish_threshold: float = 0.85,
                 publish_enabled: bool = False) -> None:
        if not 0 <= publish_threshold <= 1:
            raise ValueError("publish threshold must be between 0 and 1")
        self.tables = tables
        self.ledger = ledger
        self.agent_sources = agent_sources
        self.publish_threshold = publish_threshold
        self.publish_enabled = publish_enabled
        self.status_resolver = DeterministicPublicationStatusResolver()

    def dry_run(self, candidate: PublishCandidate, *, at: datetime | None = None,
                preclaimed: bool = False, retryable: bool = False) -> ServicePublishPlan:
        if not isinstance(candidate, PublishCandidate):
            raise ValueError("PublishCandidate v1 required")
        now = at or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("dry-run time must be timezone-aware")
        reasons: list[str] = []
        if not self.publish_enabled:
            reasons.append("PUBLISH_FEATURE_DISABLED")
        queue = self.ledger.get(candidate.agent_item_id)
        if queue is None:
            reasons.append("PUBLISH_QUEUE_MISSING")
        elif queue.status not in (("PUBLISHING",) if preclaimed else
                                  ("READY", "FAILED") if retryable else ("READY",)):
            reasons.append(f"PUBLISH_QUEUE_{queue.status}")
        if queue is not None and (
            queue.verification_result != "VERIFIED" or queue.gate_status != "PUBLISH_READY"
        ):
            reasons.append("QUEUE_VERIFICATION_NOT_READY")
        if queue is not None and queue.snapshot != _json(asdict(candidate)):
            reasons.append("QUEUE_SNAPSHOT_MISMATCH")
        if candidate.confidence < self.publish_threshold:
            reasons.append("LOW_PUBLISH_CONFIDENCE")
        if self.agent_sources is not None:
            agent_source = self.agent_sources.get(candidate.agent_source_id)
            if agent_source is None or not source_is_collectible(agent_source):
                reasons.append("AGENT_SOURCE_NOT_ACTIVE_APPROVED")
            elif (not candidate.source_homepage_url or
                  canonicalize_url(candidate.source_homepage_url) !=
                  canonicalize_url(agent_source.canonical_url or agent_source.url) or
                  normalize_title(candidate.source_name) != normalize_title(agent_source.name)):
                reasons.append("AGENT_SOURCE_IDENTITY_MISMATCH")
        temporal = assess_temporal(candidate)
        if not temporal.publishable:
            reasons.append(temporal.reason or "TEMPORAL_NOT_PUBLISHABLE")
        status = self.status_resolver.resolve(candidate, at=now)
        if status not in {ServicePublicationStatus.SCHEDULED,
                          ServicePublicationStatus.OPEN, ServicePublicationStatus.ONGOING}:
            reasons.append("PUBLICATION_STATUS_NOT_VISIBLE")
        source_id, source_mapping = map_source(candidate, self.tables.list_sources())
        if source_id is None:
            reasons.append(source_mapping)
            agent_source = (self.agent_sources.get(candidate.agent_source_id)
                            if self.agent_sources is not None else None)
            if source_creation_eligible(candidate, agent_source, threshold=self.publish_threshold):
                reasons.append("SOURCE_CREATION_REQUIRES_MANUAL_APPROVAL")
            else:
                reasons.append("SOURCE_CREATION_NOT_ELIGIBLE")
        category = self.tables.get_conference_category()
        category_id = UUID(category["id"]) if category else None
        if category_id is None:
            reasons.append("CONFERENCE_CATEGORY_MISSING")
        known_tags = self.tables.list_tags(candidate.tag_slugs)
        matched_tag_ids = tuple(UUID(row["id"]) for row in known_tags)
        known_slugs = {row["slug"] for row in known_tags}
        omitted = [f"tag:{slug}" for slug in candidate.tag_slugs if slug not in known_slugs]
        # No junction-table write in this one-Opportunity-write phase.
        omitted.extend(f"tag_link:{slug}" for slug in candidate.tag_slugs if slug in known_slugs)
        conflict = ConflictDecision(ConflictStatus.REVIEW_REQUIRED, "SOURCE_MAPPING_REQUIRED")
        if source_id is not None:
            conflict = final_conflict(candidate, self.tables.list_opportunities(), source_id=source_id)
            if conflict.status is not ConflictStatus.INSERT_ALLOWED:
                reasons.append(f"FINAL_CONFLICT:{conflict.reason}")
        payload = None
        if source_id is not None and category_id is not None and status is not None:
            payload = self._payload(candidate, source_id=source_id,
                                    category_id=category_id, status=status, at=now)
        return ServicePublishPlan(
            candidate_id=candidate.agent_item_id, eligible=not reasons,
            source_id=source_id, source_mapping=source_mapping,
            category_id=category_id, publication_status=status, temporal=temporal,
            conflict=conflict, matched_tag_ids=matched_tag_ids,
            omitted_fields=tuple(omitted), reasons=tuple(dict.fromkeys(reasons)),
            insert_payload=payload,
        )

    @staticmethod
    def _payload(candidate: PublishCandidate, *, source_id: UUID, category_id: UUID,
                 status: ServicePublicationStatus, at: datetime) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source_id": str(source_id), "category_id": str(category_id),
            "external_id": stable_external_id(candidate), "slug": stable_slug(candidate),
            "title": candidate.title, "summary": candidate.summary,
            "organizer": candidate.organizer, "official_url": candidate.official_url,
            "status": status.value, "is_featured": False,
            "published_at": at.isoformat(), "last_verified_at": candidate.verified_at.isoformat(),
        }
        for name in (
            "description", "target_audience", "difficulty", "format", "region", "venue",
            "price_type", "price_text", "image_url", "application_start_at",
            "application_deadline_at", "event_start_at", "event_end_at",
        ):
            value = getattr(candidate, name)
            if value is not None:
                payload[name] = value.isoformat() if isinstance(value, datetime) else value
        return payload

    def publish(self, candidate: PublishCandidate, *, preclaimed: bool = False) -> PublishResult:
        plan = self.dry_run(candidate, preclaimed=preclaimed)
        queue = self.ledger.get(candidate.agent_item_id)
        if (self.publish_enabled and queue is not None and
                queue.snapshot == _json(asdict(candidate)) and
                queue.verification_result == "VERIFIED" and queue.gate_status == "PUBLISH_READY" and
                plan.source_id is not None and plan.conflict.reason == "AGENT_IDEMPOTENCY_KEY" and
                plan.conflict.matched_record_id is not None):
            if queue.status == "PUBLISHED":
                if queue.service_opportunity_id != plan.conflict.matched_record_id:
                    return PublishResult(candidate.agent_item_id, PublishAction.FAILED,
                                         source_id=plan.source_id,
                                         error_code="PUBLISHED_QUEUE_RECORD_MISMATCH")
                try:
                    row, status, published_at = self._verified_readback(
                        candidate, plan.conflict.matched_record_id,
                        plan.source_id, plan.category_id)
                except Exception:
                    return PublishResult(candidate.agent_item_id, PublishAction.FAILED,
                                         source_id=plan.source_id,
                                         error_code="PUBLISHED_READBACK_FAILED")
                return PublishResult(candidate.agent_item_id, PublishAction.SKIPPED_DUPLICATE,
                                     service_opportunity_id=plan.conflict.matched_record_id,
                                     service_slug=row["slug"], published_status=status,
                                     published_at=published_at, source_id=plan.source_id,
                                     conflict_status=ConflictStatus.DUPLICATE.value)
            if queue.status in {"READY", "PUBLISHING", "FAILED"}:
                try:
                    return self._reconcile(candidate, plan.conflict.matched_record_id,
                                           plan.source_id, plan.category_id)
                except Exception:
                    return PublishResult(candidate.agent_item_id, PublishAction.FAILED,
                                         source_id=plan.source_id,
                                         error_code="IDEMPOTENT_RECONCILIATION_FAILED")
        if not plan.eligible:
            duplicate = plan.conflict.status is ConflictStatus.DUPLICATE
            action = PublishAction.SKIPPED_DUPLICATE if duplicate else PublishAction.HELD
            self.ledger.log(candidate.agent_item_id, event_type="SERVICE_PUBLISH_HELD",
                            details={"reason_codes": list(plan.reasons), "conflict": plan.conflict.status.value})
            return PublishResult(
                candidate.agent_item_id, action,
                service_opportunity_id=plan.conflict.matched_record_id if duplicate else None,
                source_id=plan.source_id, conflict_status=plan.conflict.status.value,
                error_code=plan.reasons[0] if plan.reasons else "NOT_READY",
            )
        assert queue is not None and plan.source_id is not None and plan.insert_payload is not None
        if not preclaimed and not self.ledger.claim(candidate.agent_item_id, prior_attempts=queue.attempt_count):
            return PublishResult(candidate.agent_item_id, PublishAction.HELD,
                                 source_id=plan.source_id, error_code="QUEUE_CLAIM_LOST")
        try:
            self.ledger.log(candidate.agent_item_id, event_type="SERVICE_PUBLISH_ATTEMPT",
                            details={"adapter": "SupabaseServiceDBAdapter", "decision": "INSERT_ALLOWED"})
            fresh = final_conflict(candidate, self.tables.list_opportunities(), source_id=plan.source_id)
            if fresh.status is not ConflictStatus.INSERT_ALLOWED:
                if fresh.reason == "AGENT_IDEMPOTENCY_KEY" and fresh.matched_record_id:
                    return self._reconcile(candidate, fresh.matched_record_id,
                                           plan.source_id, plan.category_id)
                self.ledger.mark_failed(candidate.agent_item_id, code=f"FINAL_CONFLICT:{fresh.reason}")
                return PublishResult(candidate.agent_item_id, PublishAction.CONFLICT,
                                     source_id=plan.source_id, conflict_status=fresh.status.value,
                                     error_code=fresh.reason)
            inserted = self.tables.insert_opportunity(plan.insert_payload)
            record_id = UUID(inserted["id"])
            return self._reconcile(candidate, record_id, plan.source_id, plan.category_id)
        except Exception as error:
            # A transport exception may happen after the INSERT commits. Stable
            # slug/external_id plus readback is the recovery path, not a blind retry.
            try:
                matching = final_conflict(candidate, self.tables.list_opportunities(),
                                          source_id=plan.source_id)
                if matching.reason == "AGENT_IDEMPOTENCY_KEY" and matching.matched_record_id:
                    return self._reconcile(candidate, matching.matched_record_id,
                                           plan.source_id, plan.category_id)
            except Exception:
                pass
            self.ledger.mark_failed(candidate.agent_item_id, code="SERVICE_INSERT_OR_READBACK_FAILED")
            self.ledger.log(candidate.agent_item_id, event_type="SERVICE_PUBLISH_FAILED",
                            details={"error_code": "SERVICE_INSERT_OR_READBACK_FAILED",
                                     "error_type": type(error).__name__})
            return PublishResult(candidate.agent_item_id, PublishAction.FAILED,
                                 source_id=plan.source_id, error_code="SERVICE_INSERT_OR_READBACK_FAILED")

    def _verified_readback(self, candidate: PublishCandidate, record_id: UUID,
                           source_id: UUID, category_id: UUID | None
                           ) -> tuple[dict[str, Any], ServicePublicationStatus, datetime]:
        row = self.tables.get_opportunity(record_id)
        row_start = None
        if row and row.get("event_start_at"):
            row_start = datetime.fromisoformat(row["event_start_at"].replace("Z", "+00:00"))
        if not row or row.get("slug") != stable_slug(candidate) or (
            row.get("external_id") != stable_external_id(candidate) or
            row.get("source_id") != str(source_id) or
            row.get("category_id") != str(category_id) or
            row.get("official_url") != candidate.official_url or
            row.get("title") != candidate.title or
            row.get("summary") != candidate.summary or
            row.get("organizer") != candidate.organizer or
            row_start != candidate.event_start_at
        ):
            raise RuntimeError("Service readback mismatch")
        status = ServicePublicationStatus(row["status"])
        if status not in {ServicePublicationStatus.SCHEDULED,
                          ServicePublicationStatus.OPEN, ServicePublicationStatus.ONGOING}:
            raise RuntimeError("Service readback is not API-visible")
        published_at = datetime.fromisoformat(row["published_at"].replace("Z", "+00:00"))
        return row, status, published_at

    def _reconcile(self, candidate: PublishCandidate, record_id: UUID,
                   source_id: UUID, category_id: UUID | None) -> PublishResult:
        row, status, published_at = self._verified_readback(candidate, record_id,
                                                             source_id, category_id)
        self.ledger.mark_published(candidate.agent_item_id, service_id=record_id,
                                   slug=row["slug"], service_status=status.value,
                                   published_at=published_at)
        try:
            self.ledger.log(candidate.agent_item_id, event_type="SERVICE_PUBLISHED",
                            details={"adapter": "SupabaseServiceDBAdapter",
                                     "service_opportunity_id": str(record_id),
                                     "status": status.value})
        except Exception:
            # A secondary audit transport failure must not turn a committed
            # Service row plus Agent PUBLISHED completion into a retry.
            pass
        return PublishResult(candidate.agent_item_id, PublishAction.CREATED,
                             service_opportunity_id=record_id, service_slug=row["slug"],
                             published_status=status, published_at=published_at,
                             source_id=source_id, conflict_status=ConflictStatus.INSERT_ALLOWED.value)
