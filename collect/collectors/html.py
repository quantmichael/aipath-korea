from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from collect.models import OpportunityCandidate, Source

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

SOURCE_DETAIL_URL_PARTS = {
    "AI Hub": (
        "/aihubnews/bsnspblanc/view.do",
        "/aihubnews/bsnspblanc/detail.do",
    ),
    "정보통신산업진흥원(NIPA)": (
        "/home/2-2/",
    ),
    "DACON": (
        "/competitions/official/",
        "/competitions/open/",
    ),
    "기업마당": (
        "/selectSIIA200Detail.do",
    ),
}

SOURCE_LIST_URL_PARTS = {
    "AI Hub": (
        "/aihubnews/bsnspblanc/list.do",
    ),
    "정보통신산업진흥원(NIPA)": (
        "/home/2-2",
    ),
    "DACON": (
        "/competitions",
    ),
    "기업마당": (
        "/selectSIIA200View.do",
        "/S1T122C128/AS/74/list.do",
    ),
}

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


class LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag != "a":
            return

        attributes = dict(attrs)
        href = attributes.get("href")
        if href:
            self._current_href = href
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._current_href:
            return

        text = " ".join(" ".join(self._current_text).split())
        if text:
            self.links.append(
                {
                    "href": self._current_href,
                    "text": text,
                }
            )

        self._current_href = None
        self._current_text = []


def collect_html_source(
    source: Source,
    *,
    max_candidates: int,
    timeout: int = 15,
) -> list[OpportunityCandidate]:
    if not source.collection_url:
        return []

    request = Request(
        source.collection_url,
        headers={
            "User-Agent": (
                "AIPathKoreaCollector/0.1 "
                "(contact: michaelis@naver.com)"
            ),
        },
    )

    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        html = response.read().decode(charset, errors="replace")

    parser = LinkExtractor()
    parser.feed(html)

    seen_urls: set[str] = set()
    candidates: list[OpportunityCandidate] = []

    for link in parser.links:
        title = link["text"].strip()
        official_url = urljoin(source.collection_url, link["href"])

        if not _is_http_url(official_url):
            continue

        if _is_excluded_link(title, official_url):
            continue

        if not _is_source_detail_url(source, official_url):
            continue

        haystack = f"{title} {official_url}".lower()
        if not _has_relevant_keyword(haystack):
            continue

        if official_url in seen_urls:
            continue

        seen_urls.add(official_url)
        candidates.append(
            OpportunityCandidate(
                source_id=source.id,
                external_id=official_url,
                title=title[:300],
                official_url=official_url,
                candidate_status="pending",
                raw_payload={
                    "collector": "html",
                    "source_name": source.name,
                    "collection_url": source.collection_url,
                    "anchor_text": title,
                    "href": link["href"],
                },
            )
        )

        if len(candidates) >= max_candidates:
            break

    return candidates


def _is_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_excluded_link(title: str, url: str) -> bool:
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


def _is_source_detail_url(source: Source, url: str) -> bool:
    normalized_url = url.lower()
    allowed_parts = SOURCE_DETAIL_URL_PARTS.get(source.name)
    list_parts = SOURCE_LIST_URL_PARTS.get(source.name, ())

    if allowed_parts:
        return any(part.lower() in normalized_url for part in allowed_parts)

    if any(part.lower() in normalized_url for part in list_parts):
        return False

    return True


def _has_relevant_keyword(haystack: str) -> bool:
    has_ai_signal = any(keyword.lower() in haystack for keyword in AI_KEYWORDS)
    has_opportunity_signal = any(
        keyword.lower() in haystack for keyword in OPPORTUNITY_KEYWORDS
    )

    return has_ai_signal and has_opportunity_signal
