"""Offline registration preview for a freshly checked individual notice.

Search text never enters the Service record. This prepares an envelope for the
existing atomic RPC, but deliberately performs no database or network I/O.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo

from agent_collect.dedup.engine import DeduplicationEngine, normalize_title
from agent_collect.discovery.auto_approval import ApprovalScope
from agent_collect.discovery.followup import FollowupLead
from agent_collect.discovery.models import SearchResult
from agent_collect.discovery.quality import (
    Authority, OfficialPageEvidence, PageType, SearchQualityEvaluator, TemporalValidity,
)
from agent_collect.metadata.category_policy import category_policy
from agent_collect.metadata.extraction import (
    PublicNoticeFact, extract_labelled_public_facts,
)
from agent_collect.metadata.evidence import MetadataUsageRights
from agent_collect.quality_gate.models import DuplicateRecord
from agent_collect.extraction.preprocess import preprocess_html
from agent_collect.registry.service import canonicalize_url


_REQUIRED = ("title", "organizer", "official_url", "application_deadline",
             "application_method", "category")
_CATEGORY_NAMES = {
    "경진대회": "competition", "공모전": "competition",
    "해커톤": "hackathon", "교육": "education",
    "세미나": "meetup", "컨퍼런스": "conference",
}
_DATE_FIELDS = {"application_start": "application_start_at",
                "application_deadline": "application_deadline_at",
                "event_start": "event_start_at", "event_end": "event_end_at"}
TECHNICAL_SOURCE_NAME = "AIPATH AI Discovery"
_METADATA_RIGHTS = ("public_read", "automated_access", "fact_extract",
                    "fact_store", "fact_display")


class _HeadingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headings: list[str] = []
        self._parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "h1":
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._parts is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1" and self._parts is not None:
            self.headings.append(" ".join(" ".join(self._parts).split()))
            self._parts = None


@dataclass(frozen=True, slots=True)
class SearchUseReview:
    permitted: bool
    reason: str
    evidence_ref: str | None = None
    valid_until: datetime | None = None


def verify_scoped_publication_use(*, scope: ApprovalScope,
                                  rights: MetadataUsageRights | None,
                                  target_url: str,
                                  officiality_document: bytes,
                                  terms_document: bytes,
                                  checked_at: datetime,
                                  now: datetime) -> SearchUseReview:
    """Replay a separately reviewed display-rights snapshot against pinned docs.

    The caller must fetch all three pages through the existing robots-aware,
    DNS-pinned transport. This verifies document integrity and use scope only;
    it does not treat robots permission as a publication license.
    """
    if (now.tzinfo is None or checked_at.tzinfo is None or
            not now - timedelta(minutes=15) <= checked_at <= now or
            not scope.reviewed_at <= now < scope.expires_at or
            not scope.covers(target_url)):
        return SearchUseReview(False, "SCOPE_EXPIRED_OR_OUTSIDE_PATH")
    if rights is None or not rights.metadata_permitted():
        return SearchUseReview(False, "METADATA_DISPLAY_RIGHTS_UNCONFIRMED")
    if (not officiality_document or not terms_document or
            len(officiality_document) > 1_048_576 or
            len(terms_document) > 1_048_576 or
            hashlib.sha256(officiality_document).hexdigest() != scope.officiality_sha256 or
            hashlib.sha256(terms_document).hexdigest() != scope.terms_sha256):
        return SearchUseReview(False, "PERMISSION_DOCUMENT_CHANGED")
    try:
        prepared = preprocess_html(terms_document, source_url=scope.terms_url,
                                   max_input_chars=12000)
    except (ValueError, UnicodeError):
        return SearchUseReview(False, "PERMISSION_DOCUMENT_UNREADABLE")
    available = {block.block_id for block in prepared.evidence_blocks}
    if any(rights.evidence_references.get(action) not in available
           for action in _METADATA_RIGHTS):
        return SearchUseReview(False, "DISPLAY_RIGHTS_EVIDENCE_NOT_REPLAYED")
    return SearchUseReview(
        True, "REVIEWED_SCOPE_AND_DISPLAY_RIGHTS_REPLAYED",
        f"scope:{scope.scope_id}:terms-sha256:{scope.terms_sha256}",
        scope.expires_at)


def resolve_discovery_service_source(rows: list[dict[str, Any]]) -> UUID | None:
    """Resolve one collection-channel Source, never an inferred official publisher.

    This row is provisioned separately; this code neither creates it nor
    treats it as permission to access any discovered website.
    """
    matches = [row for row in rows if
               row.get("name") == TECHNICAL_SOURCE_NAME and
               row.get("source_type") == "discovery" and
               row.get("collection_method") == "ai_search" and
               row.get("is_active") is True]
    if len(matches) != 1:
        return None
    try:
        return UUID(str(matches[0]["id"]))
    except (ValueError, TypeError, KeyError):
        return None


@dataclass(frozen=True, slots=True)
class SearchRegistrationPreview:
    eligible: bool
    reasons: tuple[str, ...]
    record: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    prepared_at: datetime | None = None
    permission_valid_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class SearchRegistrationResult:
    status: str
    opportunity_id: UUID | None = None


class AtomicServiceRegistration(Protocol):
    def list_opportunities(self) -> list[dict[str, Any]]: ...

    def register_opportunity(self, payload: dict[str, Any],
                             evidence: dict[str, Any]) -> tuple[dict[str, Any], str]: ...


def publish_one_search_preview(preview: SearchRegistrationPreview,
                               tables: AtomicServiceRegistration, *,
                               publish_enabled: bool = False,
                               now: datetime | None = None) -> SearchRegistrationResult:
    """Submit one eligible envelope through Service 004; never retry uncertainty.

    This is intentionally not connected to AutomationRuntime or a default-on
    flag. The existing RPC owns the transaction, status evidence and URL lock.
    """
    if not publish_enabled:
        return SearchRegistrationResult("DISABLED")
    if not preview.eligible or preview.record is None or preview.evidence is None:
        return SearchRegistrationResult("HELD")
    checked_now = now if now is not None else datetime.now(timezone.utc)
    status = _registration_window_status(preview, checked_now)
    if status is not None:
        return SearchRegistrationResult(status)
    try:
        current_rows = tables.list_opportunities()
        if _matches_existing(preview.record, current_rows):
            return SearchRegistrationResult("DUPLICATE")
    except Exception:
        return SearchRegistrationResult("UNKNOWN")
    # The final Service scan may span the recruitment cutoff or permission
    # expiry. Runtime calls with the live clock, so check once more before RPC.
    if now is None:
        status = _registration_window_status(preview, datetime.now(timezone.utc))
        if status is not None:
            return SearchRegistrationResult(status)
    try:
        row, disposition = tables.register_opportunity(preview.record, preview.evidence)
    except Exception:
        return SearchRegistrationResult("UNKNOWN")
    if disposition == "protected_existing":
        return SearchRegistrationResult("DUPLICATE")
    if (disposition != "registered" or
            any(row.get(key) != preview.record[key] for key in
                ("source_id", "category_id", "official_url", "slug")) or
            row.get("status") not in {"scheduled", "open", "ongoing"}):
        return SearchRegistrationResult("UNKNOWN")
    try:
        return SearchRegistrationResult("PUBLISHED", UUID(str(row["id"])))
    except (KeyError, ValueError, TypeError):
        return SearchRegistrationResult("UNKNOWN")


def _registration_window_status(preview: SearchRegistrationPreview,
                                checked_now: datetime) -> str | None:
    if (checked_now.tzinfo is None or preview.prepared_at is None or
            not preview.prepared_at <= checked_now <=
            preview.prepared_at + timedelta(minutes=1) or
            preview.permission_valid_until is None or
            checked_now >= preview.permission_valid_until):
        return "STALE"
    deadline = preview.evidence.get("application_deadline")
    try:
        if deadline["precision"] == "EXACT_TIME":
            cutoff = datetime.fromisoformat(preview.record["application_deadline_at"])
            if cutoff.tzinfo is None or checked_now >= cutoff:
                return "STALE"
        elif deadline["precision"] == "DATE_ONLY":
            cutoff_day = date.fromisoformat(deadline["source_date"])
            if checked_now.astimezone(ZoneInfo("Asia/Seoul")).date() >= cutoff_day:
                return "STALE"
        else:
            return "UNKNOWN"
    except (KeyError, TypeError, ValueError):
        return "UNKNOWN"
    return None


def _time(fact: PublicNoticeFact) -> tuple[str, dict[str, str]] | None:
    literal = fact.value
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", literal):
        try:
            day = date.fromisoformat(literal)
        except ValueError:
            return None
        return (f"{day.isoformat()}T00:00:00+00:00",
                {"precision": "DATE_ONLY", "source_date": day.isoformat(),
                 "source_ref": fact.evidence_reference})
    try:
        parsed = datetime.fromisoformat(literal.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return (parsed.astimezone(timezone.utc).isoformat(),
            {"precision": "EXACT_TIME", "source_literal": parsed.isoformat(),
             "source_text": literal,
             "source_ref": fact.evidence_reference})


def _matches_existing(record: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    url = canonicalize_url(record["official_url"])
    title = normalize_title(record.get("title"))
    organizer = normalize_title(record.get("organizer"))
    target = DuplicateRecord(
        id="search-preview", origin="agent", title=record.get("title"),
        organizer=record.get("organizer"), official_url=url,
        registration_url=None, canonical_url=url,
        event_start=record.get("event_start_at"),
    )
    dedup = DeduplicationEngine()
    for row in rows:
        try:
            same_url = canonicalize_url(row.get("official_url")) == url
        except (ValueError, TypeError):
            same_url = False
        same_identity = (title and organizer and
                         normalize_title(row.get("title") or "") == title and
                         normalize_title(row.get("organizer") or "") == organizer)
        other = DuplicateRecord(
            id=str(row.get("id") or "service-preview"), origin="service",
            title=row.get("title"), organizer=row.get("organizer"),
            official_url=row.get("official_url"), registration_url=None,
            canonical_url=None, event_start=row.get("event_start_at"),
        )
        if same_url or same_identity or dedup.compare_records(target, other) is not None:
            return True
    return False


def preview_fetched_search_result(*, search_result: SearchResult,
                                  page: bytes, page_url: str,
                                  official_page: OfficialPageEvidence,
                                  service_sources: list[dict[str, Any]],
                                  service_categories: list[dict[str, Any]],
                                  existing_rows: list[dict[str, Any]],
                                  snapshot_complete: bool,
                                  use_review: SearchUseReview,
                                  fetched_at: datetime,
                                  now: datetime,
                                  facts_override: dict[str, PublicNoticeFact] | None = None,
                                  ) -> SearchRegistrationPreview:
    """Turn one checked search-result page into a read-only registration preview."""
    facts = (dict(facts_override) if facts_override is not None else
             extract_labelled_public_facts(
                 page=page, page_url=page_url, fetched_at=fetched_at, now=now))
    if "title" not in facts:
        headings = _HeadingParser()
        headings.feed(page.decode("utf-8", errors="replace"))
        prepared = preprocess_html(page, source_url=page_url, max_input_chars=12000)
        expected = normalize_title(search_result.title)
        matching = [block for block in prepared.evidence_blocks
                    if (expected and len(block.text) <= 200 and
                        normalize_title(block.text) == expected)]
        if (len(headings.headings) == 1 and len(matching) == 1 and
                normalize_title(headings.headings[0]) == expected):
            block = matching[0]
            facts["title"] = PublicNoticeFact(block.text, block.block_id, block.text)
    category = facts.get("category")
    policy = category_policy(_CATEGORY_NAMES.get(
        category.value.strip(), category.value) if category else None)
    slug = policy.service_slug if policy else None
    matches = [row for row in service_categories if row.get("slug") == slug and
               row.get("is_active") is True] if slug else []
    category_id = None
    if len(matches) == 1:
        try:
            category_id = UUID(str(matches[0]["id"]))
        except (KeyError, ValueError, TypeError):
            pass
    return preview_search_registration(
        facts=facts, official_page=official_page, search_result=search_result,
        service_sources=service_sources, category_id=category_id,
        category_slug=slug, access_allowed=use_review.permitted,
        metadata_use_allowed=use_review.permitted,
        access_evidence_ref=use_review.evidence_ref,
        permission_valid_until=use_review.valid_until,
        page_sha256=hashlib.sha256(page).hexdigest(),
        existing_rows=existing_rows, snapshot_complete=snapshot_complete,
        now=now)


def preview_scoped_fetched_search_result(*, search_result: SearchResult,
                                         page: bytes, page_url: str,
                                         official_page: OfficialPageEvidence,
                                         scope: ApprovalScope,
                                         rights: MetadataUsageRights | None,
                                         officiality_document: bytes,
                                         terms_document: bytes,
                                         permission_checked_at: datetime,
                                         service_sources: list[dict[str, Any]],
                                         service_categories: list[dict[str, Any]],
                                         existing_rows: list[dict[str, Any]],
                                         snapshot_complete: bool,
                                         fetched_at: datetime,
                                         now: datetime) -> SearchRegistrationPreview:
    """Prepare one notice only after a reviewed scope and display rights replay."""
    review = verify_scoped_publication_use(
        scope=scope, rights=rights, target_url=page_url,
        officiality_document=officiality_document,
        terms_document=terms_document,
        checked_at=permission_checked_at, now=now)
    preview = preview_fetched_search_result(
        search_result=search_result, page=page, page_url=page_url,
        official_page=official_page, service_sources=service_sources,
        service_categories=service_categories, existing_rows=existing_rows,
        snapshot_complete=snapshot_complete, use_review=review,
        fetched_at=fetched_at, now=now)
    if not review.permitted:
        return SearchRegistrationPreview(
            False, tuple(dict.fromkeys((*preview.reasons, review.reason))))
    return preview


def preview_scoped_followup_lead(*, search_result: SearchResult,
                                lead: FollowupLead,
                                page: bytes,
                                scope: ApprovalScope,
                                rights: MetadataUsageRights | None,
                                officiality_document: bytes,
                                terms_document: bytes,
                                permission_checked_at: datetime,
                                service_sources: list[dict[str, Any]],
                                service_categories: list[dict[str, Any]],
                                existing_rows: list[dict[str, Any]],
                                snapshot_complete: bool,
                                fetched_at: datetime,
                                now: datetime) -> SearchRegistrationPreview:
    """Use an actual bounded Follow-up lead, not the listing search snippet."""
    try:
        parent = canonicalize_url(search_result.canonical_url or search_result.original_url)
        target = canonicalize_url(lead.url)
        if (lead.depth != 1 or lead.page_type != "INDIVIDUAL_NOTICE" or
                canonicalize_url(lead.parent_url) != parent or target == parent or
                not scope.covers(lead.url)):
            return SearchRegistrationPreview(False, ("FOLLOWUP_INDIVIDUAL_UNVERIFIED",))
        if (lead.page_sha256 is None or
                hashlib.sha256(page).hexdigest() != lead.page_sha256):
            return SearchRegistrationPreview(False, ("FOLLOWUP_PAGE_CHANGED",))
        facts = extract_labelled_public_facts(
            page=page, page_url=lead.url, fetched_at=fetched_at, now=now)
    except (ValueError, TypeError, UnicodeError):
        return SearchRegistrationPreview(False, ("FOLLOWUP_PAGE_UNVERIFIED",))
    dates: dict[str, date | datetime] = {}
    for field in _DATE_FIELDS:
        fact = facts.get(field)
        parsed = _time(fact) if fact is not None else None
        if parsed is None:
            continue
        _, claim = parsed
        if claim["precision"] == "DATE_ONLY":
            dates[field] = date.fromisoformat(claim["source_date"])
        else:
            dates[field] = datetime.fromisoformat(claim["source_literal"])
    start = dates.get("application_start")
    deadline = dates.get("application_deadline")
    if not deadline or (start is not None and type(start) is not type(deadline)):
        return SearchRegistrationPreview(False, ("RECRUITMENT_DATES_UNVERIFIED",))
    page_evidence = OfficialPageEvidence(
        lead.url, fetched_at, Authority.OFFICIAL_ORGANIZER,
        PageType.INDIVIDUAL_NOTICE,
        recruitment_start_at=start if isinstance(start, datetime) else None,
        recruitment_deadline_at=deadline if isinstance(deadline, datetime) else None,
        recruitment_start_on=start if type(start) is date else None,
        recruitment_deadline_on=deadline if type(deadline) is date else None,
        event_start_on=dates.get("event_start") if type(dates.get("event_start")) is date else None,
        event_end_on=dates.get("event_end") if type(dates.get("event_end")) is date else None,
        event_identity=facts.get("title").value if facts.get("title") else None,
        event_start_at=dates.get("event_start") if isinstance(dates.get("event_start"), datetime) else None,
        event_end_at=dates.get("event_end") if isinstance(dates.get("event_end"), datetime) else None)
    individual_result = replace(search_result, original_url=lead.url,
                                canonical_url=target, filter_reasons=())
    return preview_scoped_fetched_search_result(
        search_result=individual_result, page=page, page_url=lead.url,
        official_page=page_evidence, scope=scope, rights=rights,
        officiality_document=officiality_document,
        terms_document=terms_document,
        permission_checked_at=permission_checked_at,
        service_sources=service_sources, service_categories=service_categories,
        existing_rows=existing_rows, snapshot_complete=snapshot_complete,
        fetched_at=fetched_at, now=now)


def preview_scoped_direct_result(*, search_result: SearchResult,
                                 page: bytes, page_url: str,
                                 scope: ApprovalScope,
                                 rights: MetadataUsageRights | None,
                                 officiality_document: bytes,
                                 terms_document: bytes,
                                 permission_checked_at: datetime,
                                 service_sources: list[dict[str, Any]],
                                 service_categories: list[dict[str, Any]],
                                 existing_rows: list[dict[str, Any]],
                                 snapshot_complete: bool,
                                 fetched_at: datetime,
                                 now: datetime) -> SearchRegistrationPreview:
    """Accept a root SearchResult only within one reviewed notice directory.

    A listing or homepage cannot be promoted by a search title alone. The
    fetched page must have one matching H1 and the existing fact/evidence gate
    still decides whether it contains a complete individual opportunity.
    """
    review = verify_scoped_publication_use(
        scope=scope, rights=rights, target_url=page_url,
        officiality_document=officiality_document,
        terms_document=terms_document,
        checked_at=permission_checked_at, now=now)
    return _preview_direct_result(
        search_result=search_result, page=page, page_url=page_url,
        path_prefix=scope.path_prefix, covers=scope.covers,
        use_review=review, service_sources=service_sources,
        service_categories=service_categories, existing_rows=existing_rows,
        snapshot_complete=snapshot_complete, fetched_at=fetched_at, now=now)


def preview_public_direct_result(*, search_result: SearchResult,
                                 page: bytes, page_url: str,
                                 service_sources: list[dict[str, Any]],
                                 service_categories: list[dict[str, Any]],
                                 existing_rows: list[dict[str, Any]],
                                 snapshot_complete: bool,
                                 fetched_at: datetime,
                                 now: datetime) -> SearchRegistrationPreview:
    """Facts-only path for a public institutional notice; no raw page storage."""
    try:
        parts = urlsplit(page_url)
        host = parts.hostname or ""
        if (parts.scheme != "https" or
                not (host.endswith(".go.kr") or host.endswith(".ac.kr")) or
                parts.fragment):
            return SearchRegistrationPreview(False, ("OFFICIAL_DOMAIN_UNVERIFIED",))
        # The transport caps the response at 1 MiB. Inspect the entire page
        # for an explicit prohibition before extraction truncates its evidence.
        text = unescape(re.sub(r"<[^>]*>", " ",
                               page.decode("utf-8", errors="replace")))
        if re.search(
                r"(?:무단\s*(?:수집|재이용)|크롤링|자동화된\s*접근)\s*금지|"
                r"scraping\s+prohibited|automated\s+access\s+prohibited|"
                r"republication\s+prohibited", text, re.I):
            return SearchRegistrationPreview(False, ("EXPLICIT_USE_PROHIBITION",))
        path = parts.path.rstrip("/")
        parent = path.rpartition("/")[0] + "/"
        if not path or parent == "/" or not parts.path.startswith(parent):
            return SearchRegistrationPreview(False, ("INDIVIDUAL_PATH_UNVERIFIED",))
    except (ValueError, TypeError, UnicodeError):
        return SearchRegistrationPreview(False, ("PUBLIC_PAGE_UNVERIFIED",))
    review = SearchUseReview(True, "public_institution_facts_only",
                             f"public-facts:robots-allowed:{host}",
                             now + timedelta(minutes=15))
    return _preview_direct_result(
        search_result=search_result, page=page, page_url=page_url,
        path_prefix=parent,
        covers=lambda target: canonicalize_url(target) == canonicalize_url(page_url),
        use_review=review, service_sources=service_sources,
        service_categories=service_categories, existing_rows=existing_rows,
        snapshot_complete=snapshot_complete, fetched_at=fetched_at, now=now,
        allow_query=True, allow_search_title_suffix=True,
        authority=Authority.OFFICIAL_INSTITUTION)


def _preview_direct_result(*, search_result: SearchResult,
                           page: bytes, page_url: str, path_prefix: str,
                           covers: Any, use_review: SearchUseReview,
                           service_sources: list[dict[str, Any]],
                           service_categories: list[dict[str, Any]],
                           existing_rows: list[dict[str, Any]],
                           snapshot_complete: bool, fetched_at: datetime,
                           now: datetime, allow_query: bool = False,
                           allow_search_title_suffix: bool = False,
                           authority: Authority = Authority.OFFICIAL_ORGANIZER,
                           ) -> SearchRegistrationPreview:
    try:
        target = canonicalize_url(page_url)
        root = canonicalize_url(search_result.canonical_url or search_result.original_url)
        path = urlsplit(target).path.rstrip("/")
        parent_directory = path.rpartition("/")[0] + "/"
        if (target != root or not path or parent_directory != path_prefix or
                (urlsplit(target).query and not allow_query) or not covers(page_url)):
            return SearchRegistrationPreview(False, ("DIRECT_NOTICE_SCOPE_UNVERIFIED",))
        headings = _HeadingParser()
        headings.feed(page.decode("utf-8", errors="replace"))
        expected = normalize_title(search_result.title)
        if len(headings.headings) != 1:
            return SearchRegistrationPreview(False, ("DIRECT_NOTICE_TITLE_UNVERIFIED",))
        page_title = normalize_title(headings.headings[0])
        title_matches = (page_title == expected or
                         allow_search_title_suffix and bool(page_title) and
                         expected.startswith(page_title) and
                         len(page_title) / len(expected) >= 0.65)
        if not expected or not title_matches:
            return SearchRegistrationPreview(False, ("DIRECT_NOTICE_TITLE_UNVERIFIED",))
        facts = extract_labelled_public_facts(
            page=page, page_url=page_url, fetched_at=fetched_at, now=now)
        if ("title" in facts and
                normalize_title(facts["title"].value) != page_title):
            return SearchRegistrationPreview(False, ("DIRECT_NOTICE_TITLE_UNVERIFIED",))
        prepared = preprocess_html(page, source_url=page_url, max_input_chars=12000)
        matches = [block for block in prepared.evidence_blocks
                   if len(block.text) <= 200 and
                   normalize_title(block.text) == page_title]
        if "title" not in facts:
            if len(matches) != 1:
                return SearchRegistrationPreview(False, ("DIRECT_NOTICE_TITLE_UNVERIFIED",))
            facts["title"] = PublicNoticeFact(matches[0].text,
                                               matches[0].block_id,
                                               matches[0].text)
        if allow_search_title_suffix and "category" not in facts and len(matches) == 1:
            labels = {
                "competition": ("경진대회", "공모전"),
                "hackathon": ("해커톤",),
                "education": ("교육", "부트캠프"),
                "meetup": ("세미나", "밋업"),
                "conference": ("컨퍼런스", "학회", "심포지엄"),
            }
            found = [slug for slug, words in labels.items()
                     if any(word in headings.headings[0] for word in words)]
            if len(found) == 1:
                facts["category"] = PublicNoticeFact(
                    found[0], matches[0].block_id, headings.headings[0])
        dates: dict[str, date | datetime] = {}
        for field in _DATE_FIELDS:
            fact = facts.get(field)
            parsed = _time(fact) if fact is not None else None
            if parsed is None:
                continue
            _, claim = parsed
            dates[field] = (date.fromisoformat(claim["source_date"])
                            if claim["precision"] == "DATE_ONLY" else
                            datetime.fromisoformat(claim["source_literal"]))
        start = dates.get("application_start")
        deadline = dates.get("application_deadline")
        if not deadline or (start is not None and type(start) is not type(deadline)):
            return SearchRegistrationPreview(False, ("RECRUITMENT_DATES_UNVERIFIED",))
        page_evidence = OfficialPageEvidence(
            page_url, fetched_at, authority,
            PageType.INDIVIDUAL_NOTICE,
            recruitment_start_at=start if isinstance(start, datetime) else None,
            recruitment_deadline_at=deadline if isinstance(deadline, datetime) else None,
            recruitment_start_on=start if type(start) is date else None,
            recruitment_deadline_on=deadline if type(deadline) is date else None,
            event_start_on=dates.get("event_start") if type(dates.get("event_start")) is date else None,
            event_end_on=dates.get("event_end") if type(dates.get("event_end")) is date else None,
            event_identity=facts["title"].value,
            event_start_at=dates.get("event_start") if isinstance(dates.get("event_start"), datetime) else None,
            event_end_at=dates.get("event_end") if isinstance(dates.get("event_end"), datetime) else None)
    except (ValueError, TypeError, UnicodeError):
        return SearchRegistrationPreview(False, ("DIRECT_NOTICE_PAGE_UNVERIFIED",))
    return preview_fetched_search_result(
        search_result=replace(search_result, title=headings.headings[0], filter_reasons=()),
        page=page, page_url=page_url, official_page=page_evidence,
        service_sources=service_sources,
        service_categories=service_categories,
        existing_rows=existing_rows,
        snapshot_complete=snapshot_complete, use_review=use_review,
        facts_override=facts,
        fetched_at=fetched_at, now=now)


def preview_search_registration(*, facts: dict[str, PublicNoticeFact],
                                official_page: OfficialPageEvidence,
                                search_result: SearchResult,
                                service_sources: list[dict[str, Any]],
                                category_id: UUID | None,
                                category_slug: str | None,
                                access_allowed: bool,
                                metadata_use_allowed: bool,
                                access_evidence_ref: str | None,
                                permission_valid_until: datetime | None,
                                page_sha256: str | None,
                                existing_rows: list[dict[str, Any]],
                                snapshot_complete: bool,
                                now: datetime) -> SearchRegistrationPreview:
    """Fail closed on provenance, lifecycle, ambiguity, or stale Service reads.

    A caller may use the returned envelope only after independently checking
    page access/usage rights and the Service Source identity. This function
    does not grant Source approval or replace the final atomic RPC checks.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("current time must be timezone-aware")
    quality = SearchQualityEvaluator().assess(
        search_result, now=now, evidence=official_page)
    service_source_id = resolve_discovery_service_source(service_sources)
    reasons: list[str] = []
    if not snapshot_complete:
        reasons.append("SERVICE_SNAPSHOT_INCOMPLETE")
    if service_source_id is None:
        reasons.append("SERVICE_SOURCE_UNRESOLVED")
    if category_id is None:
        reasons.append("CATEGORY_UNRESOLVED")
    if (not access_allowed or not metadata_use_allowed or
            not access_evidence_ref or not access_evidence_ref.strip()):
        reasons.append("ACCESS_OR_METADATA_USE_UNCONFIRMED")
    if (permission_valid_until is None or permission_valid_until.tzinfo is None or
            now >= permission_valid_until):
        reasons.append("PERMISSION_EXPIRED_OR_UNBOUNDED")
    if not page_sha256 or not re.fullmatch(r"[0-9a-f]{64}", page_sha256):
        reasons.append("PAGE_DIGEST_MISSING")
    if (official_page.page_type is not PageType.INDIVIDUAL_NOTICE or
            not quality.official_source or not quality.individual_opportunity):
        reasons.append("OFFICIAL_INDIVIDUAL_UNVERIFIED")
    if quality.duplicate_reason is not None:
        reasons.append("DISCOVERY_DUPLICATE")
    if (official_page.checked_at > now or
            now - official_page.checked_at > timedelta(minutes=15)):
        reasons.append("PAGE_CHECK_STALE")
    if any(key not in facts or not facts[key].value.strip() for key in _REQUIRED):
        reasons.append("REQUIRED_PAGE_FACT_MISSING")
    if any(key in facts and (not facts[key].evidence_reference.strip() or
                             (key != "official_url" and not facts[key].quote.strip()))
           for key in _REQUIRED):
        reasons.append("PAGE_FACT_EVIDENCE_MISSING")
    try:
        url = canonicalize_url(official_page.source_url)
        if ("official_url" not in facts or
                canonicalize_url(facts["official_url"].value) != url):
            reasons.append("PAGE_URL_MISMATCH")
    except (ValueError, TypeError):
        url = None
        reasons.append("PAGE_URL_INVALID")
    policy = category_policy(_CATEGORY_NAMES.get(
        facts.get("category", PublicNoticeFact("", "", "")).value.strip(),
        facts.get("category", PublicNoticeFact("", "", "")).value))
    if policy is None or not policy.publish_gate_supported:
        reasons.append("CATEGORY_UNSUPPORTED")
    elif category_slug != policy.service_slug:
        reasons.append("CATEGORY_IDENTITY_MISMATCH")
    if policy is not None and policy.event_window_required and not all(
            name in facts and facts[name].value.strip() for name in ("event_start", "event_end")):
        reasons.append("CATEGORY_EVENT_WINDOW_MISSING")
    times: dict[str, tuple[str, dict[str, str]]] = {}
    for name in _DATE_FIELDS:
        if name in facts:
            parsed = _time(facts[name])
            if parsed is None:
                reasons.append(f"{name.upper()}_AMBIGUOUS")
            else:
                times[name] = parsed
    if "application_deadline" not in times:
        reasons.append("RECRUITMENT_DATES_MISSING")
    if policy is not None and policy.event_window_required and not {
            "event_start", "event_end"} <= times.keys():
        reasons.append("CATEGORY_EVENT_WINDOW_UNVERIFIED")
    for start, end in (("application_start", "application_deadline"),
                       ("event_start", "event_end")):
        if start in times and end in times:
            start_value, start_claim = times[start]
            end_value, end_claim = times[end]
            if (start_claim["precision"] == end_claim["precision"] and
                    start_value > end_value):
                reasons.append(f"{start.upper()}_AFTER_{end.upper()}")
    for name, (stored, claim) in times.items():
        source_value = getattr(official_page, {
            "application_start": "recruitment_start_at",
            "application_deadline": "recruitment_deadline_at",
            "event_start": "event_start_at" if claim["precision"] == "EXACT_TIME" else "event_start_on",
            "event_end": "event_end_at" if claim["precision"] == "EXACT_TIME" else "event_end_on",
        }[name])
        if name.startswith("application_") and claim["precision"] == "DATE_ONLY":
            source_value = getattr(official_page, {
                "application_start": "recruitment_start_on",
                "application_deadline": "recruitment_deadline_on",
            }[name])
        if claim["precision"] == "DATE_ONLY":
            if source_value is None or source_value.isoformat() != claim["source_date"]:
                reasons.append(f"{name.upper()}_EVIDENCE_MISMATCH")
        elif (not isinstance(source_value, datetime) or
              source_value.astimezone(timezone.utc).isoformat() != stored):
            reasons.append(f"{name.upper()}_EVIDENCE_MISMATCH")
    temporal = quality.temporal
    if (temporal is TemporalValidity.UNKNOWN and "application_start" not in facts and
            official_page.recruitment_start_at is None and
            official_page.recruitment_start_on is None and
            "application_deadline" in times):
        stored, claim = times["application_deadline"]
        if claim["precision"] == "EXACT_TIME" and now < datetime.fromisoformat(stored):
            temporal = TemporalValidity.ACTIVE
        elif (claim["precision"] == "DATE_ONLY" and
              now.astimezone(ZoneInfo("Asia/Seoul")).date() <
              date.fromisoformat(claim["source_date"])):
            temporal = TemporalValidity.ACTIVE
    if temporal not in {TemporalValidity.ACTIVE, TemporalValidity.UPCOMING}:
        reasons.append("RECRUITMENT_NOT_CONFIRMED")
    if url:
        if _matches_existing({
                "official_url": url,
                "title": facts.get("title").value if facts.get("title") else None,
                "organizer": facts.get("organizer").value if facts.get("organizer") else None,
                "event_start_at": times.get("event_start", (None, {}))[0],
        }, existing_rows):
            reasons.append("EXISTING_OPPORTUNITY_MATCH")
    if reasons:
        return SearchRegistrationPreview(False, tuple(dict.fromkeys(reasons)))

    assert url is not None and service_source_id is not None and category_id is not None
    assert policy is not None
    identity = hashlib.sha256(url.encode()).hexdigest()
    event_year = times["application_deadline"][0][:4]
    notice_identity = hashlib.sha256(
        (normalize_title(facts["title"].value) + "|" +
         normalize_title(facts["organizer"].value) + "|" + event_year).encode()
    ).hexdigest()
    record: dict[str, Any] = {
        "source_id": str(service_source_id), "category_id": str(category_id),
        "external_id": f"search:{identity}",
        "slug": f"ai-{event_year}-{notice_identity[:24]}",
        "title": facts["title"].value, "organizer": facts["organizer"].value,
        "summary": facts.get("summary", PublicNoticeFact(
            facts["title"].value, "", "")).value,
        "description": facts["application_method"].value,
        "official_url": url,
        "status": "open" if temporal is TemporalValidity.ACTIVE else "scheduled",
        "last_verified_at": official_page.checked_at.isoformat(),
    }
    evidence: dict[str, Any] = {
        "evidence_ref": f"official-page:{identity}",
        "official_url": url,
        "search_result_id": str(search_result.id),
        "page_sha256": page_sha256,
        "page_checked_at": official_page.checked_at.isoformat(),
        "access_evidence_ref": access_evidence_ref,
    }
    for name, column in _DATE_FIELDS.items():
        if name in times:
            record[column] = times[name][0]
            evidence[name] = times[name][1]
        else:
            evidence[name] = {"precision": "UNKNOWN"}
    return SearchRegistrationPreview(True, (), record, evidence, now,
                                     permission_valid_until)
