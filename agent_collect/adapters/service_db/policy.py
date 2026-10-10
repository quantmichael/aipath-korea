"""Service-facing date and public-status rules. No inferred event clock times."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from agent_collect.adapters.service_db.contract import ServicePublicationStatus
from agent_collect.schemas.models import PublishCandidate


class DateKind(StrEnum):
    DATE_ONLY = "DATE_ONLY"
    DATETIME_CONFIRMED = "DATETIME_CONFIRMED"
    UNKNOWN = "UNKNOWN"


class TimezoneBasis(StrEnum):
    CONFIRMED = "CONFIRMED"
    DERIVED = "DERIVED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class TemporalAssessment:
    date_kind: DateKind
    timezone_basis: TimezoneBasis
    publishable: bool
    reason: str | None


def assess_temporal(candidate: PublishCandidate) -> TemporalAssessment:
    kind = DateKind(candidate.event_time_kind)
    basis = TimezoneBasis(candidate.timezone_basis)
    if kind is DateKind.DATE_ONLY:
        return TemporalAssessment(kind, basis, False, "DATE_ONLY_NOT_REPRESENTABLE")
    if kind is DateKind.UNKNOWN or candidate.event_start_at is None:
        return TemporalAssessment(kind, basis, False, "UNKNOWN_EVENT_DATETIME")
    if candidate.event_start_at.tzinfo is None:
        return TemporalAssessment(kind, basis, False, "EVENT_TIMEZONE_MISSING")
    if basis is not TimezoneBasis.CONFIRMED:
        return TemporalAssessment(kind, basis, False, "EVENT_TIMEZONE_NOT_CONFIRMED")
    if candidate.application_start_at is not None or candidate.application_deadline_at is not None:
        if candidate.application_time_kind != DateKind.DATETIME_CONFIRMED:
            return TemporalAssessment(kind, basis, False, "APPLICATION_DATETIME_NOT_CONFIRMED")
        if candidate.application_timezone_basis != TimezoneBasis.CONFIRMED:
            return TemporalAssessment(kind, basis, False, "APPLICATION_TIMEZONE_NOT_CONFIRMED")
    return TemporalAssessment(kind, basis, True, None)


def derived_timezone(*, city: str | None, country: str | None) -> tuple[str | None, TimezoneBasis]:
    """Optional display hint, never a confirmed event timezone or clock time."""
    known = {
        ("seoul", "south korea"): "Asia/Seoul",
        ("kyoto", "japan"): "Asia/Tokyo",
        ("tokyo", "japan"): "Asia/Tokyo",
        ("singapore", "singapore"): "Asia/Singapore",
    }
    zone = known.get(((city or "").strip().casefold(), (country or "").strip().casefold()))
    return (zone, TimezoneBasis.DERIVED) if zone else (None, TimezoneBasis.UNKNOWN)


class DeterministicPublicationStatusResolver:
    """Matches existing API visibility without pretending registration is open."""

    def resolve(self, candidate: PublishCandidate, *, at: datetime) -> ServicePublicationStatus | None:
        if at.tzinfo is None:
            raise ValueError("status resolution requires timezone-aware now")
        if not assess_temporal(candidate).publishable:
            return None
        start, end = candidate.event_start_at, candidate.event_end_at
        if end is not None and at > end:
            return ServicePublicationStatus.ENDED
        if start is not None and at >= start:
            return ServicePublicationStatus.ONGOING if end is not None else None
        registration_start = candidate.application_start_at
        registration_end = candidate.application_deadline_at
        if registration_end is not None and at > registration_end:
            return ServicePublicationStatus.CLOSED
        if registration_start is not None and at < registration_start:
            return ServicePublicationStatus.SCHEDULED
        if registration_start is not None and at >= registration_start:
            return ServicePublicationStatus.OPEN
        if registration_end is not None and at <= registration_end:
            return ServicePublicationStatus.OPEN
        return None
