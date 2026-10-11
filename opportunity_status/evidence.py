"""Build conservative per-field time evidence for Service DB lifecycle status.

Stored ``timestamptz`` values are never proof of the source's clock precision.
Only a source literal with an explicit offset, or a known date-only extraction
path, may establish a boundary. This module performs no database I/O.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

FIELDS = (
    "application_start_at",
    "application_deadline_at",
    "event_start_at",
    "event_end_at",
)
OFFSET_DATETIME = re.compile(
    r"T[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]+)?)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
DATE_ONLY = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def _instant(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo is not None else None


def _known_date_only_path(raw: dict[str, Any], field: str) -> bool:
    detail = raw.get("detail_collector")
    if detail == "dacon_detail":
        extracted = raw.get("status_date_evidence")
        return isinstance(extracted, dict) and field in extracted
    return (
        raw.get("collector") == "api"
        and isinstance(raw.get("row"), dict)
        and field.startswith("application_")
        and ("reqstBeginEndDe" in raw["row"] or "reqstEndDe" in raw["row"])
    )


def _bizinfo_source_date(raw: dict[str, Any], field: str) -> str | None:
    row = raw.get("row")
    if not isinstance(row, dict):
        return None
    period = str(row.get("reqstBeginEndDe") or "")
    found = re.findall(r"([0-9]{4})[.\-/]?([0-9]{1,2})[.\-/]?([0-9]{1,2})", period)
    dates = [f"{int(y):04d}-{int(m):02d}-{int(d):02d}" for y, m, d in found]
    if field == "application_start_at" and len(dates) >= 2:
        return dates[0]
    if field == "application_deadline_at" and dates:
        return dates[-1]
    key = "reqstBeginDe" if field == "application_start_at" else "reqstEndDe"
    fallback = str(row.get(key) or "")
    match = re.search(r"([0-9]{4})[.\-/]?([0-9]{1,2})[.\-/]?([0-9]{1,2})", fallback)
    if match:
        y, m, d = match.groups()
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    return None


def _collect_field(candidate: dict[str, Any], field: str) -> dict[str, str]:
    value = candidate.get(field)
    stored = _instant(value)
    raw = candidate.get("raw_payload") or {}
    manual = (raw.get("status_time_evidence") or {}).get(field) or {}
    literal = manual.get("source_literal")
    source_ref = manual.get("source_ref")
    if (stored is not None and isinstance(literal, str)
            and isinstance(source_ref, str) and source_ref.strip()
            and OFFSET_DATETIME.search(literal)):
        exact = _instant(literal)
        if exact is not None and exact.astimezone(timezone.utc) == stored.astimezone(timezone.utc):
            return {"precision": "EXACT_TIME", "source_literal": literal,
                    "source_ref": source_ref}

    # Known collectors returned a calendar date; a value later replaced with
    # a non-midnight timestamp cannot inherit that collector's date-only claim.
    if stored is not None and _known_date_only_path(raw, field):
        utc = stored.astimezone(timezone.utc)
        if utc.hour == utc.minute == utc.second == utc.microsecond == 0:
            source_date = utc.date().isoformat()
            extracted = raw.get("status_date_evidence")
            bizinfo_date = (_bizinfo_source_date(raw, field)
                            if raw.get("collector") == "api" else None)
            if DATE_ONLY.fullmatch(source_date) and (
                not isinstance(extracted, dict)
                or extracted.get(field) == source_date
            ) and (raw.get("collector") != "api" or bizinfo_date == source_date):
                return {"precision": "DATE_ONLY", "source_date": source_date,
                        "source_ref": str(raw.get("detail_collector") or raw.get("collector"))}
    if isinstance(literal, str) and isinstance(source_ref, str) and source_ref.strip():
        return {"precision": "UNKNOWN", "source_literal": literal,
                "source_ref": source_ref}
    return {"precision": "UNKNOWN"}


def _hold_reason(fields: dict[str, dict[str, str]]) -> str | None:
    deadline = fields["application_deadline_at"]["precision"]
    event_end = fields["event_end_at"]["precision"]
    if deadline == "UNKNOWN":
        return ("REGISTRATION_DEADLINE_UNKNOWN_EVENT_END_ONLY"
                if event_end == "EXACT_TIME"
                else "NO_CONFIRMED_DEADLINE_OR_EVENT_END")
    if fields["event_start_at"]["precision"] == "DATE_ONLY" or event_end == "DATE_ONLY":
        return "EVENT_DATE_ONLY_HAS_NO_PHASE_POLICY"
    return None


def build_collect_evidence(candidate: dict[str, Any]) -> dict[str, Any]:
    fields = {field: _collect_field(candidate, field) for field in FIELDS}
    ref = candidate.get("id") or candidate.get("slug")
    if not ref:
        raise ValueError("candidate identity required for time evidence")
    payload: dict[str, Any] = {
        "evidence_ref": f"collect-candidate:{ref}",
        **{field.removesuffix("_at"): detail for field, detail in fields.items()},
    }
    reason = _hold_reason(fields)
    if reason:
        payload["hold_reason"] = reason
    return payload


def build_agent_evidence(candidate: Any) -> dict[str, Any]:
    """Use metadata that already passed Agent verification and Publish Gate."""
    refs = tuple(candidate.evidence_refs)
    if not refs:
        raise ValueError("Agent evidence refs required")
    fields: dict[str, dict[str, str]] = {}
    for field in FIELDS:
        value = getattr(candidate, field)
        application = field.startswith("application_")
        kind = candidate.application_time_kind if application else candidate.event_time_kind
        basis = candidate.application_timezone_basis if application else candidate.timezone_basis
        if value is not None and kind == "DATETIME_CONFIRMED" and basis == "CONFIRMED":
            instant = _instant(value)
            if instant is None:
                raise ValueError("Agent source datetime must be timezone aware")
            fields[field] = {"precision": "EXACT_TIME",
                             "source_literal": instant.isoformat(),
                             "source_ref": refs[0]}
        else:
            fields[field] = {"precision": "UNKNOWN"}
    payload: dict[str, Any] = {
        "evidence_ref": f"agent-item:{candidate.agent_item_id}",
        **{field.removesuffix("_at"): detail for field, detail in fields.items()},
    }
    reason = _hold_reason(fields)
    if reason:
        payload["hold_reason"] = reason
    return payload
