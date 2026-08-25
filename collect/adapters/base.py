from __future__ import annotations

from urllib.parse import urlparse

from collect.models import OpportunityCandidate


AI_KEYWORDS = (
    " ai ",
    "ai ",
    " ai",
    "artificial intelligence",
    "인공지능",
    "생성형",
    "llm",
    "머신러닝",
    "딥러닝",
    "데이터",
    "data",
    "digital",
    "디지털",
    "dx",
    "ax",
)

OPPORTUNITY_KEYWORDS = (
    "해커톤",
    "공모전",
    "교육",
    "콘퍼런스",
    "컨퍼런스",
    "밋업",
    "웨비나",
    "세미나",
    "경진대회",
    "창업경진대회",
    "모집",
    "공고",
    "지원",
)

EXCLUDED_TITLE_PARTS = (
    "바로가기",
    "건너뛰기",
    "메뉴",
    "푸터",
    "콘텐츠",
    "본문",
    "스크랩",
    "처음페이지",
    "이전페이지",
    "다음페이지",
    "마지막페이지",
)

EXCLUDED_URL_PARTS = (
    "/login",
    "/join",
    "/signup",
    "/privacy",
    "/terms",
    "/policy",
    "/mypage",
    "/search",
    "/contact",
    "/about",
)

EXCLUDED_TITLES = (
    "로그인",
    "회원가입",
    "검색",
    "개인정보",
    "이용약관",
    "문의",
    "소개",
    "전체보기",
    "더보기",
    "ai-hub",
    "ai 데이터찾기",
    "모집중",
    "모집마감",
    "중소벤처기업부",
    "home",
    "login",
    "signup",
)


class HtmlSourceAdapter:
    source_name: str | None = None
    detail_url_parts: tuple[str, ...] = ()
    list_url_parts: tuple[str, ...] = ()

    def accepts(self, *, title: str, url: str) -> bool:
        if self.is_excluded_link(title, url):
            return False

        if not self.is_detail_url(url):
            return False

        return self.has_relevant_keyword(f"{title} {url}".lower())

    def clean_title(self, title: str) -> str:
        return " ".join(title.split())

    def enrich_candidate(
        self,
        candidate: OpportunityCandidate,
        *,
        timeout: int = 15,
    ) -> OpportunityCandidate:
        return candidate

    def is_detail_url(self, url: str) -> bool:
        normalized_url = url.lower()

        if self.detail_url_parts:
            return any(part.lower() in normalized_url for part in self.detail_url_parts)

        if any(part.lower() in normalized_url for part in self.list_url_parts):
            return False

        return True

    def is_excluded_link(self, title: str, url: str) -> bool:
        normalized_title = title.strip().lower()
        normalized_url = url.lower()
        parsed = urlparse(url)

        if len(normalized_title) < 4:
            return True

        if parsed.fragment:
            return True

        if normalized_title in EXCLUDED_TITLES:
            return True

        if any(part in normalized_title for part in EXCLUDED_TITLE_PARTS):
            return True

        if any(part in normalized_url for part in EXCLUDED_URL_PARTS):
            return True

        if any(
            normalized_url.endswith(extension)
            for extension in (".jpg", ".jpeg", ".png", ".gif", ".pdf", ".zip")
        ):
            return True

        return False

    def has_relevant_keyword(self, haystack: str) -> bool:
        has_ai_signal = any(keyword.lower() in haystack for keyword in AI_KEYWORDS)
        has_opportunity_signal = any(
            keyword.lower() in haystack for keyword in OPPORTUNITY_KEYWORDS
        )
        return has_ai_signal and has_opportunity_signal
