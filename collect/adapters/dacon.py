from __future__ import annotations

import re

from collect.adapters.base import HtmlSourceAdapter


class DaconAdapter(HtmlSourceAdapter):
    source_name = "DACON"
    detail_url_parts = (
        "/competitions/official/",
        "/competitions/open/",
    )
    list_url_parts = (
        "/competitions",
    )

    def clean_title(self, title: str) -> str:
        cleaned = super().clean_title(title)
        cleaned = re.split(r"\s+\|\s+", cleaned, maxsplit=1)[0]
        cleaned = re.sub(r"\s+참가신청중\s+[0-9,]+명.*$", "", cleaned)
        return cleaned.strip()

