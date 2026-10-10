"""Deterministic validation shared by domain models."""

from datetime import datetime
from enum import Enum
from urllib.parse import urlsplit
from uuid import UUID


def require_uuid(value: UUID, field: str) -> None:
    if not isinstance(value, UUID):
        raise ValueError(f"{field} must be UUID")


def require_enum(value: Enum, enum_type: type[Enum], field: str) -> None:
    if not isinstance(value, enum_type):
        raise ValueError(f"{field} must be {enum_type.__name__}")


def require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")


def require_http_url(value: str, field: str) -> None:
    require_text(value, field)
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"{field} must be an absolute HTTP(S) URL")
    try:
        _ = parts.port
    except ValueError as error:
        raise ValueError(f"{field} has an invalid port") from error
    if parts.username or parts.password:
        raise ValueError(f"{field} must not contain credentials")


def require_aware(value: datetime, field: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    if value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def require_confidence(value: float, field: str = "confidence") -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{field} must be between 0 and 1")


def require_date_order(
    start: datetime | None, end: datetime | None, label: str
) -> None:
    if start is not None:
        require_aware(start, f"{label}_start_at")
    if end is not None:
        require_aware(end, f"{label}_end_at")
    if start is not None and end is not None and start > end:
        raise ValueError(f"{label} start must not follow end")
