"""Bounded GET transport with DNS-pinned sockets and checked redirects."""

import http.client
import re
import socket
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

from agent_collect.collectors.security import (
    CollectionTransportError,
    Resolver,
    resolve_public_address,
    validate_target_url,
)


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    content: bytes
    final_url: str


Dialer = Callable[[str, str, int, str, str, Mapping[str, str], float, int],
                  tuple[int, dict[str, str], bytes]]


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self.address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self.address, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self) -> None:
        sock = socket.create_connection((self.address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


def _dial_pinned(
    scheme: str, host: str, port: int, address: str, path: str,
    headers: Mapping[str, str], timeout: float, max_bytes: int,
) -> tuple[int, dict[str, str], bytes]:
    connection = (
        _PinnedHTTPSConnection(host, port, address, timeout)
        if scheme == "https" else _PinnedHTTPConnection(host, port, address, timeout)
    )
    try:
        connection.request("GET", path, headers=dict(headers))
        response = connection.getresponse()
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        if response.status in {301, 302, 303, 307, 308}:
            return response.status, response_headers, b""
        length = response_headers.get("content-length")
        if length and length.isdigit() and int(length) > max_bytes:
            raise CollectionTransportError("response_too_large", "Content-Length exceeds limit")
        if response_headers.get("content-encoding", "identity").lower() != "identity":
            raise CollectionTransportError("unsupported_encoding", "compressed response is unsupported")
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise CollectionTransportError("response_too_large", "response exceeds limit")
        return response.status, response_headers, body
    finally:
        connection.close()


def _safe_headers(headers: Mapping[str, str]) -> dict[str, str]:
    clean: dict[str, str] = {}
    for name, value in headers.items():
        if not re.fullmatch(r"[A-Za-z0-9-]+", name) or name.lower() in {
            "host", "content-length", "transfer-encoding", "connection",
        }:
            raise CollectionTransportError("invalid_header", "forbidden request header")
        if "\r" in value or "\n" in value:
            raise CollectionTransportError("invalid_header", "header contains line break")
        clean[name] = value
    clean.setdefault("Accept-Encoding", "identity")
    return clean


class SafeHttpTransport:
    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        dialer: Dialer | None = None,
    ) -> None:
        self.resolver = resolver
        self.dialer = dialer or _dial_pinned

    def fetch(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: float,
        max_bytes: int,
        max_redirects: int,
    ) -> HttpResponse:
        if timeout <= 0 or max_bytes <= 0 or max_redirects < 0:
            raise ValueError("invalid transport limits")
        current = url
        request_headers = _safe_headers(headers)
        visited: set[str] = set()
        for redirect_count in range(max_redirects + 1):
            scheme, host, port, path = validate_target_url(current)
            normalized = urlunsplit((scheme, urlsplit(current).netloc, path, "", ""))
            if normalized in visited:
                raise CollectionTransportError("redirect_loop", "redirect loop detected")
            visited.add(normalized)
            address = resolve_public_address(
                host, port, **({"resolver": self.resolver} if self.resolver else {})
            )
            try:
                status, response_headers, content = self.dialer(
                    scheme, host, port, address, path, request_headers, timeout, max_bytes
                )
            except CollectionTransportError:
                raise
            except (OSError, TimeoutError, ssl.SSLError, http.client.HTTPException) as error:
                code = "timeout" if isinstance(error, (TimeoutError, socket.timeout)) else "http_error"
                raise CollectionTransportError(code, str(error)) from error
            if len(content) > max_bytes:
                raise CollectionTransportError("response_too_large", "response exceeds limit")
            response_headers = {key.lower(): value for key, value in response_headers.items()}
            if status not in {301, 302, 303, 307, 308}:
                return HttpResponse(status, response_headers, content, current)
            location = response_headers.get("location")
            if not location:
                raise CollectionTransportError("invalid_redirect", "redirect lacks Location")
            if redirect_count >= max_redirects:
                raise CollectionTransportError("too_many_redirects", "redirect limit exceeded")
            next_url = urljoin(current, location)
            next_scheme, next_host, next_port, _ = validate_target_url(next_url)
            if (next_scheme, next_host, next_port) != (scheme, host, port):
                request_headers = {
                    key: value for key, value in request_headers.items()
                    if key.lower() in {"accept", "accept-encoding", "user-agent"}
                }
            current = next_url
        raise CollectionTransportError("too_many_redirects", "redirect limit exceeded")
