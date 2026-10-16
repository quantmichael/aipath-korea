"""Service schema mapping and conservative source/category/tag resolution."""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID

from agent_collect.dedup.engine import normalize_title
from agent_collect.registry.service import canonicalize_url
from agent_collect.registry.approval import source_is_approved, source_is_collectible
from agent_collect.schemas.models import PublishCandidate, Source, SourceType
from agent_collect.schemas.status import SourceStatus


class ConflictStatus(StrEnum):
    INSERT_ALLOWED = "INSERT_ALLOWED"
    DUPLICATE = "DUPLICATE"
    CONFLICT = "CONFLICT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass(frozen=True, slots=True)
class ConflictDecision:
    status: ConflictStatus
    reason: str
    matched_record_id: UUID | None = None


class ServiceTables(Protocol):
    def list_sources(self) -> list[dict[str, Any]]: ...
    def get_conference_category(self) -> dict[str, Any] | None: ...
    def list_tags(self, slugs: tuple[str, ...]) -> list[dict[str, Any]]: ...
    def list_opportunities(self) -> list[dict[str, Any]]: ...
    def register_opportunity(self, payload: dict[str, Any],
                             evidence: dict[str, Any]) -> tuple[dict[str, Any], str]: ...
    def insert_opportunity(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def register_time_evidence(self, record_id: UUID, evidence: dict[str, Any]) -> None: ...
    def get_opportunity(self, record_id: UUID) -> dict[str, Any] | None: ...
    def public_archive_contains(self, record_id: UUID) -> bool: ...


def _canonical(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return canonicalize_url(url)
    except (ValueError, TypeError):
        return None


def _domain(url: str | None) -> str | None:
    canonical = _canonical(url)
    if canonical is None:
        return None
    host = urlsplit(canonical).hostname or ""
    return host.removeprefix("www.")


@dataclass(frozen=True, slots=True)
class AgentSourceIdentity:
    agent_source_id: UUID
    canonical_url: str
    normalized_domain: str
    normalized_name: str


def approved_source_identity(source: Source) -> AgentSourceIdentity:
    """Prepare stable identity only after human approval; no Service I/O."""
    if not (source_is_approved(source) or source_is_collectible(source)):
        raise ValueError("reviewed approval required for Service Source identity")
    canonical = _canonical(source.canonical_url or source.url)
    domain = _domain(canonical)
    if not canonical or not domain:
        raise ValueError("invalid approved Source URL")
    return AgentSourceIdentity(source.id, canonical, domain, normalize_title(source.name))


def preview_approved_source_mapping(
    source: Source, rows: list[dict[str, Any]], *, explicit_service_source_id: UUID | None = None,
) -> tuple[UUID | None, str]:
    """Read-only deterministic preview. Missing/ambiguous identity remains REVIEW."""
    identity = approved_source_identity(source)
    active = [row for row in rows if row.get("is_active")]
    if explicit_service_source_id is not None:
        active = [row for row in active if row.get("id") == str(explicit_service_source_id)]
        if len(active) != 1:
            return None, "EXPLICIT_SOURCE_NOT_FOUND"
    exact = [row for row in active if _canonical(row.get("homepage_url")) == identity.canonical_url]
    if len(exact) == 1:
        return UUID(exact[0]["id"]), "EXACT_HOMEPAGE"
    if len(exact) > 1:
        return None, "AMBIGUOUS_SOURCE_HOMEPAGE"
    same_identity = [row for row in active if
                     _domain(row.get("homepage_url")) == identity.normalized_domain and
                     normalize_title(row.get("name")) == identity.normalized_name]
    if len(same_identity) == 1:
        return UUID(same_identity[0]["id"]), "DOMAIN_AND_NAME"
    return None, "SOURCE_NOT_FOUND_OR_AMBIGUOUS"


def map_source(candidate: PublishCandidate, rows: list[dict[str, Any]]) -> tuple[UUID | None, str]:
    target_url = candidate.source_homepage_url or candidate.official_url
    target_canonical = _canonical(target_url)
    exact = [row for row in rows if target_canonical and row.get("is_active") and
             _canonical(row.get("homepage_url")) == target_canonical]
    if len(exact) == 1:
        return UUID(exact[0]["id"]), "EXACT_HOMEPAGE"
    if len(exact) > 1:
        return None, "AMBIGUOUS_SOURCE_HOMEPAGE"
    target_domain = _domain(target_url)
    same_domain = [row for row in rows if target_domain and row.get("is_active") and
                   _domain(row.get("homepage_url")) == target_domain]
    same_name = [row for row in same_domain if
                 normalize_title(row.get("name")) == normalize_title(candidate.source_name)]
    if len(same_name) == 1:
        return UUID(same_name[0]["id"]), "DOMAIN_AND_NAME"
    return None, "SOURCE_NOT_FOUND_OR_AMBIGUOUS"


def source_creation_eligible(candidate: PublishCandidate, source: Source | None,
                             *, threshold: float) -> bool:
    """Eligibility for manual approval only; never creates a Service Source."""
    return bool(source and source.id == candidate.agent_source_id and
                source.source_type is SourceType.OFFICIAL and
                source.status is SourceStatus.ACTIVE and source.is_active and
                (source.confidence or 0) >= threshold and
                _domain(source.url) == _domain(candidate.source_homepage_url or candidate.official_url))


def stable_slug(candidate: PublishCandidate) -> str:
    title = re.sub(r"[^a-z0-9]+", "-", candidate.title.casefold()).strip("-")[:70].strip("-")
    return f"{title or 'conference'}-{candidate.agent_item_id.hex}"


def stable_external_id(candidate: PublishCandidate) -> str:
    return f"runtime_agent_publish_smoke_test:{candidate.agent_item_id}"


def _event_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def final_conflict(candidate: PublishCandidate, rows: list[dict[str, Any]],
                   *, source_id: UUID) -> ConflictDecision:
    candidate_url = _canonical(candidate.official_url)
    candidate_date = candidate.event_start_at.date().isoformat() if candidate.event_start_at else None
    candidate_title = normalize_title(candidate.title)
    candidate_org = normalize_title(candidate.organizer)
    outcomes: list[tuple[int, ConflictDecision]] = []
    for row in rows:
        record_id = UUID(row["id"])
        if row.get("slug") == stable_slug(candidate) or (
            row.get("source_id") == str(source_id) and
            row.get("external_id") == stable_external_id(candidate)
        ):
            outcomes.append((100, ConflictDecision(ConflictStatus.DUPLICATE, "AGENT_IDEMPOTENCY_KEY", record_id)))
            continue
        row_date = _event_date(row.get("event_start_at"))
        row_url = _canonical(row.get("official_url"))
        if candidate_url and row_url == candidate_url:
            if candidate_date and row_date and candidate_date[:4] != row_date[:4]:
                outcomes.append((80, ConflictDecision(ConflictStatus.CONFLICT,
                                                      "REUSED_OFFICIAL_URL_DIFFERENT_YEAR", record_id)))
            else:
                outcomes.append((90, ConflictDecision(ConflictStatus.DUPLICATE, "OFFICIAL_URL", record_id)))
            continue
        row_title = normalize_title(row.get("title"))
        if candidate_title and row_title == candidate_title:
            if candidate_date and row_date and candidate_date == row_date:
                outcomes.append((70, ConflictDecision(ConflictStatus.DUPLICATE, "TITLE_AND_DATE", record_id)))
                continue
            if not row_date:
                outcomes.append((50, ConflictDecision(ConflictStatus.REVIEW_REQUIRED,
                                                      "TITLE_WITH_UNKNOWN_DATE", record_id)))
                continue
        if (candidate_org and normalize_title(row.get("organizer")) == candidate_org
                and candidate_date and row_date == candidate_date):
            outcomes.append((60, ConflictDecision(ConflictStatus.CONFLICT, "ORGANIZER_AND_DATE", record_id)))
    if outcomes:
        return max(outcomes, key=lambda item: item[0])[1]
    return ConflictDecision(ConflictStatus.INSERT_ALLOWED, "NO_MATCH")
