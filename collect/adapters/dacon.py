from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.request import Request, urlopen

from collect.adapters.base import HtmlSourceAdapter
from collect.models import OpportunityCandidate


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text:
            self.parts.append(text)

    def lines(self) -> list[str]:
        lines: list[str] = []
        for part in self.parts:
            for raw_line in part.splitlines():
                line = raw_line.strip()
                if line:
                    lines.append(line)

        return lines


class DaconAdapter(HtmlSourceAdapter):
    source_name = "DACON"
    detail_url_parts = (
        "/competitions/official/",
        "/competitions/open/",
    )
    list_url_parts = (
        "/competitions",
    )

    def accepts(self, *, title: str, url: str) -> bool:
        return "참가신청중" in title and super().accepts(title=title, url=url)

    def clean_title(self, title: str) -> str:
        cleaned = super().clean_title(title)
        cleaned = re.split(r"\s+\|\s+", cleaned, maxsplit=1)[0]
        cleaned = re.sub(r"\s+참가신청중\s+[0-9,]+명.*$", "", cleaned)
        return cleaned.strip()

    def enrich_candidate(
        self,
        candidate: OpportunityCandidate,
        *,
        timeout: int = 15,
    ) -> OpportunityCandidate:
        if not candidate.official_url:
            return candidate

        try:
            lines = _fetch_detail_lines(candidate.official_url, timeout=timeout)
        except Exception as error:
            candidate.raw_payload["detail_error"] = str(error)
            return candidate

        candidate.summary = candidate.summary or _extract_summary(lines)
        candidate.target_audience = (
            candidate.target_audience
            or _extract_after_heading(lines, "[참가 자격]")
            or _extract_conference_audience(lines)
        )
        candidate.organizer = (
            candidate.organizer
            or _extract_organizer(lines)
            or _extract_subtitle_organizer(lines, candidate.title)
        )

        schedule = _extract_schedule(lines)
        candidate.application_start_at = (
            candidate.application_start_at or schedule.get("application_start_at")
        )
        candidate.application_deadline_at = (
            candidate.application_deadline_at
            or schedule.get("application_deadline_at")
        )
        candidate.event_start_at = candidate.event_start_at or schedule.get("event_start_at")
        candidate.event_start_at = (
            candidate.event_start_at or _extract_conference_event_start(lines)
        )
        candidate.event_end_at = candidate.event_end_at or schedule.get("event_end_at")

        candidate.raw_payload["detail_collector"] = "dacon_detail"
        return candidate


def _fetch_detail_lines(url: str, *, timeout: int) -> list[str]:
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
    return parser.lines()


def _extract_summary(lines: list[str]) -> str | None:
    topic = _extract_after_heading(lines, "[주제]")
    background = _extract_section(lines, "[배경]", stop_headings=("[대회 방식]",))
    conference_intro = _extract_intro_before_heading(lines, "[컨퍼런스 개요]")

    summary_parts = [value for value in (topic, background, conference_intro) if value]
    if not summary_parts:
        return None

    return "\n\n".join(summary_parts)[:1000]


def _extract_section(
    lines: list[str],
    heading: str,
    *,
    stop_headings: tuple[str, ...],
) -> str | None:
    start_index = _find_heading_index(lines, heading)
    if start_index is None:
        return None

    values: list[str] = []
    for line in lines[start_index + 1 :]:
        if any(stop in line for stop in stop_headings):
            break

        if _is_noise_line(line):
            continue

        values.append(line)

    return "\n".join(values).strip() or None


def _extract_after_heading(lines: list[str], heading: str) -> str | None:
    start_index = _find_heading_index(lines, heading)
    if start_index is None:
        return None

    for line in lines[start_index + 1 :]:
        if _is_noise_line(line):
            continue

        if line.startswith("[") and line.endswith("]"):
            return None

        return line

    return None


def _extract_organizer(lines: list[str]) -> str | None:
    start_index = _find_heading_index(lines, "[주최 / 운영]")
    if start_index is None:
        return None

    values: list[str] = []
    for line in lines[start_index + 1 : start_index + 8]:
        if "대회 주요 일정" in line:
            break

        if line.startswith(("주최:", "주관:", "운영:")):
            values.append(line)

    return " / ".join(values) or None


def _extract_subtitle_organizer(
    lines: list[str],
    title: str | None,
) -> str | None:
    if not title:
        return None

    title_index = _find_heading_index(lines, title)
    if title_index is None:
        return None

    for line in lines[title_index + 1 : title_index + 6]:
        if "|" not in line:
            continue

        organizer = line.split("|", maxsplit=1)[0].strip()
        return organizer or None

    return None


def _extract_conference_audience(lines: list[str]) -> str | None:
    for line in lines:
        if "참석대상" not in line:
            continue

        return _value_after_dash_or_colon(line)

    return None


def _extract_intro_before_heading(lines: list[str], heading: str) -> str | None:
    heading_index = _find_heading_index(lines, heading)
    if heading_index is None:
        return None

    values: list[str] = []
    intro_lines = lines[max(0, heading_index - 6) : heading_index]
    for index, line in enumerate(intro_lines):
        if _is_noise_line(line):
            continue

        if line.startswith(("#", "[", "##")):
            continue

        if "|" in line:
            continue

        next_line = intro_lines[index + 1] if index + 1 < len(intro_lines) else ""
        if "|" in next_line:
            continue

        values.append(line)

    return "\n".join(values[-3:]).strip() or None


def _extract_conference_event_start(lines: list[str]) -> str | None:
    year = datetime.now(timezone.utc).year

    for line in lines:
        if "일시" not in line:
            continue

        match = re.search(r"(\d{1,2})월\s*(\d{1,2})일", line)
        if not match:
            continue

        month, day = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"

    return None


def _extract_schedule(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    year = datetime.now(timezone.utc).year

    for index, line in enumerate(lines):
        if not re.fullmatch(r"\d{2}\.\d{2}", line):
            continue

        label = _next_non_noise_line(lines, index + 1)
        date_value = _date_from_month_day(line, year=year)
        if not label or not date_value:
            continue

        if "참가 신청 시작" in label:
            values["application_start_at"] = date_value
        elif "대회 시작" in label:
            values["event_start_at"] = date_value
        elif "참가 신청 마감" in label or "리더보드 제출 마감" in label:
            values.setdefault("application_deadline_at", date_value)
        elif "대회 종료" in label:
            values["event_end_at"] = date_value
            values.setdefault("application_deadline_at", date_value)

    return values


def _date_from_month_day(value: str, *, year: int) -> str | None:
    match = re.fullmatch(r"(\d{2})\.(\d{2})", value)
    if not match:
        return None

    month, day = match.groups()
    return f"{year}-{month}-{day}"


def _find_heading_index(lines: list[str], heading: str) -> int | None:
    for index, line in enumerate(lines):
        if heading in line:
            return index

    return None


def _value_after_dash_or_colon(line: str) -> str | None:
    parts = re.split(r"\s[-:]\s|:", line, maxsplit=1)
    if len(parts) < 2:
        return None

    return parts[1].strip() or None


def _next_non_noise_line(lines: list[str], start_index: int) -> str | None:
    for line in lines[start_index:]:
        if not _is_noise_line(line):
            return line

    return None


def _is_noise_line(line: str) -> bool:
    if not line:
        return True

    return line in {
        "* * *",
        "개요",
        "평가",
        "규칙",
        "일정",
        "상금",
        "동의사항",
    } or bool(re.fullmatch(r"\d+\.", line))
