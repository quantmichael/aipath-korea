"""Deterministic, offline Discovery planning. Planning never calls a provider or writes DB rows.

Plans are proposals, not approved schedules. The existing reviewed schedule and
claim path remains the only way to execute a planned query.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from agent_collect.discovery.budget import SearchBudgetPolicy, normalize_query
from agent_collect.discovery.models import DiscoveryQuery
from agent_collect.discovery.scheduling import DiscoverySchedule
from agent_collect.schemas.models import Vertical


# Each vertical has current-year, next-year and evergreen variants in both
# languages. Values are templates; rendered text is recorded separately.
QUERY_TEMPLATES: dict[Vertical, tuple[str, str, str, str, str, str]] = {
    Vertical.CONFERENCE: (
        "{year} AI conference Korea official registration", "{year} 인공지능 학술대회 참가 모집",
        "{year} AI conference Korea call for papers", "{year} 인공지능 컨퍼런스 논문모집",
        "AI conference Korea official registration", "인공지능 세미나 참가자 모집"),
    Vertical.EXHIBITION: (
        "{year} AI exhibition Korea registration", "{year} 인공지능 전시회 산업전 참가 모집",
        "{year} physical AI expo Korea", "{year} 피지컬 AI 스마트팩토리 박람회",
        "AI exhibition Korea visitor registration", "피지컬 AI 전시회 스마트팩토리 산업전"),
    Vertical.COMPETITION: (
        "{year} AI competition Korea call for entries", "{year} 인공지능 공모전 참가 모집",
        "{year} AI contest Korea registration", "{year} AI 경진대회 참가자 모집",
        "AI competition Korea official application", "인공지능 공모전 공식 모집"),
    Vertical.HACKATHON: (
        "{year} AI hackathon Korea registration", "{year} 인공지능 해커톤 참가 모집",
        "{year} AI hackathon Korea application", "{year} AI 해커톤 공식 참가 신청",
        "AI hackathon Korea official registration", "인공지능 해커톤 참가자 모집"),
    Vertical.EDUCATION: (
        "{year} AI education Korea student recruitment", "{year} 인공지능 교육생 모집",
        "{year} AI bootcamp Korea application", "{year} AI 부트캠프 교육생 모집",
        "AI education Korea official course application", "인공지능 교육생 공식 모집"),
    Vertical.RESEARCH: (
        "{year} AI research program Korea applications", "{year} 인공지능 연구 프로그램 모집",
        "{year} AI researcher Korea recruitment", "{year} AI 연구원 모집 공고",
        "AI research program Korea official application", "인공지능 연구 프로그램 참가자 모집"),
    Vertical.GRANT: (
        "{year} AI government grant Korea application", "{year} 정부 AI 지원사업 공고",
        "{year} AI business support Korea recruitment", "{year} AI 기업 지원사업 모집",
        "AI grant Korea official application", "인공지능 정부 지원사업 공식 모집"),
    Vertical.PROGRAM: (
        "{year} AI accelerator Korea participant recruitment", "{year} AI 액셀러레이팅 프로그램 모집",
        "{year} AI program Korea application", "{year} 인공지능 참가자 모집 공고",
        "AI accelerator Korea official application", "인공지능 프로그램 참가자 모집"),
}


@dataclass(frozen=True, slots=True)
class PlannedQuery:
    category: Vertical
    query_year: int | None
    query_template: str
    query_text: str
    language: str
    country: str
    plan_key: str
    planned_for: datetime
    status: str = "DRY_RUN"

    def __post_init__(self) -> None:
        if self.status != "DRY_RUN" or self.planned_for.tzinfo is None:
            raise ValueError("a new plan must remain timezone-aware and dry-run")


@dataclass(frozen=True, slots=True)
class SearchPlan:
    created_at: datetime
    items: tuple[PlannedQuery, ...]
    candidate_query_count: int
    duplicate_count: int
    cooldown_blocked_count: int
    budget_blocked_count: int
    daily_usage: int
    monthly_usage: int

    def audit_details(self) -> dict[str, object]:
        return {
            "planner_version": "deterministic-v1",
            "created_at": self.created_at.isoformat(),
            "planned_query_count": len(self.items), "executed_query_count": 0,
            "candidate_query_count": self.candidate_query_count,
            "duplicate_count": self.duplicate_count,
            "cooldown_blocked_count": self.cooldown_blocked_count,
            "budget_blocked_count": self.budget_blocked_count,
            "daily_usage": self.daily_usage, "monthly_usage": self.monthly_usage,
            "discovered_count": 0, "candidate_count": 0,
            "items": [{"plan_key": item.plan_key, "category": item.category.value,
                       "query_year": item.query_year,
                       "query_template": item.query_template,
                       "query_text": item.query_text, "status": item.status}
                      for item in self.items],
        }


class AutonomousDiscoveryPlanner:
    """Three balanced proposals per KST day, bounded by the existing Tavily policy.

    Reading the existing schedules and query ledger is the caller's job. A failed
    ledger read must fail closed; passing no ledger is an explicit offline test.
    """

    def __init__(self, *, policy: SearchBudgetPolicy | None = None,
                 daily_plan_limit: int = 3) -> None:
        self.policy = policy or SearchBudgetPolicy()
        if not 1 <= daily_plan_limit <= self.policy.daily_limit:
            raise ValueError("invalid daily planning limit")
        self.daily_plan_limit = daily_plan_limit

    def catalog(self, *, now: datetime) -> tuple[tuple[Vertical, int | None, str, str], ...]:
        """All 48 bilingual variants for the current KST year, next year and evergreen."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        year = now.astimezone(ZoneInfo(self.policy.timezone_name)).year
        return tuple((category, year if index < 2 else year + 1 if index < 4 else None,
                      template, template.format(year=year if index < 2 else year + 1)
                      if index < 4 else template)
                     for category, templates in QUERY_TEMPLATES.items()
                     for index, template in enumerate(templates))

    def screen_proposals(self, *, now: datetime, proposals: tuple[object, ...],
                         existing_schedules: tuple[DiscoverySchedule, ...],
                         recent_queries: tuple[DiscoveryQuery, ...],
                         daily_usage: int, monthly_usage: int) -> tuple[tuple[object, ...], tuple[str, ...]]:
        """Apply the same deterministic budget/cooldown/diversity policy to AI proposals.

        This is only a planning preview. The DB reservation is authoritative at
        execution time, when another worker may have consumed the budget.
        """
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        existing = {normalize_query(row.query_text) for row in existing_schedules}
        cooling = {normalize_query(row.text) for row in recent_queries
                   if row.started_at is not None and
                   row.started_at >= now - timedelta(hours=self.policy.cooldown_hours)}
        available = max(0, min(self.policy.daily_limit - daily_usage,
                               self.policy.monthly_limit - monthly_usage))
        selected: list[object] = []
        blocked: list[str] = []
        categories: set[Vertical] = set()
        for item in proposals:
            key = normalize_query(item.query)
            if key in existing or key in cooling or key in {normalize_query(row.query) for row in selected}:
                blocked.append("DUPLICATE_OR_COOLDOWN")
            elif len(selected) >= available:
                blocked.append("BUDGET")
            elif item.category in categories and item.category is not Vertical.UNKNOWN:
                blocked.append("CATEGORY_DIVERSITY")
            else:
                selected.append(item)
                categories.add(item.category)
        return tuple(selected), tuple(blocked)

    def plan(self, *, now: datetime, existing_schedules: tuple[DiscoverySchedule, ...],
             recent_queries: tuple[DiscoveryQuery, ...]) -> SearchPlan:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        local = now.astimezone(ZoneInfo(self.policy.timezone_name))
        day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = day_start.replace(day=1)
        cooldown_start = now - timedelta(hours=self.policy.cooldown_hours)
        attempted = [q for q in recent_queries if q.provider == "tavily" and q.started_at is not None]
        daily = sum(q.started_at >= day_start.astimezone(timezone.utc) for q in attempted)
        monthly = sum(q.started_at >= month_start.astimezone(timezone.utc) for q in attempted)
        existing = {normalize_query(s.query_text) for s in existing_schedules}
        cooling = {normalize_query(q.text) for q in attempted if q.started_at >= cooldown_start}
        categories = tuple(QUERY_TEMPLATES)
        # Date ordinal rotates category priority. Each default daily plan has
        # one current-year, one next-year and one evergreen query. Over time,
        # every category and both languages receive a turn.
        rotation = ((local.date().toordinal() - 1) * self.daily_plan_limit) % len(categories)
        ordered = categories[rotation:] + categories[:rotation]
        items: list[PlannedQuery] = []
        duplicates = cooldown_blocks = 0
        used_categories: set[Vertical] = set()
        for slot in range(self.daily_plan_limit):
            # The third slot is evergreen; extra slots cycle through the same
            # three temporal buckets without exceeding the configured budget.
            bucket = slot % 3
            for category in ordered:
                if category in used_categories:
                    continue
                templates = QUERY_TEMPLATES[category]
                preferred = (local.date().toordinal() + slot) % 2
                for language_offset in range(2):
                    index = bucket * 2 + (preferred + language_offset) % 2
                    year = local.year if index < 2 else local.year + 1 if index < 4 else None
                    template = templates[index]
                    rendered = template.format(year=year) if year is not None else template
                    key = normalize_query(rendered)
                    if key in existing or any(normalize_query(i.query_text) == key for i in items):
                        duplicates += 1
                        continue
                    if key in cooling:
                        cooldown_blocks += 1
                        continue
                    items.append(PlannedQuery(
                        category, year, template, rendered,
                        "en" if index % 2 == 0 else "ko", "KR",
                        f"{local.date().isoformat()}:{category.value}:{index}", now))
                    used_categories.add(category)
                    break
                if category in used_categories:
                    break
        available = max(0, min(self.policy.daily_limit - daily,
                               self.policy.monthly_limit - monthly))
        budget_blocks = max(0, len(items) - available)
        selected = tuple(items[:available])
        return SearchPlan(now, selected, len(items), duplicates,
                          cooldown_blocks, budget_blocks, daily, monthly)
