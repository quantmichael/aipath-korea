"""Conservative, transient extraction from explicitly labelled notice fields.

This parser never fetches, stores or publishes a page. Ambiguous or missing
fields remain absent; the evidence resolver then holds the lead for review.
"""

import hashlib
import re
from datetime import datetime, timedelta

from agent_collect.discovery.evidence import _CLOCK_IN_QUOTE, _replay_claim
from agent_collect.extraction.preprocess import preprocess_html
from agent_collect.extraction.validation import _dates_in_text, parse_explicit_datetime
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
        r"^(?:application method|how to apply|신청 방법|접수 방법)\s*[:：]\s*(.+)$", re.I),
    "category": re.compile(r"^(?:category|분류|유형)\s*[:：]\s*(.+)$", re.I),
}
_DATES = frozenset({"application_start", "application_deadline", "event_start", "event_end"})


def _date_value(text: str) -> str | None:
    exact = list(_EXACT.finditer(text))
    if exact:
        if len(exact) != 1 or parse_explicit_datetime(exact[0].group()) is None:
            return None
        remainder = text[:exact[0].start()] + text[exact[0].end():]
        return (exact[0].group() if not _dates_in_text(remainder) and
                not _CLOCK_IN_QUOTE.search(remainder) else None)
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
    prepared = preprocess_html(page, source_url=page_url, max_input_chars=12000)
    facts: dict[str, MetadataFact] = {}
    for name, pattern in _LABELS.items():
        matches = [(block, pattern.fullmatch(block.text))
                   for block in prepared.evidence_blocks if len(block.text) <= 500]
        matches = [(block, match) for block, match in matches if match is not None]
        if len(matches) != 1:
            continue
        block, match = matches[0]
        value = (_date_value(match.group(1)) if name in _DATES
                 else match.group(1).strip())
        if not value or len(value) > 200:
            continue
        fact = MetadataFact(value, page_url, EvidenceType.PAGE_BLOCK,
                            block.block_id, block.text, fetched_at, 0.95,
                            policy.permission_digest)
        replay_name = {"application_start": "registration_start",
                       "application_deadline": "registration_deadline"}.get(name, name)
        from agent_collect.discovery.evidence import EvidenceClaim
        replayed, _, error = _replay_claim(
            replay_name, EvidenceClaim(value, block.text, (block.block_id,), ()),
            prepared, page_url)
        if error is None and replayed is not None:
            facts[name] = fact
    page_links = [link for link in prepared.link_evidence
                  if link.link_id == "L000" and _same_url(link.href, page_url)]
    if len(page_links) == 1:
        facts["official_url"] = MetadataFact(
            page_url, page_url, EvidenceType.PAGE_LINK, "L000", "",
            fetched_at, 0.95, policy.permission_digest)
    return facts
