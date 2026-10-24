"""Conservative, transient extraction from explicitly labelled notice fields.

This parser never fetches, stores or publishes a page. Ambiguous or missing
fields remain absent; the evidence resolver then holds the lead for review.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from agent_collect.collectors.security import validate_target_url
from agent_collect.discovery.evidence import _CLOCK_IN_QUOTE, _replay_claim
from agent_collect.extraction.preprocess import preprocess_html
from agent_collect.extraction.validation import (
    _dates_in_text, parse_explicit_datetime, parse_explicit_korean_datetime,
)
from agent_collect.metadata.evidence import (
    EvidenceType, MetadataAccessPolicy, MetadataFact, _same_url,
)
from agent_collect.schemas.models import Source


_MAX_AGE = timedelta(minutes=15)
_MAX_PAGE_BYTES = 1_048_576
_EXACT = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})"
)
_LABELS = {
    "title": re.compile(r"^(?:title|제목|공고명)\s*[:：]\s*(.+)$", re.I),
    "summary": re.compile(r"^(?:summary|요약|소개)\s*[:：]\s*(.+)$", re.I),
    "organizer": re.compile(r"^(?:organizer|host|주최|주관)\s*[:：]\s*(.+)$", re.I),
    "application_start": re.compile(
        r"^(?:application (?:opens|starts)|registration (?:opens|starts)|신청 시작|접수 시작)\s*[:：]?\s*(.+)$", re.I),
    "application_deadline": re.compile(
        r"^(?:application deadline|registration deadline|신청 마감|접수 마감)\s*[:：]?\s*(.+)$", re.I),
    "event_start": re.compile(r"^(?:event starts?|행사 시작|개최 시작)\s*[:：]?\s*(.+)$", re.I),
    "event_end": re.compile(r"^(?:event ends?|행사 종료|개최 종료)\s*[:：]?\s*(.+)$", re.I),
    "application_method": re.compile(
        r"^(?:application method|how to apply|신청\s*방법|접수\s*방법)\s*[:：]\s*(.+)$", re.I),
    "category": re.compile(r"^(?:category|분류|유형)\s*[:：]\s*(.+)$", re.I),
}
_DATES = frozenset({"application_start", "application_deadline", "event_start", "event_end"})
_APPLICATION_PERIOD = re.compile(r"^(?:신청|접수|모집)\s*기간\s*[:：]\s*(.+)$")


def _label_match(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    match = pattern.fullmatch(text)
    if match is not None:
        return match
    # preprocess_html keeps a two-cell <th>/<td> row in one evidence block.
    # Only an unambiguous label/value pair may be treated like a labelled line.
    if not text.startswith("|"):
        return None
    cells = [cell.strip() for cell in text.split("|")[1:]]
    if cells and cells[-1] == "":
        cells.pop()
    if len(cells) == 2 and all(cells):
        return pattern.fullmatch(f"{cells[0]}: {cells[1]}")
    return None


@dataclass(frozen=True, slots=True)
class PublicNoticeFact:
    """A replayed page fact; this alone does not establish officiality or usage rights."""

    value: str
    evidence_reference: str
    quote: str


def extract_labelled_public_facts(*, page: bytes, page_url: str,
                                  fetched_at: datetime,
                                  now: datetime) -> dict[str, PublicNoticeFact]:
    """Read fresh labelled facts without claiming that the Source is approved.

    The caller must establish safe fetching, official individual page identity,
    permitted fact usage, and registration eligibility separately.
    """
    if (now.tzinfo is None or fetched_at.tzinfo is None or
            not now - _MAX_AGE <= fetched_at <= now or
            not page or len(page) > _MAX_PAGE_BYTES):
        raise ValueError("fresh bounded page required")
    validate_target_url(page_url)
    if urlsplit(page_url).scheme != "https":
        raise ValueError("public notice requires HTTPS")
    prepared = preprocess_html(page, source_url=page_url, max_input_chars=12000)
    facts: dict[str, PublicNoticeFact] = {}
    for name, pattern in _LABELS.items():
        matches = [(block, _label_match(pattern, block.text))
                   for block in prepared.evidence_blocks if len(block.text) <= 500]
        matches = [(block, match) for block, match in matches if match is not None]
        if len(matches) != 1:
            continue
        block, match = matches[0]
        value = (_date_value(match.group(1)) if name in _DATES
                 else match.group(1).strip())
        if not value or len(value) > 200:
            continue
        from agent_collect.discovery.evidence import EvidenceClaim
        replay_name = {"application_start": "registration_start",
                       "application_deadline": "registration_deadline"}.get(name, name)
        replayed, _, error = _replay_claim(
            replay_name, EvidenceClaim(value, block.text, (block.block_id,), ()),
            prepared, page_url)
        if error is None and replayed is not None:
            facts[name] = PublicNoticeFact(value, block.block_id, block.text)
    periods = [(block, _label_match(_APPLICATION_PERIOD, block.text))
               for block in prepared.evidence_blocks if len(block.text) <= 500]
    periods = [(block, match) for block, match in periods if match is not None]
    if len(periods) == 1:
        block, match = periods[0]
        pieces = [piece.strip() for piece in match.group(1).split("~")]
        if len(pieces) == 2 and all(pieces):
            period_facts: dict[str, PublicNoticeFact] = {}
            for name, piece in zip(("application_start", "application_deadline"), pieces):
                value = _date_value(piece)
                if value is None:
                    break
                from agent_collect.discovery.evidence import EvidenceClaim
                replayed, _, error = _replay_claim(
                    {"application_start": "registration_start",
                     "application_deadline": "registration_deadline"}[name],
                    EvidenceClaim(value, piece, (block.block_id,), ()),
                    prepared, page_url)
                if error is not None or replayed is None:
                    break
                period_facts[name] = PublicNoticeFact(value, block.block_id, piece)
            if len(period_facts) == 2:
                if any(name in facts and facts[name].value != fact.value
                       for name, fact in period_facts.items()):
                    facts.pop("application_start", None)
                    facts.pop("application_deadline", None)
                else:
                    facts.update(period_facts)
    elif len(periods) > 1:
        facts.pop("application_start", None)
        facts.pop("application_deadline", None)
    page_links = [link for link in prepared.link_evidence
                  if link.link_id == "L000" and _same_url(link.href, page_url)]
    if len(page_links) == 1:
        facts["official_url"] = PublicNoticeFact(page_url, "L000", "")
    return facts


def _date_value(text: str) -> str | None:
    exact = list(_EXACT.finditer(text))
    if exact:
        if len(exact) != 1 or parse_explicit_datetime(exact[0].group()) is None:
            return None
        remainder = text[:exact[0].start()] + text[exact[0].end():]
        return (exact[0].group() if not _dates_in_text(remainder) and
                not _CLOCK_IN_QUOTE.search(remainder) else None)
    korean = parse_explicit_korean_datetime(text)
    if korean is not None:
        return korean.isoformat()
    if _CLOCK_IN_QUOTE.search(text):
        return None
    dates = _dates_in_text(text)
    return next(iter(dates)) if len(dates) == 1 else None


def extract_labelled_metadata(*, source: Source, policy: MetadataAccessPolicy,
                              page: bytes, page_url: str, fetched_at: datetime,
                              permission_document: bytes,
                              permission_checked_at: datetime,
                              now: datetime) -> dict[str, MetadataFact]:
    """Extract only unique, exact labelled fields after access checks.

    A confidence of 0.95 means the deterministic label and evidence replay
    agreed. It does not assert organizer permission or Source officiality;
    those are separately supplied by an authenticated policy.
    """
    if (now.tzinfo is None or fetched_at.tzinfo is None or
            permission_checked_at.tzinfo is None or
            not now - _MAX_AGE <= fetched_at <= now or
            not now - _MAX_AGE <= permission_checked_at <= now or
            not page or len(page) > _MAX_PAGE_BYTES or
            not _same_url(page_url, policy.official_url) or
            not policy.permits(source, page_url, now=now)[0] or
            policy.approved_principal_id is None or
            policy.approval_event_id is None or
            hashlib.sha256(permission_document).hexdigest() != policy.permission_digest):
        raise ValueError("current authenticated metadata permission and page recheck required")
    public_facts = extract_labelled_public_facts(
        page=page, page_url=page_url, fetched_at=fetched_at, now=now)
    return {name: MetadataFact(
        fact.value, page_url,
        EvidenceType.PAGE_LINK if name == "official_url" else EvidenceType.PAGE_BLOCK,
        fact.evidence_reference, fact.quote, fetched_at, 0.95,
        policy.permission_digest)
        for name, fact in public_facts.items()}
