"""Read-only Metadata Evidence to existing atomic Service RPC contract.

The returned envelope is a preview. It is never enqueued or submitted here:
the current registration RPC does not bind an Agent Metadata approval to a
Service write, so an operational caller must not treat this as authorization.
"""

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID

from agent_collect.discovery.evidence import TimePrecision
from agent_collect.extraction.validation import parse_explicit_datetime, validated_calendar_date
from agent_collect.metadata.category_policy import category_policy
from agent_collect.metadata.evidence import MetadataAccessPolicy, MetadataOutcome
from agent_collect.adapters.service_db.mapping import preview_approved_source_mapping
from agent_collect.quality_gate.models import DuplicateStatus, GateStatus
from agent_collect.registry.approval import source_is_collectible
from agent_collect.schemas.models import Source
from opportunity_status.simulation import target_status


_TIME_FIELDS = ("application_start", "application_deadline", "event_start", "event_end")


@dataclass(frozen=True, slots=True)
class MetadataRegistrationPreview:
    eligible: bool
    reasons: tuple[str, ...]
    service_slug: str | None
    status: str | None
    record: dict[str, Any] | None
    evidence: dict[str, Any] | None


def _time_field(value: str | None, precision: TimePrecision | None,
                reference: str | None) -> tuple[str | None, dict[str, str]]:
    if value is None:
        return None, {"precision": "UNKNOWN"}
    if not reference:
        raise ValueError("replayed field reference required")
    if precision is TimePrecision.EXACT_TIME:
        parsed = parse_explicit_datetime(value)
        if parsed is None:
            raise ValueError("unconfirmed exact time")
        literal = parsed.isoformat()
        return literal, {"precision": "EXACT_TIME", "source_literal": literal,
                         "source_ref": reference}
    if precision is TimePrecision.DATE_ONLY:
        day = validated_calendar_date(value)
        if day is None:
            raise ValueError("unconfirmed calendar date")
        # Migration 002 expects the UTC calendar-date placeholder. Its scope
        # stores the original date and applies KST day-start/end separately.
        placeholder = datetime.combine(day, time.min, timezone.utc).isoformat()
        return placeholder, {"precision": "DATE_ONLY", "source_date": day.isoformat(),
                             "source_ref": reference}
    raise ValueError("unknown time precision cannot become a Service date")


def preview_metadata_registration(
    outcome: MetadataOutcome, *, source: Source | None,
    approval: MetadataAccessPolicy | None,
    service_sources: list[dict[str, Any]],
    service_categories: list[dict[str, Any]],
    at: datetime,
) -> MetadataRegistrationPreview:
    """Validate a proposed Service envelope without DB writes or queue access."""
    if at.tzinfo is None:
        raise ValueError("preview time must be timezone aware")
    reasons: list[str] = []
    policy = category_policy(outcome.values.get("category"))
    slug = policy.service_slug if policy else None
    if slug is None:
        reasons.append("CATEGORY_SERVICE_MAPPING_UNAVAILABLE")
    if (outcome.verification.status != "VERIFIED" or
            outcome.gate.status is not GateStatus.PUBLISH_READY or
            outcome.duplicate.status is not DuplicateStatus.UNIQUE):
        reasons.append("EVIDENCE_GATE_OR_DUPLICATE_NOT_READY")
    if (outcome.verification.candidate_id != outcome.candidate_id or
            outcome.duplicate.candidate_id != outcome.candidate_id or
            outcome.gate.candidate_id != outcome.candidate_id or
            outcome.verification.verified_at.tzinfo is None or
            not at - timedelta(minutes=15) <= outcome.verification.verified_at <= at):
        reasons.append("EVIDENCE_SNAPSHOT_STALE_OR_MISMATCHED")
    if (source is None or source.id != outcome.source_id or
            not source_is_collectible(source)):
        reasons.append("SOURCE_APPROVAL_NOT_ACTIVE")
    if (approval is None or source is None or
            not approval.permits(source, outcome.values.get("official_url") or "", now=at)[0] or
            (outcome.cohort == "operational" and (
                approval.approved_principal_id is None or approval.approval_event_id is None))):
        reasons.append("METADATA_APPROVAL_NOT_ACTIVE")
    mapped_source: UUID | None = None
    if source is not None and source_is_collectible(source):
        try:
            mapped_source, mapping_reason = preview_approved_source_mapping(
                source, service_sources)
            if mapped_source is None:
                reasons.append(mapping_reason)
        except ValueError:
            reasons.append("SERVICE_SOURCE_MAPPING_UNAVAILABLE")
    categories = [row for row in service_categories
                  if slug and row.get("slug") == slug and row.get("is_active") is True]
    if len(categories) != 1:
        reasons.append("SERVICE_CATEGORY_MISSING_OR_AMBIGUOUS")
    if reasons:
        return MetadataRegistrationPreview(False, tuple(dict.fromkeys(reasons)),
                                           slug, None, None, None)

    assert policy is not None and mapped_source is not None
    evidence: dict[str, Any] = {"evidence_ref": f"agent-metadata:{outcome.candidate_id}"}
    dates: dict[str, str] = {}
    try:
        for name in _TIME_FIELDS:
            stored, detail = _time_field(outcome.values.get(name),
                                         outcome.field_precisions.get(name),
                                         outcome.field_references.get(name))
            evidence[name] = detail
            if stored is not None:
                dates[name + "_at"] = stored
    except ValueError:
        return MetadataRegistrationPreview(False, ("TIME_EVIDENCE_UNREPRESENTABLE",),
                                           slug, None, None, None)
    title = outcome.values["title"]
    title_slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")[:70].strip("-")
    record: dict[str, Any] = {
        "source_id": str(mapped_source), "category_id": str(UUID(categories[0]["id"])),
        "external_id": f"runtime_agent_metadata:{outcome.candidate_id}",
        "slug": f"{title_slug or slug}-{outcome.candidate_id.hex}",
        "title": title, "summary": outcome.values["summary"],
        "organizer": outcome.values["organizer"],
        "official_url": outcome.values["official_url"],
        "is_featured": False,
        "last_verified_at": outcome.verification.verified_at.isoformat(),
        **dates,
    }
    status = target_status(record, evidence, at)
    if status not in {"scheduled", "open", "ongoing"}:
        return MetadataRegistrationPreview(False,
                                           ("PUBLICATION_STATUS_NOT_RECRUITABLE",),
                                           slug, status, None, None)
    record["status"] = status
    record["published_at"] = at.isoformat()
    return MetadataRegistrationPreview(True, (), slug, status, record, evidence)
