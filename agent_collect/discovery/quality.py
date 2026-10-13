"""Conservative, offline search-result quality assessment.

Search snippets are discovery leads, never proof of recruitment dates or
collection permission. Only a separately verified official-page evidence
record can establish an active or upcoming individual opportunity.
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from agent_collect.discovery.models import SearchResult
from agent_collect.registry.service import canonicalize_url


KST = ZoneInfo("Asia/Seoul")


class QualityClass(StrEnum):
    ACTIVE_OPPORTUNITY = "ACTIVE_OPPORTUNITY"
    UPCOMING_OPPORTUNITY = "UPCOMING_OPPORTUNITY"
    EXPIRED_OPPORTUNITY = "EXPIRED_OPPORTUNITY"
    OFFICIAL_LISTING_PAGE = "OFFICIAL_LISTING_PAGE"
    NEWS_ARTICLE = "NEWS_ARTICLE"
    EVENT_RECAP = "EVENT_RECAP"
    DUPLICATE = "DUPLICATE"
    UNVERIFIED = "UNVERIFIED"


class Authority(StrEnum):
    OFFICIAL_INSTITUTION = "OFFICIAL_INSTITUTION"
    OFFICIAL_ORGANIZER = "OFFICIAL_ORGANIZER"
    THIRD_PARTY = "THIRD_PARTY"
    UNKNOWN = "UNKNOWN"


class PageType(StrEnum):
    INDIVIDUAL_NOTICE = "INDIVIDUAL_NOTICE"
    OFFICIAL_LISTING = "OFFICIAL_LISTING"
    EVENT_PAGE = "EVENT_PAGE"
    NEWS = "NEWS"
    RECAP = "RECAP"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class TemporalValidity(StrEnum):
    ACTIVE = "ACTIVE"
    UPCOMING = "UPCOMING"
    EXPIRED = "EXPIRED"
    EVENT_ONGOING = "EVENT_ONGOING"
    EVENT_ENDED = "EVENT_ENDED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class OfficialPageEvidence:
    """Facts checked on the original page, never extracted from search snippets."""

    source_url: str
    checked_at: datetime
    authority: Authority
    page_type: PageType
    recruitment_start_at: datetime | None = None
    recruitment_deadline_at: datetime | None = None
    recruitment_start_on: date | None = None
    recruitment_deadline_on: date | None = None
    event_start_on: date | None = None
    event_end_on: date | None = None
    event_identity: str | None = None

    def __post_init__(self) -> None:
        for value in (self.checked_at, self.recruitment_start_at,
                      self.recruitment_deadline_at):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError("official evidence timestamps must have a timezone")
        if urlsplit(self.source_url).scheme != "https":
            raise ValueError("official evidence requires an HTTPS source URL")
        if ((self.recruitment_start_at or self.recruitment_deadline_at) and
                (self.recruitment_start_on or self.recruitment_deadline_on)):
            raise ValueError("do not mix exact and date-only recruitment evidence")


@dataclass(frozen=True, slots=True)
class QualityAssessment:
    classification: QualityClass
    authority: Authority
    page_type: PageType
    temporal: TemporalValidity
    duplicate_reason: str | None
    confidence: str
    evidence_codes: tuple[str, ...]
    uncertainty_codes: tuple[str, ...]
    official_source: bool
    individual_opportunity: bool
    verified_new_opportunity: bool
    followup: str | None = None
    recruitment_start: str | None = None
    recruitment_deadline: str | None = None
    event_start: str | None = None
    event_end: str | None = None

    def audit_record(self, *, candidate_persisted: bool | None = False) -> dict[str, object]:
        """No page text, title, snippet, URL, or model instruction enters feedback."""
        return {
            "classification": self.classification.value,
            "authority": self.authority.value,
            "page_type": self.page_type.value,
            "temporal": self.temporal.value,
            "duplicate_reason": self.duplicate_reason,
            "confidence": self.confidence,
            "evidence_codes": list(self.evidence_codes),
            "uncertainty_codes": list(self.uncertainty_codes),
            "official_source": self.official_source,
            "individual_opportunity": self.individual_opportunity,
            "verified_new_opportunity": self.verified_new_opportunity,
            "followup": self.followup,
            "recruitment_start": self.recruitment_start,
            "recruitment_deadline": self.recruitment_deadline,
            "event_start": self.event_start,
            "event_end": self.event_end,
            "source_candidate_persisted": candidate_persisted,
        }


def listing_followup_plan(result: SearchResult,
                          assessment: QualityAssessment) -> dict[str, object] | None:
    """Record a separate, non-executable search proposal linked to its result."""
    if assessment.classification is not QualityClass.OFFICIAL_LISTING_PAGE:
        return None
    return {
        "kind": "INDIVIDUAL_NOTICE_SEARCH",
        "status": "PROPOSED",
        "source_search_result_id": str(result.id),
        "execution_enabled": False,
        "requires_budget_reservation": True,
        "requires_source_approval_for_collection": True,
    }


def _temporal(evidence: OfficialPageEvidence | None, now: datetime) -> TemporalValidity:
    if evidence is None:
        return TemporalValidity.UNKNOWN
    start, end = evidence.recruitment_start_at, evidence.recruitment_deadline_at
    if start is not None and end is not None and start < end:
        if now < start:
            return TemporalValidity.UPCOMING
        return TemporalValidity.ACTIVE if now < end else TemporalValidity.EXPIRED
    start_day, end_day = evidence.recruitment_start_on, evidence.recruitment_deadline_on
    if start_day is not None and end_day is not None and start_day <= end_day:
        today = now.astimezone(KST).date()
        if today < start_day:
            return TemporalValidity.UPCOMING
        if today > end_day:
            return TemporalValidity.EXPIRED
        # Without an exact opening/closing time, same-day boundaries are unknown.
        if start_day < today < end_day:
            return TemporalValidity.ACTIVE
    today = now.astimezone(KST).date()
    if evidence.event_end_on and today > evidence.event_end_on:
        return TemporalValidity.EVENT_ENDED
    if evidence.event_start_on and evidence.event_end_on and evidence.event_start_on <= today <= evidence.event_end_on:
        return TemporalValidity.EVENT_ONGOING
    return TemporalValidity.UNKNOWN


class SearchQualityEvaluator:
    """Classify leads without fetching a page or changing approval state."""

    def __init__(self, *, news_domains: tuple[str, ...] = (
            "yna.co.kr", "zdnet.co.kr", "koreadaily.com")) -> None:
        self.news_domains = tuple(domain.lower().strip(".") for domain in news_domains)

    def assess(self, result: SearchResult, *, now: datetime,
               evidence: OfficialPageEvidence | None = None,
               seen_event_identities: set[str] | None = None) -> QualityAssessment:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("assessment time must have a timezone")
        if evidence and canonicalize_url(evidence.source_url) != canonicalize_url(
                result.canonical_url or result.original_url):
            raise ValueError("official evidence URL does not match the search result")
        host = (urlsplit(result.canonical_url or result.original_url).hostname or "").lower()
        domain_is_news = any(host == domain or host.endswith("." + domain)
                             for domain in self.news_domains)
        authority = evidence.authority if evidence else (
            Authority.THIRD_PARTY if domain_is_news else Authority.UNKNOWN)
        page_type = evidence.page_type if evidence else (
            PageType.NEWS if domain_is_news else PageType.UNKNOWN)
        official = authority in {Authority.OFFICIAL_INSTITUTION, Authority.OFFICIAL_ORGANIZER}
        individual = page_type is PageType.INDIVIDUAL_NOTICE
        temporal = _temporal(evidence, now) if official else TemporalValidity.UNKNOWN
        duplicate = next((reason for reason in result.filter_reasons if reason in {
            "duplicate_url_in_query", "existing_candidate", "existing_source",
            "existing_opportunity"}), None)
        if not duplicate and evidence and evidence.event_identity and seen_event_identities is not None:
            identity = " ".join(evidence.event_identity.casefold().split())
            if identity in seen_event_identities:
                duplicate = "same_event_other_url"
            else:
                seen_event_identities.add(identity)
        codes = ("official_page_checked",) if evidence else ("search_result_only",)
        uncertainty: tuple[str, ...] = ()
        if not evidence:
            uncertainty = ("original_page_unverified", "recruitment_window_unverified")
        elif not official:
            uncertainty = ("officiality_unverified",)
        elif not individual:
            uncertainty = ("individual_recruitment_unverified",)
        elif temporal is TemporalValidity.UNKNOWN:
            uncertainty = ("recruitment_window_unverified",)
        if duplicate:
            classification = QualityClass.DUPLICATE
        elif page_type is PageType.RECAP:
            classification = QualityClass.EVENT_RECAP
        elif page_type is PageType.NEWS:
            classification = QualityClass.NEWS_ARTICLE
        elif official and page_type is PageType.OFFICIAL_LISTING:
            classification = QualityClass.OFFICIAL_LISTING_PAGE
        elif official and individual and temporal in {
            TemporalValidity.ACTIVE, TemporalValidity.UPCOMING, TemporalValidity.EXPIRED}:
            classification = QualityClass(temporal.value + "_OPPORTUNITY")
        else:
            classification = QualityClass.UNVERIFIED
        verified_new = classification in {QualityClass.ACTIVE_OPPORTUNITY,
                                           QualityClass.UPCOMING_OPPORTUNITY} and not duplicate
        return QualityAssessment(
            classification, authority, page_type, temporal, duplicate,
            "VERIFIED" if evidence and official else "UNVERIFIED", codes, uncertainty,
            official, individual, verified_new,
            "INDIVIDUAL_NOTICE_SEARCH" if classification is QualityClass.OFFICIAL_LISTING_PAGE else None,
            (evidence.recruitment_start_at or evidence.recruitment_start_on).isoformat()
            if evidence and (evidence.recruitment_start_at or evidence.recruitment_start_on) else None,
            (evidence.recruitment_deadline_at or evidence.recruitment_deadline_on).isoformat()
            if evidence and (evidence.recruitment_deadline_at or evidence.recruitment_deadline_on) else None,
            evidence.event_start_on.isoformat() if evidence and evidence.event_start_on else None,
            evidence.event_end_on.isoformat() if evidence and evidence.event_end_on else None,
        )


@dataclass(frozen=True, slots=True)
class SearchQualityMetrics:
    search_result_count: int
    official_source_count: int
    individual_opportunity_count: int
    active_opportunity_count: int
    upcoming_opportunity_count: int
    expired_opportunity_count: int
    listing_page_count: int
    news_article_count: int
    duplicate_count: int
    unverified_count: int
    verified_new_opportunity_count: int
    valid_opportunity_yield: float
    official_source_ratio: float
    duplicate_ratio: float


def quality_metrics(rows: tuple[QualityAssessment, ...]) -> SearchQualityMetrics:
    size = len(rows)
    count = lambda category: sum(row.classification is category for row in rows)
    official = sum(row.official_source for row in rows)
    verified = sum(row.verified_new_opportunity for row in rows)
    duplicate = count(QualityClass.DUPLICATE)
    return SearchQualityMetrics(
        size, official, sum(row.individual_opportunity for row in rows),
        count(QualityClass.ACTIVE_OPPORTUNITY), count(QualityClass.UPCOMING_OPPORTUNITY),
        count(QualityClass.EXPIRED_OPPORTUNITY), count(QualityClass.OFFICIAL_LISTING_PAGE),
        count(QualityClass.NEWS_ARTICLE), duplicate, count(QualityClass.UNVERIFIED),
        verified, verified / size if size else 0.0, official / size if size else 0.0,
        duplicate / size if size else 0.0,
    )
