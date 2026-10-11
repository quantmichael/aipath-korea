"""Explicit, replaceable source evaluation. Rules are only a preliminary signal."""

from datetime import datetime, timezone
import re
from typing import Protocol

from agent_collect.discovery.models import (
    EvaluationDecision, SourceCandidate, SourceEvaluation,
)
from agent_collect.schemas.models import CollectionMethod


class SourceEvaluator(Protocol):
    def evaluate(self, candidate: SourceCandidate) -> SourceEvaluation: ...


AI_PATTERN = re.compile(r"(?<![a-z])(?:ai|ml)(?![a-z])|artificial intelligence|machine learning|인공지능")
VERTICAL_TERMS = (
    "conference", "symposium", "call for papers", "competition", "contest",
    "hackathon", "education", "course", "training", "fellowship",
    "research program", "research programme", "research grant", "funding",
    "government grant", "call for proposals", "recruitment", "call for applications",
    "meetup", "컨퍼런스", "학회", "논문모집", "공모전", "경진대회", "대회",
    "해커톤", "교육", "강좌", "연수", "연구 프로그램", "연구과제",
    "연구지원", "지원사업", "정부지원", "모집공고", "참가자 모집", "밋업",
)


class RuleBasedEvaluator:
    def __init__(self, *, official_domain_hints: tuple[str, ...] = ()) -> None:
        self.official_domain_hints = tuple(domain.lower().strip(".") for domain in official_domain_hints)

    def evaluate(self, candidate: SourceCandidate) -> SourceEvaluation:
        text = f"{candidate.title} {candidate.snippet} {candidate.canonical_url}".lower()
        ai_match = bool(AI_PATTERN.search(text))
        vertical_match = any(term in text for term in VERTICAL_TERMS)
        official_hint = any(
            candidate.domain == hint or candidate.domain.endswith("." + hint)
            for hint in self.official_domain_hints if hint
        )
        reasons = tuple(
            ("ai_related",) if ai_match else ("ai_relevance_unknown",)
        ) + tuple(("vertical_related",) if vertical_match else ("vertical_unknown",)) + tuple(
            ("configured_official_domain_hint",) if official_hint else ("officiality_unconfirmed",)
        )
        evidence = (f"url:{candidate.canonical_url}", f"title:{candidate.title}")
        if not ai_match and not vertical_match:
            decision, confidence = EvaluationDecision.REJECT, 0.2
        elif ai_match and vertical_match and official_hint:
            decision, confidence = EvaluationDecision.ACCEPT, 0.9
        else:
            decision, confidence = EvaluationDecision.REVIEW, 0.55
        return SourceEvaluation(
            candidate_id=candidate.id, decision=decision, confidence=confidence,
            reasons=reasons, evidence=evidence,
            evaluated_at=datetime.now(timezone.utc),
            suggested_method=CollectionMethod.HTTP_HTML,
        )
