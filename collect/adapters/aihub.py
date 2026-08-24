from __future__ import annotations

from collect.adapters.base import HtmlSourceAdapter


class AIHubAdapter(HtmlSourceAdapter):
    source_name = "AI Hub"
    detail_url_parts = (
        "/aihubnews/bsnspblanc/view.do",
        "/aihubnews/bsnspblanc/detail.do",
    )
    list_url_parts = (
        "/aihubnews/bsnspblanc/list.do",
    )

