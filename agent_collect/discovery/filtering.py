"""No-network URL and duplicate screening before candidate creation."""

from dataclasses import dataclass
from urllib.parse import urlsplit

from agent_collect.collectors.security import UnsafeTargetError, validate_target_url
from agent_collect.discovery.models import SearchResult
from agent_collect.registry.service import canonicalize_url
from agent_collect.repositories.contracts import SourceRepository


IRRELEVANT_SUFFIXES = frozenset({
    ".pdf", ".zip", ".gz", ".jpg", ".jpeg", ".png", ".gif",
    ".webp", ".mp4", ".mp3", ".csv", ".xlsx", ".docx", ".pptx",
})


@dataclass(frozen=True, slots=True)
class FilterDecision:
    accepted: bool
    canonical_url: str | None
    domain: str | None
    reasons: tuple[str, ...]


class CandidateFilter:
    def __init__(self, sources: SourceRepository, *, existing_candidate_urls: set[str] | None = None) -> None:
        self.sources = sources
        self.existing_candidate_urls = existing_candidate_urls if existing_candidate_urls is not None else set()
        self._seen_urls: set[str] = set()
        self._seen_domains: set[str] = set()

    def evaluate(self, result: SearchResult) -> FilterDecision:
        try:
            _, domain, _, _ = validate_target_url(result.original_url)
            canonical = canonicalize_url(result.original_url)
        except (UnsafeTargetError, ValueError, UnicodeError, AttributeError):
            return FilterDecision(False, None, None, ("unsafe_or_malformed_url",))
        reasons: list[str] = []
        path = urlsplit(canonical).path.lower()
        if any(path.endswith(suffix) for suffix in IRRELEVANT_SUFFIXES):
            reasons.append("irrelevant_file_type")
        if canonical in self._seen_urls:
            reasons.append("duplicate_url_in_query")
        same_domain = domain in self._seen_domains
        if self.sources.find_by_canonical_url(canonical) is not None:
            reasons.append("existing_source")
        if canonical in self.existing_candidate_urls:
            reasons.append("existing_candidate")
        # Search results from one host can be distinct pages. Domain is a hint,
        # never a URL-level rejection rule; Source promotion handles site identity.
        if "irrelevant_file_type" not in reasons:
            self._seen_urls.add(canonical)
            self._seen_domains.add(domain)
        if reasons:
            return FilterDecision(False, canonical, domain, tuple(reasons))
        return FilterDecision(True, canonical, domain,
                              ("same_domain_as_prior_result",) if same_domain else ())
