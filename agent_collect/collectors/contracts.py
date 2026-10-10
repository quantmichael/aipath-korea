"""Common collection input/output contracts for every generic collector."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from agent_collect.schemas.models import Source
from agent_collect.schemas.validation import require_aware, require_http_url


@dataclass(frozen=True, slots=True)
class CollectionRequest:
    requested_url: str
    timeout_seconds: float
    max_response_bytes: int
    max_redirects: int
    max_retries: int
    retry_backoff_seconds: float
    user_agent: str
    headers: dict[str, str] = field(default_factory=dict)
    query_params: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_http_url(self.requested_url, "requested_url")
        if self.timeout_seconds <= 0 or self.max_response_bytes <= 0:
            raise ValueError("timeout and response limit must be positive")
        if self.max_redirects < 0 or self.max_retries < 0 or self.retry_backoff_seconds < 0:
            raise ValueError("redirect/retry limits must be non-negative")
        if not self.user_agent.strip():
            raise ValueError("user_agent must be non-empty")
        if any(not isinstance(k, str) or not isinstance(v, str)
               for k, v in self.headers.items()):
            raise ValueError("headers must contain text keys and values")
        if any(not isinstance(k, str) or not isinstance(v, str)
               for k, v in self.query_params.items()):
            raise ValueError("query_params must contain text keys and values")

    @property
    def url_with_query(self) -> str:
        parts = urlsplit(self.requested_url)
        query = parse_qsl(parts.query, keep_blank_values=True)
        query.extend(self.query_params.items())
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


@dataclass(frozen=True, slots=True)
class CollectionError:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class CollectionResult:
    source_id: UUID
    requested_url: str
    final_url: str | None
    http_status: int | None
    content_type: str | None
    content: bytes
    content_hash: str | None
    collected_at: datetime
    elapsed_time: float
    metadata: dict[str, Any] = field(default_factory=dict)
    error: CollectionError | None = None

    def __post_init__(self) -> None:
        require_aware(self.collected_at, "collected_at")
        if self.elapsed_time < 0:
            raise ValueError("elapsed_time must be non-negative")
        if self.error is None and (not self.content or not self.content_hash):
            raise ValueError("successful collection requires content and hash")

    @property
    def succeeded(self) -> bool:
        return self.error is None


class Collector(Protocol):
    def collect(self, source: Source, request: CollectionRequest) -> CollectionResult: ...
