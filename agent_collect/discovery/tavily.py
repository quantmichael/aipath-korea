"""Minimal Tavily Search adapter; never persists provider payload or API key."""

import json
import socket
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from agent_collect.discovery.models import DiscoveryQuery, SearchResult
from agent_collect.discovery.providers import (
    SearchHit, SearchProviderError, SearchRateLimitError, SearchRequestPolicy,
    normalize_hit,
)


Transport = Callable[[bytes, Mapping[str, str], float], tuple[int, Mapping[str, str], bytes]]
MAX_RESPONSE_BYTES = 1_000_000
MAX_SNIPPET_CHARS = 500


def _http_transport(payload: bytes, headers: Mapping[str, str], timeout: float
                    ) -> tuple[int, Mapping[str, str], bytes]:
    request = Request("https://api.tavily.com/search", data=payload,
                      headers=dict(headers), method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        # Discard response body: errors can contain untrusted/request text.
        return error.code, dict(error.headers or {}), b""


class TavilySearchProvider:
    name = "tavily"

    def __init__(self, api_key: str | None, *, transport: Transport | None = None) -> None:
        self._api_key = api_key or ""
        self._transport = transport or _http_transport

    @property
    def credential_present(self) -> bool:
        return bool(self._api_key.strip())

    def search(self, query: DiscoveryQuery, *, policy: SearchRequestPolicy) -> list[SearchResult]:
        if not self.credential_present:
            raise SearchProviderError("missing_credential", retryable=False)
        payload = json.dumps({
            "query": query.text, "topic": "general", "search_depth": "basic",
            "max_results": policy.max_results, "include_answer": False,
            "include_raw_content": False, "include_images": False,
            "include_usage": False,
        }).encode("utf-8")
        headers = {"Authorization": f"Bearer {self._api_key}",
                   "Content-Type": "application/json"}
        try:
            status, response_headers, body = self._transport(payload, headers, policy.timeout_seconds)
        except (TimeoutError, socket.timeout):
            raise SearchProviderError("timeout", retryable=True) from None
        except (URLError, OSError):
            raise SearchProviderError("network_failure", retryable=True) from None
        if status == 429:
            retry_after = response_headers.get("Retry-After") or response_headers.get("retry-after")
            seconds = int(retry_after) if retry_after and retry_after.isdigit() else None
            raise SearchRateLimitError(retry_after_seconds=seconds)
        if status in (401, 403):
            raise SearchProviderError("authentication_failure", retryable=False, status_code=status)
        if status != 200:
            raise SearchProviderError("http_error", retryable=status >= 500, status_code=status)
        if len(body) > MAX_RESPONSE_BYTES:
            raise SearchProviderError("malformed_response", retryable=False)
        try:
            document = json.loads(body)
            if not isinstance(document, dict) or not isinstance(document.get("results"), list):
                raise ValueError("results missing")
            results = document["results"]
            if len(results) > policy.max_results:
                raise ValueError("too many results")
            hits = []
            for row in results:
                if not isinstance(row, dict) or not isinstance(row.get("title"), str) or not isinstance(row.get("url"), str):
                    raise ValueError("invalid result")
                hits.append(SearchHit(
                    title=row["title"][:500], url=row["url"][:2048],
                    snippet=str(row.get("content") or "")[:MAX_SNIPPET_CHARS],
                ))
        except (ValueError, TypeError, UnicodeDecodeError):
            raise SearchProviderError("malformed_response", retryable=False) from None
        return [normalize_hit(hit, query, rank=index, provider=self.name)
                for index, hit in enumerate(hits, 1)]
