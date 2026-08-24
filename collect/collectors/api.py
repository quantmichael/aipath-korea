from __future__ import annotations

import json
import os
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from collect.models import OpportunityCandidate, Source


BIZINFO_API_URL = "https://www.bizinfo.go.kr/uss/rss/bizinfoApi.do"
BIZINFO_TOPIC_KEYWORDS = (
    "ai",
    "인공지능",
    "생성형",
    "llm",
    "데이터",
    "빅데이터",
    "디지털",
    "dx",
    "ax",
    "sw",
    "소프트웨어",
    "블록체인",
    "보안",
    "스마트",
    "ict",
    "정보통신",
)


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self.parts).split())


def collect_api_source(
    source: Source,
    *,
    max_candidates: int,
    timeout: int = 15,
) -> list[OpportunityCandidate]:
    if source.name == "기업마당":
        return collect_bizinfo_api_source(
            source,
            max_candidates=max_candidates,
            timeout=timeout,
        )

    return []


def collect_bizinfo_api_source(
    source: Source,
    *,
    max_candidates: int,
    timeout: int = 15,
) -> list[OpportunityCandidate]:
    api_key = os.environ.get("BIZINFO_API_KEY")
    if not api_key:
        raise RuntimeError("BIZINFO_API_KEY 환경변수가 설정되지 않았습니다.")

    url = _build_bizinfo_url(
        source.collection_url or BIZINFO_API_URL,
        api_key=api_key,
        max_candidates=max_candidates,
    )
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
        raw_text = response.read().decode(charset, errors="replace")

    payload = json.loads(raw_text)
    rows = _extract_bizinfo_rows(payload)

    candidates: list[OpportunityCandidate] = []
    seen_external_ids: set[str] = set()

    for row in rows:
        if not _is_relevant_bizinfo_row(row):
            continue

        candidate = _candidate_from_bizinfo_row(source, row)
        if not candidate:
            continue

        external_id = candidate.external_id or candidate.official_url
        if external_id in seen_external_ids:
            continue

        if external_id:
            seen_external_ids.add(external_id)

        candidates.append(candidate)
        if len(candidates) >= max_candidates:
            break

    return candidates


def _build_bizinfo_url(
    base_url: str,
    *,
    api_key: str,
    max_candidates: int,
) -> str:
    params = {
        "crtfcKey": api_key,
        "dataType": "json",
        "searchCnt": str(max(max_candidates, 10)),
    }
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}{urlencode(params)}"


def _extract_bizinfo_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    for key in ("jsonArray", "items", "item", "data", "list", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            rows = _extract_bizinfo_rows(value)
            if rows:
                return rows

    for value in payload.values():
        rows = _extract_bizinfo_rows(value)
        if rows:
            return rows

    return []


def _candidate_from_bizinfo_row(
    source: Source,
    row: dict[str, Any],
) -> OpportunityCandidate | None:
    title = _first_text(
        row,
        (
            "pblancNm",
            "pblancTitle",
            "title",
            "sj",
            "bsnsNm",
            "사업명",
            "공고명",
        ),
    )
    if not title:
        return None

    official_url = _first_text(
        row,
        (
            "pblancUrl",
            "url",
            "link",
            "detailUrl",
            "참고URL",
        ),
    )
    pblanc_id = _first_text(row, ("pblancId", "PBLANC_ID", "id"))

    if not official_url and pblanc_id:
        official_url = (
            "https://www.bizinfo.go.kr/sii/siia/selectSIIA200Detail.do"
            f"?pblancId={pblanc_id}"
        )

    external_id = pblanc_id or official_url
    if not external_id:
        return None

    application_start_at, application_deadline_at = _date_range_from_text(
        _first_text(row, ("reqstBeginEndDe", "reqstDt", "신청기간"))
    )

    return OpportunityCandidate(
        source_id=source.id,
        external_id=external_id,
        title=title[:300],
        summary=_clean_html_text(
            _first_text(row, ("bsnsSumryCn", "description", "summary", "cn", "내용"))
        ),
        organizer=_first_text(row, ("jrsdInsttNm", "organization", "기관명")),
        target_audience=_first_text(row, ("trgetNm", "target", "지원대상")),
        application_start_at=application_start_at
        or _first_iso_date(row, ("reqstBeginDe", "reqstBeginDt", "신청기간시작")),
        application_deadline_at=application_deadline_at
        or _first_iso_date(row, ("reqstEndDe", "reqstEndDt", "신청기간종료")),
        official_url=official_url,
        candidate_status="pending",
        raw_payload={
            "collector": "api",
            "source_name": source.name,
            "collection_url": source.collection_url,
            "api": "bizinfo",
            "row": row,
        },
    )


def _first_text(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue

        text = str(value).strip()
        if text:
            return text

    return None


def _is_relevant_bizinfo_row(row: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(value)
        for key, value in row.items()
        if key
        in {
            "pblancNm",
            "title",
            "hashtags",
            "bsnsSumryCn",
            "description",
            "pldirSportRealmLclasCodeNm",
            "pldirSportRealmMlsfcCodeNm",
            "jrsdInsttNm",
            "excInsttNm",
        }
        and value is not None
    ).lower()

    return any(_matches_topic_keyword(haystack, keyword) for keyword in BIZINFO_TOPIC_KEYWORDS)


def _matches_topic_keyword(haystack: str, keyword: str) -> bool:
    normalized_keyword = keyword.lower()

    if normalized_keyword in {"ai", "sw", "dx", "ax"}:
        return bool(
            re.search(
                rf"(^|[^a-z0-9]){re.escape(normalized_keyword)}([^a-z0-9]|$)",
                haystack,
            )
        )

    return normalized_keyword in haystack


def _clean_html_text(value: str | None) -> str | None:
    if not value:
        return None

    parser = TextExtractor()
    parser.feed(unescape(value))
    text = parser.text()
    return text or None


def _first_iso_date(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue

        text = str(value).strip()
        match = re.search(r"(\d{4})[.-](\d{1,2})[.-](\d{1,2})", text)
        if match:
            year, month, day = match.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"

    return None


def _date_range_from_text(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return (None, None)

    matches = re.findall(r"(\d{4})[.-]?(\d{1,2})[.-]?(\d{1,2})", value)
    dates = [f"{year}-{int(month):02d}-{int(day):02d}" for year, month, day in matches]

    if not dates:
        return (None, None)

    if len(dates) == 1:
        return (None, dates[0])

    return (dates[0], dates[1])
