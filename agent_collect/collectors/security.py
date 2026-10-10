"""URL and DNS checks for outbound collection requests."""

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit


class CollectionTransportError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class UnsafeTargetError(CollectionTransportError):
    def __init__(self, message: str) -> None:
        super().__init__("unsafe_url", message)


Resolver = Callable[..., list[tuple]]


def validate_target_url(url: str) -> tuple[str, str, int, str]:
    """Return scheme, host, port and request path after syntactic checks."""
    if not isinstance(url, str) or not url or any(char.isspace() for char in url):
        raise UnsafeTargetError("URL is empty or contains whitespace")
    if "\\" in url or any(ord(char) < 32 for char in url):
        raise UnsafeTargetError("URL contains forbidden characters")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError as error:
        raise UnsafeTargetError("malformed URL") from error
    if parts.scheme not in {"http", "https"} or not host:
        raise UnsafeTargetError("only absolute HTTP(S) URLs are supported")
    if parts.username or parts.password or "@" in parts.netloc:
        raise UnsafeTargetError("URL credentials are forbidden")
    host = host.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal")):
        raise UnsafeTargetError("local hostnames are forbidden")
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    if port <= 0:
        raise UnsafeTargetError("invalid port")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise UnsafeTargetError("non-public IP address is forbidden")
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return parts.scheme, host, port, path


def resolve_public_address(
    host: str,
    port: int,
    *,
    resolver: Resolver = socket.getaddrinfo,
) -> str:
    """Reject mixed/private DNS answers; return a public address to pin the socket."""
    try:
        records = resolver(host, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise CollectionTransportError("dns_error", str(error)) from error
    if not records:
        raise CollectionTransportError("dns_error", "DNS returned no addresses")
    addresses: list[str] = []
    for record in records:
        address = record[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as error:
            raise UnsafeTargetError("DNS returned malformed address") from error
        if not ip.is_global:
            raise UnsafeTargetError("DNS returned a non-public address")
        addresses.append(str(ip))
    return addresses[0]
