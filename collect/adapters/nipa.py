from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.request import Request, urlopen

from collect.adapters.base import HtmlSourceAdapter
from collect.models import OpportunityCandidate


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self.skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return

        text = " ".join(data.split())
        if text:
            self.parts.append(text)

    def lines(self) -> list[str]:
        return [part.strip() for part in self.parts if part.strip()]


class NipaAdapter(HtmlSourceAdapter):
    source_name = "정보통신산업진흥원(NIPA)"
    detail_url_parts = (
        "/home/2-2/",
    )
    list_url_parts = (
        "/home/2-2",
    )

    def enrich_candidate(
        self,
        candidate: OpportunityCandidate,
        *,
        timeout: int = 15,
    ) -> OpportunityCandidate:
        if not candidate.official_url:
            return candidate

        try:
            text = _fetch_detail_text(candidate.official_url, timeout=timeout)
        except Exception as error:
            candidate.raw_payload["detail_error"] = str(error)
            return candidate

        candidate.application_start_at, candidate.application_deadline_at = (
            _extract_application_period(text)
        )
        candidate.organizer = candidate.organizer or _extract_organizer(text)
        detail_summary = _extract_summary(text)
        if _is_missing_summary(candidate.summary):
            candidate.summary = detail_summary
        candidate.raw_payload["detail_collector"] = "nipa_detail"
        return candidate


def _fetch_detail_text(url: str, *, timeout: int) -> str:
    request = Request(
        url,
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

    parser = TextExtractor()
    parser.feed(html)
    return " ".join(parser.lines())


def _extract_application_period(text: str) -> tuple[str | None, str | None]:
    match = re.search(
        r"신청기간\s*:\s*(\d{4}-\d{2}-\d{2})\s*\d{2}:\d{2}\s*~\s*"
        r"(\d{4}-\d{2}-\d{2})\s*\d{2}:\d{2}",
        text,
    )
    return match.groups() if match else (None, None)


def _extract_organizer(text: str) -> str | None:
    paired = re.search(
        r"(과학기술정보통신부)\s*와\s*(정보통신산업진흥원|NIPA)",
        text,
    )
    if paired:
        return " / ".join(dict.fromkeys(paired.groups()))

    match = re.search(
        r"(과학기술정보통신부|정보통신산업진흥원|NIPA)[^。]{0,80}"
        r"(?:장관|원장|주관|주최)",
        text,
    )
    if match:
        value = match.group(0)
        names = re.findall(r"과학기술정보통신부|정보통신산업진흥원|NIPA", value)
        return " / ".join(dict.fromkeys(names))

    return None


def _extract_summary(text: str) -> str | None:
    match = re.search(
        r"내용\s*(?:\|\s*|:\s*|)(.+?)(?:첨부파일|목록|인쇄하기)",
        text,
    )
    if not match:
        return None

    summary = re.sub(r"\s+", " ", match.group(1)).strip(" |:")
    summary = re.sub(r"\s+(?=[-•]\s*)", "\n", summary)
    summary = re.sub(r"\s+(?=\d{4}\.\s*\d{1,2}\.\s*\d{1,2})", "\n", summary)
    return summary[:900] or None


def _is_missing_summary(value: str | None) -> bool:
    if not value:
        return True

    normalized = re.sub(r"\s+", "", value).lower()
    return normalized in {"요약정보가없습니다.", "요약정보가없습니다"}
