"""Replaceable search provider contract and an offline fixture provider."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from agent_collect.discovery.models import DiscoveryQuery, SearchResult
from agent_collect.registry.service import canonicalize_url


class SearchProvider(Protocol):
    name: str

    def search(self, query: DiscoveryQuery, *, policy: "SearchRequestPolicy") -> list[SearchResult]: ...


@dataclass(frozen=True, slots=True)
class SearchRequestPolicy:
    max_results: int = 5
    timeout_seconds: int = 10

    def __post_init__(self) -> None:
        if not 1 <= self.max_results <= 20:
            raise ValueError("max_results must be between 1 and 20")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


class SearchProviderError(Exception):
    def __init__(self, code: str, *, retryable: bool, status_code: int | None = None,
                 retry_after_seconds: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class SearchRateLimitError(SearchProviderError):
    def __init__(self, *, retry_after_seconds: int | None = None) -> None:
        super().__init__("rate_limited", retryable=True, status_code=429,
                         retry_after_seconds=retry_after_seconds)


@dataclass(frozen=True, slots=True)
class SearchHit:
    title: str
    url: str
    snippet: str = ""


def normalize_hit(
    hit: SearchHit, query: DiscoveryQuery, *, rank: int, provider: str,
    discovered_at: datetime | None = None,
) -> SearchResult:
    """Preserve bad provider URLs as results so the filter can record why they failed."""
    try:
        canonical = canonicalize_url(hit.url)
        domain = urlsplit(canonical).hostname
    except (ValueError, UnicodeError, AttributeError):
        canonical = None
        domain = None
    return SearchResult(
        id=uuid4(), query_id=query.id, provider=provider, rank=rank,
        title=hit.title.strip() or "(untitled search result)",
        original_url=hit.url, snippet=hit.snippet,
        discovered_at=discovered_at or datetime.now(timezone.utc),
        canonical_url=canonical, domain=domain,
    )


class FakeSearchProvider:
    name = "fake"

    def __init__(self, hits: list[SearchHit]) -> None:
        self.hits = list(hits)
        self.calls: list[DiscoveryQuery] = []

    def search(self, query: DiscoveryQuery, *, policy: SearchRequestPolicy) -> list[SearchResult]:
        self.calls.append(query)
        return [normalize_hit(hit, query, rank=index, provider=self.name)
                for index, hit in enumerate(self.hits[:policy.max_results], 1)]
