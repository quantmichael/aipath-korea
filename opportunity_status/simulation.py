"""Offline mirror of Migration 002 for read-only impact previews.

The database function remains the runtime source of truth. This module never
writes Service data; use it to audit proposed evidence before migration rollout.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

KST = ZoneInfo("Asia/Seoul")


def _instant(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _field(evidence: dict[str, Any], name: str) -> dict[str, Any]:
    return evidence.get(name) or {"precision": "UNKNOWN"}


def _snapshot_matches(row: dict[str, Any], evidence: dict[str, Any]) -> bool:
    for name in ("application_start", "application_deadline", "event_start", "event_end"):
        detail = _field(evidence, name)
        stored = _instant(row.get(name + "_at"))
        if detail.get("precision") == "EXACT_TIME":
            if stored != _instant(detail.get("source_literal")):
                return False
        elif detail.get("precision") == "DATE_ONLY":
            source_date = detail.get("source_date")
            if stored is None or stored.astimezone(timezone.utc).date().isoformat() != source_date:
                return False
    return True


def target_status(row: dict[str, Any], evidence: dict[str, Any], now: datetime) -> str | None:
    if now.tzinfo is None:
        raise ValueError("simulation time must be timezone aware")
    if not _snapshot_matches(row, evidence):
        return None
    event_end = _instant(row.get("event_end_at"))
    event_start = _instant(row.get("event_start_at"))
    end_kind = _field(evidence, "event_end").get("precision")
    start_kind = _field(evidence, "event_start").get("precision")
    if event_end and end_kind == "EXACT_TIME" and now > event_end:
        return "ended"
    if (event_start and event_end and start_kind == end_kind == "EXACT_TIME"
            and now >= event_start):
        return "ongoing"

    deadline_detail = _field(evidence, "application_deadline")
    deadline = _instant(row.get("application_deadline_at"))
    deadline_kind = deadline_detail.get("precision")
    if deadline_kind == "EXACT_TIME" and deadline and now > deadline:
        return "closed"
    if deadline_kind == "DATE_ONLY" and deadline:
        source_date = date.fromisoformat(deadline_detail["source_date"])
        cutoff = datetime.combine(source_date + timedelta(days=1), time.min, KST)
        if now >= cutoff:
            return "closed"
    if deadline is None or deadline_kind not in {"EXACT_TIME", "DATE_ONLY"}:
        return None

    start_detail = _field(evidence, "application_start")
    application_start = _instant(row.get("application_start_at"))
    if start_detail.get("precision") == "EXACT_TIME" and application_start and now < application_start:
        return "scheduled"
    if start_detail.get("precision") == "DATE_ONLY" and application_start:
        source_date = date.fromisoformat(start_detail["source_date"])
        if now < datetime.combine(source_date, time.min, KST):
            return "scheduled"
    if application_start and start_detail.get("precision") not in {"EXACT_TIME", "DATE_ONLY"}:
        return None
    return "open"


def transition_decision(row: dict[str, Any], evidence: dict[str, Any], now: datetime) -> tuple[str | None, bool, str]:
    current = row["status"]
    if current in {"draft", "cancelled", "ended"}:
        return None, False, "PROTECTED_STATUS"
    target = target_status(row, evidence, now)
    if target is None:
        return None, False, evidence.get("hold_reason") or "INSUFFICIENT_TIME_EVIDENCE"
    if target == current:
        return target, False, "ALREADY_CURRENT"
    if current == "closed":
        return target, False, "LEGACY_CLOSED_PROVENANCE_UNKNOWN"
    forward = {
        "scheduled": {"open", "closed", "ongoing", "ended"},
        "open": {"closed", "ongoing", "ended"},
        "ongoing": {"ended"},
    }
    if target not in forward.get(current, set()):
        return target, False, "BACKWARD_TRANSITION_BLOCKED"
    return target, True, "SOURCE_EVIDENCE_OR_KST_POLICY"
