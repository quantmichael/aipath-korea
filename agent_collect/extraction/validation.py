"""Resolve cited IDs, then validate values against source-owned evidence."""

import re
import unicodedata
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from agent_collect.collectors.security import UnsafeTargetError, validate_target_url
from agent_collect.extraction.preprocess import PreparedContent
from agent_collect.extraction.schema import (
    DATE_FIELDS, FIELD_NAMES, URL_FIELDS, ConferenceCandidate, FieldEvidence,
)


_MONTH = (r"(?:January|February|March|April|May|June|July|August|September|October|"
          r"November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)")
_MONTH_DATE = re.compile(
    rf"\b(?P<month>{_MONTH})\.?\s+(?P<start>\d{{1,2}})"
    r"(?:\s*[-–—]\s*(?P<end>\d{1,2}))?,?\s+(?P<year>\d{4})\b", re.I,
)
_DAY_FIRST_RANGE = re.compile(
    rf"(?<!\d)(?P<start>\d{{1,2}})\s*[-–—]\s*(?P<end>\d{{1,2}})\s+"
    rf"(?P<month>{_MONTH})\.?,?\s+(?P<year>\d{{4}})(?!\d)", re.I,
)
_CROSS_MONTH_RANGE = re.compile(
    rf"\b(?P<start_month>{_MONTH})\.?\s+(?P<start>\d{{1,2}})\s*[-–—]\s*"
    rf"(?P<end_month>{_MONTH})\.?\s+(?P<end>\d{{1,2}}),?\s+"
    r"(?P<year>\d{4})(?!\d)", re.I,
)
_EXPLICIT_DATETIME = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})"
)
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_DOTTED_DATE = re.compile(
    r"(?<![\d.])\d{4}\.\s*\d{1,2}\.\s*\d{1,2}(?:\.(?!\d))?(?!\d|\.\d)"
)
_KOREAN_WEEKDAYS = "월화수목금토일"
_KOREAN_DATE = re.compile(r"\b\d{4}년\s*\d{1,2}월\s*\d{1,2}일\b")
_KOREAN_ZONED_CLOCK = re.compile(
    r"(?<!\d)(?P<year>\d{4})(?:년\s*(?P<km>\d{1,2})월\s*(?P<kd>\d{1,2})일|"
    r"[.\-/]\s*(?P<nm>\d{1,2})\s*[.\-/]\s*(?P<nd>\d{1,2})\.?)"
    r"\s*(?:\((?P<weekday>[월화수목금토일])\))?\s*"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?"
    r"\s*(?P<zone>KST|UTC\+09:00|\+09:00)(?![A-Za-z0-9:+-])",
    re.I,
)
_OTHER_CLOCK = re.compile(r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)|(?<!\d)\d{1,2}\s*시")


def _normal(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(re.sub(r"[^\w]+", " ", normalized).split())


def parse_unambiguous_date(value: str) -> str | None:
    text = value.strip()
    dotted = _DOTTED_DATE.fullmatch(text)
    if dotted:
        try:
            year, month, day = (int(part) for part in re.findall(r"\d+", dotted.group()))
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        pass
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%b. %d, %Y", "%d %B %Y", "%Y년 %m월 %d일"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_explicit_korean_datetime(text: str) -> datetime | None:
    """Replay one full Korean local clock only when its +09:00 zone is explicit."""
    matches = list(_KOREAN_ZONED_CLOCK.finditer(text))
    if len(matches) != 1:
        return None
    match = matches[0]
    remainder = text[:match.start()] + text[match.end():]
    if _OTHER_CLOCK.search(remainder) or _dates_in_text(remainder):
        return None
    try:
        local = datetime(
            int(match.group("year")), int(match.group("km") or match.group("nm")),
            int(match.group("kd") or match.group("nd")), int(match.group("hour")),
            int(match.group("minute")), int(match.group("second") or 0),
            tzinfo=timezone(timedelta(hours=9)),
        )
    except ValueError:
        return None
    weekday = match.group("weekday")
    if weekday and weekday != _KOREAN_WEEKDAYS[local.weekday()]:
        return None
    return local


def _dates_in_text(text: str) -> set[str]:
    found: set[str] = set()
    for match in _ISO_DATE.finditer(text):
        parsed = parse_unambiguous_date(match.group())
        if parsed:
            found.add(parsed)
    for match in _DOTTED_DATE.finditer(text):
        parsed = parse_unambiguous_date(match.group())
        if parsed:
            weekday = re.match(r"\s*\(([월화수목금토일])\)", text[match.end():])
            if weekday and weekday.group(1) != _KOREAN_WEEKDAYS[date.fromisoformat(parsed).weekday()]:
                continue
            found.add(parsed)
    for match in _KOREAN_DATE.finditer(text):
        parsed = parse_unambiguous_date(match.group())
        if parsed:
            found.add(parsed)
    for match in _MONTH_DATE.finditer(text):
        if match.group("end"):
            endpoints = _range_endpoints(match)
            if endpoints:
                found.update(endpoints)
        else:
            parsed = parse_unambiguous_date(
                f"{match.group('month')} {match.group('start')}, {match.group('year')}")
            if parsed:
                found.add(parsed)
    for pattern in (_DAY_FIRST_RANGE, _CROSS_MONTH_RANGE):
        for match in pattern.finditer(text):
            endpoints = _range_endpoints(match)
            if endpoints:
                found.update(endpoints)
    return found


def _range_endpoints(match: re.Match[str]) -> tuple[str, str] | None:
    year = match.group("year")
    start_month = match.groupdict().get("start_month") or match.group("month")
    end_month = match.groupdict().get("end_month") or start_month
    start = parse_unambiguous_date(f"{start_month} {match.group('start')}, {year}")
    end = parse_unambiguous_date(f"{end_month} {match.group('end')}, {year}")
    if not start or not end or end < start:
        return None
    return start, end


def parse_evidenced_date(value: str, *, field_name: str) -> str | None:
    """Accept a single full date or an explicit, ordered range endpoint."""
    single = parse_unambiguous_date(value)
    if single:
        return single
    for pattern in (_MONTH_DATE, _DAY_FIRST_RANGE, _CROSS_MONTH_RANGE):
        match = pattern.fullmatch(value.strip())
        if match and match.groupdict().get("end"):
            endpoints = _range_endpoints(match)
            if endpoints:
                return endpoints[0 if field_name.endswith("start") else 1]
    return None


def parse_explicit_datetime(value: str) -> datetime | None:
    """Only an ISO clock time with an explicit numeric offset or Z is confirmed."""
    if not _EXPLICIT_DATETIME.fullmatch(value.strip()):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def validated_calendar_date(value: str) -> date | None:
    """Calendar component of an already validated ISO date or offset datetime."""
    try:
        return date.fromisoformat(value)
    except ValueError:
        timestamp = parse_explicit_datetime(value)
        return timestamp.date() if timestamp else None


def validate_candidate(candidate: ConferenceCandidate, *, prepared: PreparedContent,
                       source_url: str, max_evidence_chars: int) -> ConferenceCandidate:
    if max_evidence_chars <= 0:
        raise ValueError("max_evidence_chars must be positive")
    block_map = {block.block_id: block for block in prepared.evidence_blocks}
    link_map = {link.link_id: link for link in prepared.link_evidence}
    reasons: list[str] = []
    fields: dict[str, FieldEvidence] = {}
    for name in FIELD_NAMES:
        field = candidate.fields[name]
        raw_value = field.value.strip() if field.value else None
        if not candidate.is_conference:
            fields[name] = replace(field, value=None, evidence_text=None, confidence=None,
                                   validation_status="NOT_CONFERENCE")
            continue
        if name in URL_FIELDS:
            link_ids = field.link_ids
            if not link_ids:
                # A model may put one real L-id in evidence_block_ids. Resolve
                # only that cited link, while requiring every other cited ID
                # to be a real block. The link itself remains mandatory.
                misplaced = tuple(ref for ref in field.evidence_block_ids if ref in link_map)
                other_ids = tuple(ref for ref in field.evidence_block_ids if ref not in link_map)
                if len(misplaced) == 1 and all(ref in block_map for ref in other_ids):
                    link_ids = misplaced
            if not link_ids and raw_value is None and not field.evidence_block_ids:
                fields[name] = replace(field, value=None, confidence=None, validation_status="MISSING")
                continue
            if len(link_ids) != 1 or link_ids[0] not in link_map:
                failure = "invalid_link_id"
            else:
                link = link_map[link_ids[0]]
                try:
                    validate_target_url(link.href)
                    failure = "url_mismatch" if raw_value and raw_value != link.href else None
                except UnsafeTargetError:
                    failure = "invalid_url"
            if failure:
                reasons.append(f"{name}:{failure}")
                fields[name] = replace(field, value=None, evidence_text=None, confidence=0,
                                       validation_status=failure.upper())
                continue
            evidence = f"{link.text} -> {link.href}"[:max_evidence_chars]
            # URL provenance is the resolved link, not any unrelated block IDs
            # the model may also have supplied.
            fields[name] = replace(field, value=link.href, evidence_text=evidence,
                                   evidence_block_ids=(), link_ids=link_ids,
                                   confidence=field.confidence or 0,
                                   validation_status="VALID")
            continue

        if raw_value is None:
            fields[name] = replace(field, value=None, confidence=None, validation_status="MISSING")
            continue
        if not field.evidence_block_ids:
            failure = "missing_evidence"
        elif len(field.evidence_block_ids) > 5:
            failure = "evidence_limit_exceeded"
        elif any(block_id not in block_map for block_id in field.evidence_block_ids):
            failure = "invalid_block_id"
        else:
            failure = None
        if failure:
            reasons.append(f"{name}:{failure}")
            fields[name] = replace(field, value=None, evidence_text=None, confidence=0,
                                   validation_status=failure.upper())
            continue
        resolved = " ".join(block_map[block_id].text for block_id in field.evidence_block_ids)
        value = raw_value
        if name in DATE_FIELDS:
            timestamp = parse_explicit_datetime(raw_value)
            parsed = parse_evidenced_date(raw_value, field_name=name)
            if timestamp is not None:
                # A confirmed clock time and offset must appear verbatim in the
                # selected evidence; a matching calendar date is insufficient.
                failure = None if raw_value in resolved else "date_mismatch"
                value = timestamp.isoformat()
            elif parsed is None or parsed not in _dates_in_text(resolved):
                failure = "date_mismatch"
            else:
                failure = None
                value = parsed
        elif name == "format":
            failure = None if raw_value.lower() in {"online", "offline", "hybrid"} and _normal(raw_value) in _normal(resolved) else "value_mismatch"
        else:
            failure = None if _normal(raw_value) and _normal(raw_value) in _normal(resolved) else "value_mismatch"
        if failure:
            reasons.append(f"{name}:{failure}")
            fields[name] = replace(field, value=None, evidence_text=resolved[:max_evidence_chars],
                                   confidence=0, validation_status=failure.upper())
        else:
            fields[name] = replace(field, value=value, evidence_text=resolved[:max_evidence_chars],
                                   confidence=field.confidence or 0, validation_status="VALID")
    for required in ("title", "organizer", "official_url"):
        if candidate.is_conference and not fields[required].value:
            reasons.append(f"{required}:missing")
    confidence = min(candidate.document_confidence, 0.49) if reasons else candidate.document_confidence
    return ConferenceCandidate(fields, confidence, candidate.is_conference, tuple(dict.fromkeys(reasons)))
