"""Source registry operations without persistence or HTTP implementation details."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid4

from agent_collect.repositories.contracts import SourceRepository
from agent_collect.schemas.models import CollectionMethod, Source, SourceType
from agent_collect.schemas.status import SourceStatus
from agent_collect.schemas.validation import require_aware, require_http_url, require_text


def canonicalize_url(url: str) -> str:
    """Normalize a registry identity URL without fetching it or guessing its content."""
    require_http_url(url, "url")
    parts = urlsplit(url)
    host = parts.hostname.encode("idna").decode("ascii").lower()
    port = parts.port
    default_port = 443 if parts.scheme == "https" else 80
    port_suffix = f":{port}" if port is not None and port != default_port else ""
    if ":" in host:
        host = f"[{host}]"
    path = parts.path.rstrip("/") or "/"
    query = urlencode(sorted(
        (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in {"fbclid", "gclid"}
    ))
    return urlunsplit((parts.scheme.lower(), f"{host}{port_suffix}", path, query, ""))


def preferred_method(method: CollectionMethod | None) -> CollectionMethod | None:
    if method in {CollectionMethod.HTTP, CollectionMethod.HTML}:
        return CollectionMethod.HTTP_HTML
    return method


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    source: Source
    created: bool


class SourceRegistryService:
    def __init__(self, repository: SourceRepository) -> None:
        self.repository = repository

    def register(
        self,
        *,
        name: str,
        url: str,
        source_type: SourceType = SourceType.UNKNOWN,
        collection_method: CollectionMethod | None = None,
        provenance: dict[str, Any] | None = None,
        collection_config: dict[str, Any] | None = None,
        category_hint: str | None = None,
        country_code: str | None = None,
        language_code: str | None = None,
        collection_interval_minutes: int | None = None,
        discovery_method: str | None = None,
        agent_rationale: str | None = None,
        confidence: float | None = None,
        discovered_at: datetime | None = None,
    ) -> RegistrationResult:
        require_text(name, "name")
        canonical_url = canonicalize_url(url)
        existing = self.repository.find_by_canonical_url(canonical_url)
        if existing is not None:
            return RegistrationResult(existing, False)
        source = Source(
            id=uuid4(), name=name.strip(), url=url,
            canonical_url=canonical_url, source_type=source_type,
            status=SourceStatus.CANDIDATE, is_active=False,
            collection_method=preferred_method(collection_method),
            category_hint=category_hint, country_code=country_code,
            language_code=language_code,
            collection_interval_minutes=collection_interval_minutes,
            discovery_method=discovery_method, agent_rationale=agent_rationale,
            confidence=confidence,
            discovered_at=discovered_at or datetime.now(timezone.utc),
            provenance=provenance or {}, collection_config=collection_config or {},
        )
        return RegistrationResult(self.repository.save(source), True)

    def get(self, source_id: UUID) -> Source | None:
        return self.repository.get(source_id)

    def update(
        self,
        source_id: UUID,
        *,
        name: str | None = None,
        url: str | None = None,
        collection_method: CollectionMethod | None = None,
        collection_config: dict[str, Any] | None = None,
        category_hint: str | None = None,
        country_code: str | None = None,
        language_code: str | None = None,
        collection_interval_minutes: int | None = None,
        confidence: float | None = None,
    ) -> Source:
        source = self._required(source_id)
        canonical_url = canonicalize_url(url) if url is not None else source.canonical_url
        if (canonical_url != source.canonical_url and
                source.status in {SourceStatus.APPROVED, SourceStatus.ACTIVE}):
            raise ValueError("approved source identity cannot change; return to review")
        if (confidence is not None and confidence != source.confidence and
                source.status in {SourceStatus.APPROVED, SourceStatus.ACTIVE}):
            raise ValueError("approved source trust cannot change; return to review")
        if source.status in {SourceStatus.APPROVED, SourceStatus.ACTIVE} and (
            (collection_method is not None and preferred_method(collection_method) != source.collection_method)
            or (collection_config is not None and collection_config != source.collection_config)
            or (collection_interval_minutes is not None and
                collection_interval_minutes != source.collection_interval_minutes)
        ):
            raise ValueError("approved collection settings cannot change; return to review")
        if canonical_url != source.canonical_url:
            duplicate = self.repository.find_by_canonical_url(canonical_url)
            if duplicate and duplicate.id != source_id:
                raise ValueError("source URL already registered")
        updated = replace(
            source,
            name=name.strip() if name is not None else source.name,
            url=url if url is not None else source.url,
            canonical_url=canonical_url,
            collection_method=(preferred_method(collection_method)
                               if collection_method is not None else source.collection_method),
            collection_config=(collection_config
                               if collection_config is not None else source.collection_config),
            category_hint=category_hint if category_hint is not None else source.category_hint,
            country_code=country_code if country_code is not None else source.country_code,
            language_code=language_code if language_code is not None else source.language_code,
            collection_interval_minutes=(collection_interval_minutes
                                         if collection_interval_minutes is not None
                                         else source.collection_interval_minutes),
            confidence=confidence if confidence is not None else source.confidence,
        )
        return self.repository.save(updated)

    def activate(self, source_id: UUID) -> Source:
        raise ValueError("direct activation disabled; use SourceApprovalService.activate with reviewer and reason")

    def deactivate(self, source_id: UUID) -> Source:
        raise ValueError("direct lifecycle change disabled; use SourceApprovalService.hold")

    def record_collection_result(
        self, source_id: UUID, *, succeeded: bool, collected_at: datetime
    ) -> Source:
        require_aware(collected_at, "collected_at")
        source = self._required(source_id)
        updates = {"last_collected_at": collected_at}
        updates["last_success_at" if succeeded else "last_failure_at"] = collected_at
        return self.repository.save(replace(source, **updates))

    def _required(self, source_id: UUID) -> Source:
        source = self.repository.get(source_id)
        if source is None:
            raise KeyError(f"unknown source: {source_id}")
        return source
