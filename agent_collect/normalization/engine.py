"""Normalize only extraction fields already validated against source evidence."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from agent_collect.extraction.schema import FIELD_NAMES
from agent_collect.extraction.validation import parse_explicit_datetime


NORMALIZATION_VERSION = "conference-temporal-v1"
_DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}")
_TEMPORAL_FIELDS = ("event_start", "event_end", "registration_start", "registration_deadline")
_DERIVED_ZONES = {
    ("seoul", "south korea"): "Asia/Seoul",
    ("kyoto", "japan"): "Asia/Tokyo",
    ("tokyo", "japan"): "Asia/Tokyo",
    ("singapore", "singapore"): "Asia/Singapore",
}


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    payload: dict[str, Any]
    input_sha256: str


def _validated_text(fields: dict[str, Any], name: str) -> str | None:
    field = fields.get(name) or {}
    value = field.get("value")
    if field.get("validation_status") != "VALID":
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _derived_zone(fields: dict[str, Any]) -> str | None:
    city = _validated_text(fields, "city")
    country = _validated_text(fields, "country")
    return _DERIVED_ZONES.get(((city or "").casefold(), (country or "").casefold()))


def _field(fields: dict[str, Any], name: str, *, zone_hint: str | None) -> dict[str, Any]:
    item = fields[name]
    raw = _validated_text(fields, name)
    references = item.get("evidence_block_ids") or []
    evidence = item.get("evidence_text")
    result: dict[str, Any] = {
        "kind": "UNKNOWN", "date": None, "datetime": None,
        "timezone_basis": "UNKNOWN", "timezone_hint": None,
        "evidence_block_ids": list(references),
        "source_url": item.get("source_url"), "reason": None,
    }
    if raw is None:
        raw_attempt = item.get("raw_value")
        result["reason"] = (
            "FIELD_INVALID" if (
                isinstance(raw_attempt, str) and raw_attempt.strip()
            ) or item.get("validation_status") not in {None, "MISSING", "UNVALIDATED"}
            else "FIELD_NOT_VALIDATED"
        )
        return result
    if not references or not isinstance(evidence, str) or not evidence.strip():
        result["reason"] = "TEMPORAL_EVIDENCE_MISSING"
        return result
    instant = parse_explicit_datetime(raw)
    if instant is not None:
        result.update(kind="DATETIME_CONFIRMED", date=instant.date().isoformat(),
                      datetime=instant.isoformat(), timezone_basis="CONFIRMED")
        return result
    if _DATE_ONLY.fullmatch(raw):
        try:
            normalized_date = date.fromisoformat(raw).isoformat()
        except ValueError:
            result["reason"] = "INVALID_CALENDAR_DATE"
            return result
        result.update(kind="DATE_ONLY", date=normalized_date,
                      timezone_basis="DERIVED" if zone_hint else "UNKNOWN",
                      timezone_hint=zone_hint)
        return result
    result["reason"] = "AMBIGUOUS_OR_UNSUPPORTED_TEMPORAL_VALUE"
    return result


def _period_kind(start: dict[str, Any], end: dict[str, Any]) -> tuple[str, str]:
    if start["kind"] == "UNKNOWN":
        return "UNKNOWN", "UNKNOWN"
    present = (start,) if end["reason"] == "FIELD_NOT_VALIDATED" else (start, end)
    if any(item["kind"] == "UNKNOWN" for item in present):
        return "UNKNOWN", "UNKNOWN"
    if all(item["kind"] == "DATETIME_CONFIRMED" for item in present):
        return "DATETIME_CONFIRMED", "CONFIRMED"
    hint = next((item["timezone_hint"] for item in present if item["timezone_hint"]), None)
    return "DATE_ONLY", "DERIVED" if hint else "UNKNOWN"


def normalize_extracted_payload(extracted_payload: dict[str, Any]) -> NormalizationResult:
    if not isinstance(extracted_payload, dict):
        raise ValueError("extracted_payload must be an object")
    fields = extracted_payload.get("fields")
    if not isinstance(fields, dict) or set(fields) != set(FIELD_NAMES):
        raise ValueError("extracted_payload requires complete Conference fields")
    if not all(isinstance(field, dict) for field in fields.values()):
        raise ValueError("extraction fields must be objects")
    input_hash = extraction_input_sha256(extracted_payload)
    zone_hint = _derived_zone(fields)
    normalized_fields = {
        name: _field(fields, name, zone_hint=zone_hint) for name in _TEMPORAL_FIELDS
    }
    event_kind, event_basis = _period_kind(normalized_fields["event_start"],
                                           normalized_fields["event_end"])
    registration_start = normalized_fields["registration_start"]
    registration_end = normalized_fields["registration_deadline"]
    if registration_start["kind"] == "UNKNOWN" and registration_start["reason"] == "FIELD_NOT_VALIDATED":
        application_kind, application_basis = _period_kind(registration_end, registration_start)
    else:
        application_kind, application_basis = _period_kind(registration_start, registration_end)
    issues = []
    event_order_invalid = bool(
        normalized_fields["event_start"]["date"] and normalized_fields["event_end"]["date"]
        and normalized_fields["event_end"]["date"] < normalized_fields["event_start"]["date"]
    )
    application_order_invalid = bool(
        registration_start["date"] and registration_end["date"]
        and registration_end["date"] < registration_start["date"]
    )
    if event_order_invalid:
        event_kind, event_basis = "UNKNOWN", "UNKNOWN"
        issues.append("EVENT_DATE_ORDER_INVALID")
    if application_order_invalid:
        application_kind, application_basis = "UNKNOWN", "UNKNOWN"
        issues.append("APPLICATION_DATE_ORDER_INVALID")
    if normalized_fields["event_start"]["kind"] == "UNKNOWN":
        issues.append("EVENT_START_UNAVAILABLE")
    for name, item in normalized_fields.items():
        if item["reason"] not in {None, "FIELD_NOT_VALIDATED"}:
            issues.append(f"{name.upper()}:{item['reason']}")
    if event_kind == "DATE_ONLY":
        issues.append("EVENT_DATE_ONLY")
    if application_kind == "DATE_ONLY":
        issues.append("APPLICATION_DATE_ONLY")
    payload: dict[str, Any] = {
        "normalization_version": NORMALIZATION_VERSION,
        "input_sha256": input_hash,
        "status": "REVIEW_REQUIRED" if issues else "NORMALIZED",
        "reason_codes": issues,
        "fields": normalized_fields,
        "event_time_kind": event_kind,
        "timezone_basis": event_basis,
        "application_time_kind": application_kind,
        "application_timezone_basis": application_basis,
    }
    for name, item in normalized_fields.items():
        payload[name] = item["date"]
        db_name = {"registration_start": "application_start_at",
                   "registration_deadline": "application_deadline_at"}.get(name, f"{name}_at")
        invalid_order = (event_order_invalid if name.startswith("event_")
                         else application_order_invalid)
        payload[db_name] = None if invalid_order else item["datetime"]
    return NormalizationResult(payload, input_hash)


def extraction_input_sha256(extracted_payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        extracted_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
