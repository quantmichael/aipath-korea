"""Narrow, opt-in approval of privately collectible official Source pages.

An operator must review each scope and pin the document that explicitly permits
collection. Robots permission alone is never treated as copyright permission.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
from uuid import uuid4

from agent_collect.collectors.security import CollectionTransportError, validate_target_url
from agent_collect.collectors.transport import SafeHttpTransport
from agent_collect.discovery.models import (
    EvaluationDecision, SourceCandidate, SourceEvaluation,
)
from agent_collect.discovery.evaluation import AI_PATTERN, VERTICAL_TERMS
from agent_collect.extraction.preprocess import preprocess_html
from agent_collect.registry.approval import SourceApprovalService
from agent_collect.registry.service import canonicalize_url
from agent_collect.schemas.models import Source
from agent_collect.schemas.status import SourceStatus


AUTO_REVIEWER = "AIPATH_AUTO_APPROVAL_V1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ApprovalScope:
    scope_id: str
    origin: str
    path_prefix: str
    reviewed_by: str
    reviewed_at: datetime
    expires_at: datetime
    officiality_evidence_url: str
    officiality_sha256: str
    terms_url: str
    terms_sha256: str
    permission_basis: str

    def __post_init__(self) -> None:
        if (not re.fullmatch(r"[a-z0-9_.-]{1,64}", self.scope_id)
                or not self.reviewed_by.strip() or len(self.reviewed_by) > 120
                or any(ord(char) < 32 for char in self.reviewed_by)
                or not self.officiality_evidence_url.strip()):
            raise ValueError("scope identifier, reviewer and officiality evidence are required")
        if self.permission_basis != "explicit_license":
            raise ValueError("automatic approval requires an explicit collection license")
        if not _SHA256.fullmatch(self.terms_sha256) or not _SHA256.fullmatch(self.officiality_sha256):
            raise ValueError("evidence hashes must be lowercase SHA-256 digests")
        if self.reviewed_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("scope dates must include a timezone")
        if not self.reviewed_at < self.expires_at <= self.reviewed_at + timedelta(days=90):
            raise ValueError("scope validity must be positive and no longer than 90 days")
        parts = urlsplit(self.origin)
        if (parts.scheme != "https" or not parts.hostname or parts.port is not None
                or parts.path or parts.query or parts.fragment or parts.username):
            raise ValueError("scope origin must be an HTTPS host without a path or port")
        validate_target_url(self.origin)
        if (self.path_prefix == "/" or not self.path_prefix.startswith("/")
                or not self.path_prefix.endswith("/") or ".." in self.path_prefix
                or "%" in self.path_prefix or "//" in self.path_prefix):
            raise ValueError("scope path_prefix must be a directory path")
        for url in (self.officiality_evidence_url, self.terms_url):
            validate_target_url(url)
            if (_origin(url) != self.origin or urlsplit(url).scheme != "https"
                    or urlsplit(url).query or urlsplit(url).fragment):
                raise ValueError("scope evidence must remain on the exact official origin")

    def covers(self, url: str) -> bool:
        parts = urlsplit(url)
        return (parts.scheme == "https" and _origin(url) == self.origin
                and parts.path.startswith(self.path_prefix) and not parts.query
                and not parts.fragment and "%" not in parts.path
                and all(segment not in {".", ".."} for segment in parts.path.split("/")))


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc.lower()}"


def load_approval_scopes(raw: str) -> tuple[ApprovalScope, ...]:
    data = json.loads(raw)
    if not isinstance(data, list) or not data:
        raise ValueError("enabled auto approval requires nonempty reviewed scopes")
    scopes = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("approval scope must be an object")
        scopes.append(ApprovalScope(
            scope_id=item["scope_id"], origin=item["origin"],
            path_prefix=item["path_prefix"], reviewed_by=item["reviewed_by"],
            reviewed_at=datetime.fromisoformat(item["reviewed_at"].replace("Z", "+00:00")),
            expires_at=datetime.fromisoformat(item["expires_at"].replace("Z", "+00:00")),
            officiality_evidence_url=item["officiality_evidence_url"],
            officiality_sha256=item["officiality_sha256"],
            terms_url=item["terms_url"], terms_sha256=item["terms_sha256"],
            permission_basis=item["permission_basis"],
        ))
    if len({scope.scope_id for scope in scopes}) != len(scopes):
        raise ValueError("approval scope ids must be unique")
    return tuple(scopes)


@dataclass(frozen=True, slots=True)
class AutoApprovalAssessment:
    eligible: bool
    reason: str
    candidate_url: str | None = None
    scope_id: str | None = None
    reviewed_by: str | None = None
    officiality_evidence_url: str | None = None
    officiality_sha256: str | None = None
    terms_url: str | None = None
    terms_sha256: str | None = None
    checked_at: str | None = None

    def provenance(self) -> dict[str, object]:
        return {"eligible": self.eligible, "reason": self.reason,
                "candidate_url": self.candidate_url,
                "scope_id": self.scope_id, "reviewed_by": self.reviewed_by,
                "officiality_evidence_url": self.officiality_evidence_url,
                "officiality_sha256": self.officiality_sha256,
                "terms_url": self.terms_url, "terms_sha256": self.terms_sha256,
                "checked_at": self.checked_at, "policy_version": "official-scope-v1"}


class AutoApprovalAssessor:
    def __init__(self, scopes: tuple[ApprovalScope, ...], *, user_agent: str,
                 transport: SafeHttpTransport | None = None,
                 clock=None) -> None:
        if not scopes or not user_agent.strip():
            raise ValueError("scopes and user agent are required")
        self.scopes = scopes
        self.user_agent = user_agent
        self.transport = transport or SafeHttpTransport()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def assess(self, candidate: SourceCandidate,
               evaluation: SourceEvaluation) -> AutoApprovalAssessment:
        now = self.clock()
        def deny(reason: str) -> AutoApprovalAssessment:
            return AutoApprovalAssessment(False, reason, checked_at=now.isoformat())

        if evaluation.candidate_id != candidate.id or not {"ai_related", "vertical_related"}.issubset(evaluation.reasons):
            return deny("relevance_unconfirmed")
        try:
            validate_target_url(candidate.candidate_url)
            if canonicalize_url(candidate.candidate_url) != candidate.canonical_url:
                return deny("candidate_identity_mismatch")
            matching = [scope for scope in self.scopes if scope.covers(candidate.candidate_url)]
            if len(matching) != 1:
                return deny("scope_not_unique_or_not_reviewed")
            scope = matching[0]
            if not scope.reviewed_at <= now < scope.expires_at:
                return deny("scope_expired_or_not_yet_valid")
            robots_url = scope.origin + "/robots.txt"
            robots = self.transport.fetch(
                robots_url, headers={"User-Agent": self.user_agent}, timeout=10,
                max_bytes=32768, max_redirects=0)
            if robots.status != 200 or robots.final_url != robots_url or not robots.content:
                return deny("robots_unavailable")
            if b"user-agent:" not in robots.content.lower() or robots.content.lstrip().startswith(b"<"):
                return deny("robots_invalid")
            parser = RobotFileParser()
            parser.parse(robots.content.decode("utf-8").splitlines())
            if not all(parser.can_fetch(self.user_agent, url) for url in (
                    candidate.candidate_url, scope.officiality_evidence_url, scope.terms_url)):
                return deny("robots_disallow")
            officiality = self.transport.fetch(
                scope.officiality_evidence_url,
                headers={"User-Agent": self.user_agent}, timeout=10,
                max_bytes=262144, max_redirects=0)
            if (officiality.status != 200 or officiality.final_url != scope.officiality_evidence_url
                    or hashlib.sha256(officiality.content).hexdigest() != scope.officiality_sha256):
                return deny("officiality_evidence_unavailable_or_changed")
            terms = self.transport.fetch(
                scope.terms_url, headers={"User-Agent": self.user_agent}, timeout=10,
                max_bytes=262144, max_redirects=0)
            if (terms.status != 200 or terms.final_url != scope.terms_url or
                    hashlib.sha256(terms.content).hexdigest() != scope.terms_sha256):
                return deny("terms_unavailable_or_changed")
            page = self.transport.fetch(
                candidate.candidate_url, headers={"User-Agent": self.user_agent}, timeout=10,
                max_bytes=1048576, max_redirects=0)
            if (page.status != 200 or page.final_url != candidate.candidate_url or
                    not page.content or not page.headers.get("content-type", "").lower().startswith("text/html")):
                return deny("candidate_page_unavailable")
            visible = preprocess_html(page.content, source_url=candidate.candidate_url,
                                      max_input_chars=12000).full_text.lower()
            if not AI_PATTERN.search(visible) or not any(term in visible for term in VERTICAL_TERMS):
                return deny("page_relevance_unconfirmed")
        except (CollectionTransportError, UnicodeError, ValueError, OSError):
            return deny("permission_check_failed")
        return AutoApprovalAssessment(
            True, "reviewed_official_scope_robots_and_terms_match", candidate.candidate_url,
            scope.scope_id, scope.reviewed_by, scope.officiality_evidence_url,
            scope.officiality_sha256, scope.terms_url, scope.terms_sha256,
            now.isoformat())

    def revalidate_source(self, source: Source) -> bool:
        """Block later collection when the original scope or live permission changes."""
        recorded = source.provenance.get("auto_approval") or {}
        if not recorded.get("eligible") or source.url != recorded.get("candidate_url"):
            return False
        candidate = SourceCandidate(
            id=uuid4(), query_id=uuid4(), search_result_id=uuid4(),
            original_url=source.url, candidate_url=source.url,
            canonical_url=source.canonical_url or "", domain=urlsplit(source.url).hostname or "",
            title=source.name, snippet="", discovered_at=source.discovered_at,
        )
        evaluation = SourceEvaluation(
            candidate_id=candidate.id, decision=EvaluationDecision.REVIEW,
            confidence=0.55, reasons=("ai_related", "vertical_related"),
            evidence=(f"url:{source.url}",), evaluated_at=self.clock(),
        )
        checked = self.assess(candidate, evaluation)
        return bool(checked.eligible and checked.scope_id == recorded.get("scope_id")
                    and checked.terms_sha256 == recorded.get("terms_sha256")
                    and checked.officiality_sha256 == recorded.get("officiality_sha256")
                    and checked.reviewed_by == recorded.get("reviewed_by"))


class AutoApprovalExecutor:
    """Use the existing audited RPC, with a machine reviewer distinct from a human."""

    def __init__(self, review: SourceApprovalService,
                 schedule_verifier: Callable[[Source], bool] | None = None) -> None:
        self.review = review
        self.schedule_verifier = schedule_verifier

    def activate(self, source: Source, assessment: AutoApprovalAssessment) -> Source:
        recorded = source.provenance.get("auto_approval") or {}
        if (not assessment.eligible or source.status is not SourceStatus.CANDIDATE
                or source.url != assessment.candidate_url
                or recorded.get("eligible") is not True
                or recorded.get("scope_id") != assessment.scope_id
                or recorded.get("officiality_sha256") != assessment.officiality_sha256
                or recorded.get("terms_sha256") != assessment.terms_sha256):
            raise ValueError("only a new, eligible Source can be auto approved")
        reason = (f"auto policy official-scope-v1 scope={assessment.scope_id}; "
                  f"scope reviewed_by={assessment.reviewed_by}; "
                  f"officiality evidence sha256={assessment.officiality_sha256}; "
                  f"robots allowed; explicit license pinned at {assessment.terms_url} "
                  f"sha256={assessment.terms_sha256}; private collection only")
        began_here = activated_here = False
        try:
            self.review.begin_review(source.id, reviewer=AUTO_REVIEWER, reason=reason)
            began_here = True
            self.review.approve(source.id, reviewer=AUTO_REVIEWER, reason=reason,
                                officiality_confirmed=True, collection_allowed=True)
            active = self.review.activate(source.id, reviewer=AUTO_REVIEWER, reason=reason)
            activated_here = True
            if self.schedule_verifier is not None and not self.schedule_verifier(active):
                raise RuntimeError("collection_schedule_not_confirmed")
            return active
        except Exception:
            current = self.review.repository.get_source(source.id)
            review = (current.provenance.get("source_review") or {}) if current else {}
            approval = review.get("approval") or {}
            owned_by_auto = approval.get("reviewer") == AUTO_REVIEWER
            if (not activated_here and current and current.status is SourceStatus.ACTIVE
                    and owned_by_auto and current.url == assessment.candidate_url):
                try:
                    confirmed = (self.schedule_verifier is None
                                 or self.schedule_verifier(current))
                except Exception:
                    confirmed = False
                if confirmed:
                    # Another worker completed the same reviewed approval first.
                    return current
                self.review.hold(source.id, reviewer=AUTO_REVIEWER,
                                 reason="automatic approval schedule could not be confirmed")
                raise RuntimeError("collection_schedule_not_confirmed")
            if current and current.status is SourceStatus.REVIEW:
                events = self.review.repository.list_events(source.id)
                owned_by_auto = bool(events and events[-1].command.reviewer == AUTO_REVIEWER)
            if began_here and current and owned_by_auto and current.status in {
                    SourceStatus.REVIEW, SourceStatus.APPROVED, SourceStatus.ACTIVE}:
                self.review.hold(source.id, reviewer=AUTO_REVIEWER,
                                 reason="automatic approval failed; held for manual review")
            raise
