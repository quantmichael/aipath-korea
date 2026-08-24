from __future__ import annotations

from collect.adapters.aihub import AIHubAdapter
from collect.adapters.base import HtmlSourceAdapter
from collect.adapters.dacon import DaconAdapter
from collect.adapters.nipa import NipaAdapter
from collect.models import Source


ADAPTERS = {
    adapter.source_name: adapter
    for adapter in (
        AIHubAdapter(),
        DaconAdapter(),
        NipaAdapter(),
    )
}


def get_html_adapter(source: Source) -> HtmlSourceAdapter:
    return ADAPTERS.get(source.name) or HtmlSourceAdapter()
