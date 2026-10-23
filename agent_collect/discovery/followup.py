"""Bounded, read-only follow-up of a reviewed official search result.

This is a Discovery lead finder, not Collection or official evidence verification.
It never stores page bodies, registers Sources, or marks a lead publishable.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Callable
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo

from agent_collect.collectors.security import CollectionTransportError, validate_target_url
from agent_collect.collectors.transport import SafeHttpTransport
from agent_collect.discovery.models import SearchResult
from agent_collect.extraction.preprocess import preprocess_html
from agent_collect.extraction.validation import _dates_in_text, parse_explicit_datetime
from agent_collect.registry.service import canonicalize_url


if TYPE_CHECKING:
    from agent_collect.discovery.auto_approval import ApprovalScope


_LINK = re.compile(
    r"모집|신청|접수|사전등록|참가|논문|공고|지원사업|"
    r"apply|application|register|registration|pre.?register|"
    r"call for (?:papers|participation|proposals)|competition|hackathon|grant|recruit",
    re.I,
)
_INDIVIDUAL_LINK = re.compile(
    r"모집|신청|접수|사전등록|논문|공고|지원사업|"
    r"apply|application|register|registration|pre.?register|"
    r"call for (?:papers|participation|proposals)|competition|hackathon|grant|recruit",
    re.I,
)
_WINDOW = re.compile(
    r"모집\s*기간|신청\s*기간|접수\s*기간|등록\s*기간|사전등록\s*기간|"
    r"registration\s*(?:period|window)|application\s*(?:period|window)|"
    r"submission\s*(?:period|window)", re.I,
)
_EXACT = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})")
_NEXT_PHASE = re.compile(r"^[-–—]?\s*[2-9]\d*차\b")
_EVENT_WINDOW = re.compile(r"행사\s*기간|개최\s*기간|event\s*dates?|exhibition\s*dates?", re.I)
_RESTRICTED = re.compile(
    r"login|signin|sign-in|signup|sign-up|account|my-page|mypage|"
    r"captcha|admin|로그인|회원가입|마이페이지|캡차|본인인증", re.I)
_REDIRECT = frozenset({301, 302, 303, 307, 308})
_KST = ZoneInfo("Asia/Seoul")


def _request_url(url: str) -> str:
    """Encode internationalized paths without changing the checked authority."""
    parts = urlsplit(url)
    path = quote(parts.path, safe="/%:@")
    query = quote(parts.query, safe="=&?+;%:@/")
    path = re.sub(r"%[0-9a-fA-F]{2}", lambda item: item.group().upper(), path)
    query = re.sub(r"%[0-9a-fA-F]{2}", lambda item: item.group().upper(), query)
    return urlunsplit((parts.scheme, parts.netloc, path, query, ""))


@dataclass(frozen=True, slots=True)
class ReviewedOfficialRoot:
    """Operator-reviewed origin; host identity alone does not prove officiality."""

    origin: str
    reviewed_by: str
    relationship: str
    path_prefix: str = "/"

    def __post_init__(self) -> None:
        scheme, host, port, _ = validate_target_url(self.origin)
        parts = urlsplit(self.origin)
        if (scheme != "https" or port != 443 or parts.path or parts.query or parts.fragment
                or not self.reviewed_by.strip() or not self.relationship.strip()
                or self.origin != f"https://{host}"
                or not self.path_prefix.startswith("/") or
                (self.path_prefix != "/" and not self.path_prefix.endswith("/")) or
                ".." in self.path_prefix or "%" in self.path_prefix or
                "//" in self.path_prefix):
            raise ValueError("an exact reviewed HTTPS official origin is required")

    def covers(self, url: str) -> bool:
        parts = urlsplit(url)
        return (parts.scheme == "https" and
                f"https://{parts.hostname}" == self.origin and
                not re.search(r"%(?:2e|2f|5c)", parts.path, re.I) and
                all(segment not in {".", ".."} for segment in parts.path.split("/")) and
                (self.path_prefix == "/" or
                 parts.path == self.path_prefix.rstrip("/") or
                 parts.path.startswith(self.path_prefix)))


@dataclass(frozen=True, slots=True)
class FollowupLead:
    url: str
    parent_url: str
    depth: int
    link_text: str
    page_type: str
    recruitment_start: str | None
    recruitment_deadline: str | None
    time_precision: str
    temporal: str
    discovery_lead_days: int | None
    review_reason: str
    event_start: str | None = None
    event_end: str | None = None
    event_lead_days: int | None = None


@dataclass(frozen=True, slots=True)
class FollowupOutcome:
    root_result_id: str
    visited_urls: tuple[str, ...]
    leads: tuple[FollowupLead, ...]
    blocked: tuple[str, ...]

    def audit_record(self) -> dict[str, object]:
        """Bounded aggregate feedback only; no page body or untrusted link text."""
        recruitment_leads = [row.discovery_lead_days for row in self.leads
                             if row.discovery_lead_days is not None]
        event_leads = [row.event_lead_days for row in self.leads
                       if row.event_lead_days is not None]
        return {"followup_visited": max(0, len(self.visited_urls) - 1),
                "followup_leads": len(self.leads),
                "followup_individual": sum(row.page_type == "INDIVIDUAL_NOTICE"
                                           for row in self.leads),
                "followup_window_found": sum(row.recruitment_start is not None and
                                             row.recruitment_deadline is not None
                                             for row in self.leads),
                "followup_blocked": len(self.blocked),
                "followup_verified": 0,
                "discovery_lead_days": round(sum(recruitment_leads) / len(recruitment_leads))
                if recruitment_leads else None,
                "event_lead_days": round(sum(event_leads) / len(event_leads))
                if event_leads else None}


def _event_window(blocks: tuple, *, discovered_at: datetime) -> tuple[str | None, str | None, int | None]:
    for block in blocks:
        if not _EVENT_WINDOW.search(block.text):
            continue
        days = sorted(_dates_in_text(block.text))
        if len(days) == 2 and days[0] <= days[1]:
            return days[0], days[1], (date.fromisoformat(days[0]) -
                                      discovered_at.astimezone(_KST).date()).days
    return None, None, None


def _window(blocks: tuple, *, discovered_at: datetime, now: datetime) -> tuple[
        str | None, str | None, str, str, int | None]:
    """Use a labelled window and contiguous numbered phases, never event dates."""
    for index, block in enumerate(blocks):
        line = block.text
        if not _WINDOW.search(line):
            continue
        exact = [parse_explicit_datetime(item.group()) for item in _EXACT.finditer(line)]
        if len(exact) == 2 and all(exact) and exact[0] < exact[1]:
            start, end = exact
            temporal = "UPCOMING" if now < start else "ACTIVE" if now < end else "EXPIRED"
            return (start.isoformat(), end.isoformat(), "EXACT_TIME", temporal,
                    (end.astimezone(_KST).date() - discovered_at.astimezone(_KST).date()).days)
        dates = sorted(_dates_in_text(line))
        if len(dates) == 2 and dates[0] <= dates[1]:
            from datetime import date
            phases = [(date.fromisoformat(dates[0]), date.fromisoformat(dates[1]))]
            for following in blocks[index + 1:]:
                if not _NEXT_PHASE.search(following.text):
                    break
                pair = sorted(_dates_in_text(following.text))
                if len(pair) != 2 or pair[0] > pair[1]:
                    return None, None, "UNKNOWN", "UNKNOWN", None
                phases.append((date.fromisoformat(pair[0]), date.fromisoformat(pair[1])))
            first, last = phases[0][0], phases[-1][1]
            if any(right[0] < left[0] or right[1] < left[1]
                   for left, right in zip(phases, phases[1:])):
                return None, None, "UNKNOWN", "UNKNOWN", None
            today = now.astimezone(_KST).date()
            temporal = ("UPCOMING" if today < first else "EXPIRED" if today > last
                        else "ACTIVE" if any(start < today < end for start, end in phases)
                        else "UNKNOWN")
            return first.isoformat(), last.isoformat(), "DATE_ONLY", temporal, (
                last - discovered_at.astimezone(_KST).date()).days
    return None, None, "UNKNOWN", "UNKNOWN", None


class OfficialFollowupExplorer:
    """Use Collection's DNS-pinned transport, with robots checked before each GET.

    External origins and redirects are denied. A separately reviewed official
    relationship can be added in a future policy revision; none is inferred.
    """

    def __init__(self, *, transport: SafeHttpTransport | None = None,
                 user_agent: str = "AIPATH-AgentCollect/1.0") -> None:
        self.transport = transport or SafeHttpTransport()
        self.user_agent = user_agent
        self._robots: dict[str, RobotFileParser | None] = {}

    def _fetch(self, url: str, reviewed: ReviewedOfficialRoot, *,
               request_policy: Callable[[str], bool] | None = None):
        current = _request_url(url)
        redirects: set[str] = set()
        for _ in range(4):
            scheme, host, port, _ = validate_target_url(current)
            parts = urlsplit(current)
            if (scheme != "https" or port != 443 or not reviewed.covers(current)
                    or _RESTRICTED.search(unquote(parts.path))
                    or (request_policy is not None and not request_policy(current))):
                raise CollectionTransportError("outside_reviewed_origin", "unreviewed or restricted target")
            if current in redirects:
                raise CollectionTransportError("redirect_loop", "redirect loop")
            redirects.add(current)
            if reviewed.origin not in self._robots:
                robots_url = reviewed.origin + "/robots.txt"
                response = self.transport.fetch(robots_url, headers={"User-Agent": self.user_agent},
                                                timeout=10, max_bytes=32768, max_redirects=0,
                                                return_redirect_response=True)
                parser = None
                if (response.status == 200 and response.final_url == robots_url
                        and b"user-agent:" in response.content.lower()
                        and not response.content.lstrip().startswith(b"<")):
                    parser = RobotFileParser()
                    parser.parse(response.content.decode("utf-8").splitlines())
                self._robots[reviewed.origin] = parser
            parser = self._robots[reviewed.origin]
            if parser is None or not parser.can_fetch(self.user_agent, current):
                raise CollectionTransportError("robots_blocked", "robots missing or disallows target")
            response = self.transport.fetch(current, headers={"User-Agent": self.user_agent},
                                            timeout=10, max_bytes=1048576, max_redirects=0,
                                            return_redirect_response=True)
            if response.status in _REDIRECT:
                location = response.headers.get("location")
                if not location:
                    raise CollectionTransportError("invalid_redirect", "missing Location")
                current = _request_url(urljoin(current, location))
                continue
            if (response.status != 200 or not response.content or
                    not response.headers.get("content-type", "").lower().startswith("text/html") or
                    "noindex" in response.headers.get("x-robots-tag", "").lower()):
                raise CollectionTransportError("page_inaccessible", "not a public HTML page")
            return response
        raise CollectionTransportError("too_many_redirects", "redirect limit exceeded")

    def investigate(self, result: SearchResult, *, scope: "ApprovalScope", now: datetime) -> FollowupOutcome:
        """Read-only pre-approval research under an existing reviewed license scope.

        This never approves a Source or constitutes publication evidence.
        """
        from agent_collect.discovery.auto_approval import ApprovalScope
        def blocked(code):
            return FollowupOutcome(str(result.id), (), (), (code,))
        url = result.original_url
        if (not isinstance(scope, ApprovalScope) or now.tzinfo is None or
                not scope.reviewed_at <= now < scope.expires_at or not scope.covers(url) or
                canonicalize_url(result.canonical_url or url) != canonicalize_url(url)):
            return blocked("investigation_scope_invalid")
        reviewed = ReviewedOfficialRoot(scope.origin, scope.reviewed_by,
                                        "reviewed explicit license", scope.path_prefix)
        evidence_root = ReviewedOfficialRoot(scope.origin, scope.reviewed_by,
                                             "reviewed permission evidence")
        def public_page(target: str) -> bool:
            path = unquote(urlsplit(target).path).lower()
            return not re.search(
                r"(?:download|attachment|/files?/|\.(?:pdf|hwp|hwpx|docx?|xlsx?|pptx?|zip|jpe?g|png|gif)$)", path)

        self._robots.clear()
        try:
            for evidence_url, digest in ((scope.officiality_evidence_url, scope.officiality_sha256),
                                         (scope.terms_url, scope.terms_sha256)):
                response = self._fetch(evidence_url, evidence_root, request_policy=public_page)
                if (response.final_url != evidence_url or
                        hashlib.sha256(response.content).hexdigest() != digest):
                    return blocked("investigation_evidence_changed")
            def permitted_page(target: str) -> bool:
                return scope.covers(target) and public_page(target)
            return self.explore(result, reviewed=reviewed, now=now, max_depth=1,
                                max_links=2, request_policy=permitted_page)
        except (CollectionTransportError, ValueError, UnicodeError, OSError):
            return blocked("investigation_access_blocked")

    def explore(self, result: SearchResult, *, reviewed: ReviewedOfficialRoot,
                now: datetime, max_depth: int = 2, max_links: int = 10,
                request_policy: Callable[[str], bool] | None = None) -> FollowupOutcome:
        if now.tzinfo is None or result.discovered_at.tzinfo is None:
            raise ValueError("aware times required")
        if not 1 <= max_depth <= 2 or not 1 <= max_links <= 10:
            raise ValueError("follow-up bounds cannot exceed depth 2 or ten links")
        root = _request_url(result.canonical_url or result.original_url)
        if f"https://{urlsplit(root).hostname}" != reviewed.origin:
            raise ValueError("search result outside reviewed official origin")
        # Re-read robots for each bounded exploration, including a long-lived runner.
        self._robots.clear()
        queue = [(root, 0, "", "")]
        seen = {canonicalize_url(root)}
        visited: list[str] = []
        leads: list[FollowupLead] = []
        blocked: list[str] = []
        while queue:
            url, depth, parent, label = queue.pop(0)
            try:
                response = self._fetch(url, reviewed, request_policy=request_policy)
            except (CollectionTransportError, ValueError, UnicodeError) as error:
                blocked.append(getattr(error, "code", "invalid_target"))
                continue
            visited.append(canonicalize_url(response.final_url))
            prepared = preprocess_html(response.content, source_url=response.final_url,
                                       max_input_chars=12000)
            if depth:
                individual = bool(_INDIVIDUAL_LINK.search(label) and
                                  _INDIVIDUAL_LINK.search(prepared.full_text))
                start, end, precision, temporal, lead_days = _window(
                    prepared.evidence_blocks, discovered_at=result.discovered_at, now=now)
                event_start, event_end, event_lead = _event_window(
                    prepared.evidence_blocks, discovered_at=result.discovered_at)
                leads.append(FollowupLead(
                    canonicalize_url(response.final_url), parent, depth, label[:120],
                    "INDIVIDUAL_NOTICE" if individual else "REVIEW_REQUIRED",
                    start if individual else None, end if individual else None,
                    precision if individual else "UNKNOWN", temporal if individual else "UNKNOWN",
                    lead_days if individual else None,
                    "requires_source_approval_and_official_evidence_pipeline",
                    event_start if individual else None,
                    event_end if individual else None,
                    event_lead if individual else None))
            if depth >= max_depth:
                continue
            eligible = []
            for link in prepared.link_evidence:
                if link.link_id == "L000" or not _LINK.search(link.text + " " + link.href):
                    continue
                try:
                    target = canonicalize_url(_request_url(link.href))
                    scheme, host, port, _ = validate_target_url(target)
                    if (scheme != "https" or port != 443 or
                            not reviewed.covers(target) or
                            _RESTRICTED.search(unquote(urlsplit(target).path)) or target in seen):
                        continue
                except (CollectionTransportError, ValueError, UnicodeError):
                    continue
                eligible.append((target, link.text))
            for target, text in eligible:
                if len(seen) - 1 >= max_links:
                    break
                if target not in seen:
                    seen.add(target)
                    queue.append((target, depth + 1, canonicalize_url(response.final_url), text))
        return FollowupOutcome(str(result.id), tuple(visited), tuple(leads), tuple(blocked))
