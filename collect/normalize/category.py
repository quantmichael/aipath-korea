from __future__ import annotations

from collect.models import OpportunityCandidate


CATEGORY_KEYWORDS = {
    "education": (
        "교육",
        "강의",
        "부트캠프",
        "과정",
        "수강",
        "캠프",
        "교육생",
        "아카데미",
        "class",
        "course",
    ),
    "competition": ("공모전", "경진대회", "대회", "contest"),
    "hackathon": ("해커톤", "hackathon"),
    "meetup": ("밋업", "meetup", "네트워킹", "모임"),
    "conference": ("콘퍼런스", "컨퍼런스", "conference", "세미나", "포럼"),
    "webinar": ("웨비나", "webinar", "온라인 설명회"),
    "growth-program": (
        "멘토링",
        "창업",
        "성장",
        "취업",
        "커리어",
        "프로그램",
        "지원사업",
        "지원",
        "참가자",
        "모집",
        "공고",
        "수정 공고",
        "활용 지원",
        "파운데이션 모델",
        "사업화",
        "사업",
    ),
}

def infer_category_slug(candidate: OpportunityCandidate) -> str | None:
    text = " ".join(
        value
        for value in [
            candidate.title,
            candidate.summary,
            candidate.description,
            candidate.official_url,
        ]
        if value
    ).lower()

    for slug, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword.lower() in text for keyword in keywords):
            return slug

    return None
