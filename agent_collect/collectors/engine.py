"""HTML, RSS and JSON API collection through one transport contract."""

import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.message import Message
from html.parser import HTMLParser
from typing import Any, Callable, Protocol

from agent_collect.collectors.contracts import (
    CollectionError,
    CollectionRequest,
    CollectionResult,
)
from agent_collect.collectors.security import CollectionTransportError, UnsafeTargetError
from agent_collect.collectors.transport import HttpResponse, SafeHttpTransport
from agent_collect.schemas.models import Source


class HttpTransport(Protocol):
    def fetch(
        self, url: str, *, headers: dict[str, str], timeout: float,
        max_bytes: int, max_redirects: int,
    ) -> HttpResponse: ...


def _media_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _decode(content: bytes, content_type: str | None) -> str:
    message = Message()
    message["content-type"] = content_type or ""
    charset = message.get_param("charset", header="content-type")
    if charset is None:
        match = re.search(rb"charset\s*=\s*['\"]?([A-Za-z0-9_-]+)", content[:2048], re.I)
        charset = match.group(1).decode("ascii") if match else "utf-8"
    try:
        return content.decode(charset)
    except LookupError as error:
        raise ValueError(f"unable to decode response with charset {charset}") from error
    except UnicodeError:
        return content.decode("utf-8", errors="replace")


class _TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)


class BaseCollector:
    def __init__(
        self,
        transport: HttpTransport | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.transport = transport or SafeHttpTransport()
        self.sleep = sleep

    def collect(self, source: Source, request: CollectionRequest) -> CollectionResult:
        started = time.monotonic()
        url = request.url_with_query
        response: HttpResponse | None = None
        error: CollectionError | None = None
        metadata: dict[str, Any] = {}
        for attempt in range(request.max_retries + 1):
            error = None
            try:
                headers = {"User-Agent": request.user_agent, "Accept": self.accept}
                headers.update(request.headers)
                response = self.transport.fetch(
                    url, headers=headers, timeout=request.timeout_seconds,
                    max_bytes=request.max_response_bytes,
                    max_redirects=request.max_redirects,
                )
                if response.status == 429 or 500 <= response.status <= 599:
                    error = CollectionError("http_status", f"HTTP {response.status}")
                    if attempt < request.max_retries:
                        self.sleep(request.retry_backoff_seconds * (2 ** attempt))
                        continue
                    break
                if response.status < 200 or response.status >= 300:
                    error = CollectionError("http_status", f"HTTP {response.status}")
                    break
                if not response.content:
                    error = CollectionError("empty_body", "response body is empty")
                    break
                try:
                    metadata = self.parse_metadata(response)
                except ValueError as parse_error:
                    error = CollectionError("invalid_content", str(parse_error))
                break
            except UnsafeTargetError as unsafe:
                error = CollectionError(unsafe.code, str(unsafe))
                break
            except CollectionTransportError as transport_error:
                error = CollectionError(transport_error.code, str(transport_error))
                if transport_error.code in {"timeout", "http_error", "dns_error"} and attempt < request.max_retries:
                    self.sleep(request.retry_backoff_seconds * (2 ** attempt))
                    continue
                break
        collected_at = datetime.now(timezone.utc)
        content = response.content if response and error is None else b""
        return CollectionResult(
            source_id=source.id,
            requested_url=url,
            final_url=response.final_url if response else None,
            http_status=response.status if response else None,
            content_type=response.headers.get("content-type") if response else None,
            content=content,
            content_hash=hashlib.sha256(content).hexdigest() if content else None,
            collected_at=collected_at,
            elapsed_time=time.monotonic() - started,
            metadata=metadata,
            error=error,
        )

    @property
    def accept(self) -> str:
        raise NotImplementedError

    def parse_metadata(self, response: HttpResponse) -> dict[str, Any]:
        raise NotImplementedError


class HtmlCollector(BaseCollector):
    @property
    def accept(self) -> str:
        return "text/html, application/xhtml+xml"

    def parse_metadata(self, response: HttpResponse) -> dict[str, Any]:
        content_type = response.headers.get("content-type")
        if _media_type(content_type) not in {"text/html", "application/xhtml+xml"}:
            raise ValueError("response is not HTML")
        parser = _TitleParser()
        parser.feed(_decode(response.content, content_type))
        return {"title": " ".join(parser.title_parts).strip()}


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(element: ET.Element, name: str) -> str | None:
    for child in element:
        if _local_tag(child.tag) == name:
            text = (child.text or "").strip()
            return text or None
    return None


class RssCollector(BaseCollector):
    @property
    def accept(self) -> str:
        return "application/rss+xml, application/atom+xml, application/xml, text/xml"

    def parse_metadata(self, response: HttpResponse) -> dict[str, Any]:
        content_type = response.headers.get("content-type")
        if _media_type(content_type) not in {
            "application/rss+xml", "application/atom+xml", "application/xml",
            "text/xml", "application/rdf+xml",
        }:
            raise ValueError("response is not RSS/Atom XML")
        upper = response.content.upper()
        if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
            raise ValueError("DTD/entity declarations are not supported")
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as error:
            raise ValueError("malformed feed XML") from error
        kind = _local_tag(root.tag)
        if kind == "rss":
            channel = next((child for child in root if _local_tag(child.tag) == "channel"), None)
            if channel is None:
                raise ValueError("RSS channel missing")
            items = [child for child in channel if _local_tag(child.tag) == "item"]
            feed_title = _child_text(channel, "title")
            feed_link = _child_text(channel, "link")
            item_rows = [
                {"title": _child_text(item, "title"), "link": _child_text(item, "link"),
                 "id": _child_text(item, "guid"), "published": _child_text(item, "pubdate")}
                for item in items[:100]
            ]
        elif kind == "feed":
            items = [child for child in root if _local_tag(child.tag) == "entry"]
            feed_title = _child_text(root, "title")
            feed_link = next(
                (child.attrib.get("href") for child in root if _local_tag(child.tag) == "link"),
                None,
            )
            item_rows = [
                {"title": _child_text(item, "title"),
                 "link": next((child.attrib.get("href") for child in item
                               if _local_tag(child.tag) == "link"), None),
                 "id": _child_text(item, "id"),
                 "published": _child_text(item, "published") or _child_text(item, "updated")}
                for item in items[:100]
            ]
        else:
            raise ValueError("unsupported feed root")
        return {"feed_title": feed_title, "feed_link": feed_link,
                "item_count": len(items), "items_sample": item_rows}


class ApiCollector(BaseCollector):
    @property
    def accept(self) -> str:
        return "application/json"

    def parse_metadata(self, response: HttpResponse) -> dict[str, Any]:
        content_type = response.headers.get("content-type")
        media_type = _media_type(content_type)
        if media_type != "application/json" and not media_type.endswith("+json"):
            raise ValueError("response is not JSON")
        try:
            data = json.loads(_decode(response.content, content_type))
        except json.JSONDecodeError as error:
            raise ValueError("malformed JSON") from error
        if isinstance(data, list):
            return {"json_type": "array", "item_count": len(data)}
        if isinstance(data, dict):
            return {"json_type": "object", "top_level_keys": list(data)[:50]}
        return {"json_type": type(data).__name__}
