from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from collect.adapters.registry import get_html_adapter
from collect.models import OpportunityCandidate, Source


class LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag != "a":
            return

        attributes = dict(attrs)
        href = attributes.get("href")
        if href:
            self._current_href = href
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._current_href:
            return

        text = " ".join(" ".join(self._current_text).split())
        if text:
            self.links.append(
                {
                    "href": self._current_href,
                    "text": text,
                }
            )

        self._current_href = None
        self._current_text = []


def collect_html_source(
    source: Source,
    *,
    max_candidates: int,
    timeout: int = 15,
) -> list[OpportunityCandidate]:
    if not source.collection_url:
        return []

    request = Request(
        source.collection_url,
        headers={
            "User-Agent": (
                "AIPathKoreaCollector/0.1 "
                "(contact: michaelis@naver.com)"
            ),
        },
    )

    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        html = response.read().decode(charset, errors="replace")

    parser = LinkExtractor()
    parser.feed(html)

    adapter = get_html_adapter(source)
    seen_urls: set[str] = set()
    candidates: list[OpportunityCandidate] = []

    for link in parser.links:
        title = link["text"].strip()
        official_url = urljoin(source.collection_url, link["href"])

        if not _is_http_url(official_url):
            continue

        if not adapter.accepts(title=title, url=official_url):
            continue

        if official_url in seen_urls:
            continue

        cleaned_title = adapter.clean_title(title)
        seen_urls.add(official_url)
        candidates.append(
            OpportunityCandidate(
                source_id=source.id,
                external_id=official_url,
                title=cleaned_title[:300],
                official_url=official_url,
                candidate_status="pending",
                raw_payload={
                    "collector": "html",
                    "source_name": source.name,
                    "collection_url": source.collection_url,
                    "anchor_text": title,
                    "href": link["href"],
                },
            )
        )

        if len(candidates) >= max_candidates:
            break

    return candidates


def _is_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
