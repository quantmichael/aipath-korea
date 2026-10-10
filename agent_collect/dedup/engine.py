"""Conservative URL, title, organizer and date-aware Conference deduplication."""

import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import urlsplit, urlunsplit

from agent_collect.quality_gate.models import (
    DuplicateRecord, DuplicateStatus, QualityCandidate, QualityDuplicateResult,
)
from agent_collect.registry.service import canonicalize_url


def normalize_title(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[^\W_]+", text, flags=re.UNICODE))


def _url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return canonicalize_url(value)
    except (TypeError, ValueError):
        return None


def _soft_url(value: str | None) -> str | None:
    canonical = _url(value)
    if canonical is None:
        return None
    parts = urlsplit(canonical)
    host = parts.hostname or ""
    if host.startswith("www."):
        host = host[4:]
    # Scheme/www equivalence is a review hint, not an exact-match proof.
    return urlunsplit(("https", host, parts.path, parts.query, ""))


def _date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return value if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else None


def candidate_record(candidate: QualityCandidate) -> DuplicateRecord:
    return DuplicateRecord(
        id=str(candidate.id), origin="agent", title=candidate.value("title"),
        organizer=candidate.value("organizer"), official_url=candidate.value("official_url"),
        registration_url=candidate.value("registration_url"),
        canonical_url=candidate.value("official_url"), event_start=candidate.value("event_start"),
        raw_document_id=str(candidate.raw_document_id),
    )


class DeduplicationEngine:
    def __init__(self, *, similarity_threshold: float = 0.88) -> None:
        if not 0 <= similarity_threshold <= 1:
            raise ValueError("similarity threshold must be between 0 and 1")
        self.similarity_threshold = similarity_threshold

    def check(self, candidate: QualityCandidate, records: list[DuplicateRecord]) -> QualityDuplicateResult:
        target = candidate_record(candidate)
        best: tuple[int, float, DuplicateStatus, str, DuplicateRecord] | None = None
        for other in records:
            if other.origin == "agent" and other.id == target.id:
                continue
            result = self._compare(target, other)
            if result is None:
                continue
            rank, score, status, kind = result
            if best is None or (rank, score) > (best[0], best[1]):
                best = (rank, score, status, kind, other)
        now = datetime.now(timezone.utc)
        if best is None:
            return QualityDuplicateResult(candidate.id, DuplicateStatus.UNIQUE, "none", 0,
                                          ("no_match",), now)
        _, score, status, kind, other = best
        return QualityDuplicateResult(
            candidate.id, status, kind, score, (kind,), now,
            matched_candidate_id=other.id if other.origin == "agent" else None,
            matched_service_record_id=other.id if other.origin == "service" else None,
        )

    def _compare(self, a: DuplicateRecord, b: DuplicateRecord) -> tuple[int, float, DuplicateStatus, str] | None:
        ad, bd = _date(a.event_start), _date(b.event_start)
        if ad and bd and ad[:4] != bd[:4]:
            return None  # Different annual editions are distinct events.
        if a.canonical_url and b.canonical_url and _url(a.canonical_url) == _url(b.canonical_url):
            return (80, 1, DuplicateStatus.DUPLICATE, "canonical_url")
        if a.official_url and b.official_url and _url(a.official_url) == _url(b.official_url):
            return (70, 1, DuplicateStatus.DUPLICATE, "official_url")
        if a.registration_url and b.registration_url and _url(a.registration_url) == _url(b.registration_url):
            return (60, 1, DuplicateStatus.DUPLICATE, "registration_url")
        title_a, title_b = normalize_title(a.title), normalize_title(b.title)
        org_a, org_b = normalize_title(a.organizer), normalize_title(b.organizer)
        if title_a and title_a == title_b:
            if ad and bd and ad == bd:
                return (55, 1, DuplicateStatus.DUPLICATE, "title_date")
            return (40, 1, DuplicateStatus.POSSIBLE_DUPLICATE, "normalized_title")
        if (a.raw_document_id and b.raw_document_id
                and a.raw_document_id == b.raw_document_id):
            # Versioned extractions of one raw page need reconciliation even
            # when an older extraction has no validated title or date.
            return (35, 1, DuplicateStatus.POSSIBLE_DUPLICATE, "same_raw_document")
        if org_a and org_a == org_b and ad and ad == bd:
            return (30, 0.9, DuplicateStatus.POSSIBLE_DUPLICATE, "organizer_date")
        if a.official_url and b.official_url and _soft_url(a.official_url) == _soft_url(b.official_url):
            return (25, 0.95, DuplicateStatus.POSSIBLE_DUPLICATE, "url_scheme_or_www")
        if title_a and title_b:
            first_a, first_b = title_a.split()[0], title_b.split()[0]
            if (first_a != first_b and len(first_a) <= 8 and len(first_b) <= 8
                    and first_a.isalpha() and first_b.isalpha()):
                # Similar-looking short conference acronyms (ICCAI/IJCAI)
                # are not evidence of the same event.
                return None
            score = SequenceMatcher(None, title_a, title_b).ratio()
            if score >= self.similarity_threshold:
                return (10, score, DuplicateStatus.POSSIBLE_DUPLICATE, "title_similarity")
        return None
