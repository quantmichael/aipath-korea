from __future__ import annotations

from collect.adapters.base import HtmlSourceAdapter


class NipaAdapter(HtmlSourceAdapter):
    source_name = "정보통신산업진흥원(NIPA)"
    detail_url_parts = (
        "/home/2-2/",
    )
    list_url_parts = (
        "/home/2-2",
    )

