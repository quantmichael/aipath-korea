"""Deterministic selection of a generic collector from source metadata."""

from agent_collect.collectors.contracts import Collector
from agent_collect.collectors.engine import ApiCollector, HtmlCollector, HttpTransport, RssCollector
from agent_collect.schemas.models import CollectionMethod, Source


class UnsupportedCollectionMethodError(ValueError):
    pass


class CollectionPlanner:
    def __init__(self, transport: HttpTransport | None = None) -> None:
        self.collectors: dict[CollectionMethod, Collector] = {
            CollectionMethod.HTTP_HTML: HtmlCollector(transport),
            CollectionMethod.RSS: RssCollector(transport),
            CollectionMethod.REST_API: ApiCollector(transport),
        }
        self.collectors[CollectionMethod.HTTP] = self.collectors[CollectionMethod.HTTP_HTML]
        self.collectors[CollectionMethod.HTML] = self.collectors[CollectionMethod.HTTP_HTML]

    def plan(self, source: Source) -> Collector:
        collector = self.collectors.get(source.collection_method)
        if collector is None:
            raise UnsupportedCollectionMethodError(
                f"unsupported collection method: {source.collection_method}"
            )
        return collector
