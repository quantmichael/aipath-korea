from __future__ import annotations

import re
from urllib.parse import urlparse

from collect.models import OpportunityCandidate


def slugify(value: str) -> str:
    normalized = value.lower()
    normalized = re.sub(r"[^a-z0-9가-힣]+", "-", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-")

    if not normalized:
        return "opportunity"

    # Existing DB slug rule is ASCII-only, so keep Korean titles URL-safe by
    # falling back to a stable domain/path based slug in the service layer.
    ascii_slug = re.sub(r"[^a-z0-9-]+", "", normalized)
    return ascii_slug.strip("-") or "opportunity"


def slug_from_url(url: str) -> str:
    parsed = urlparse(url)
    parts = [parsed.netloc.replace("www.", ""), parsed.path]
    slug = slugify("-".join(parts))
    return slug[:80].strip("-") or "opportunity"


def normalize_candidate(candidate: OpportunityCandidate) -> OpportunityCandidate:
    if candidate.title:
        candidate.title = " ".join(candidate.title.split())

    if candidate.official_url:
        candidate.official_url = candidate.official_url.strip()

    if not candidate.slug:
        if candidate.official_url:
            candidate.slug = slug_from_url(candidate.official_url)
        elif candidate.title:
            candidate.slug = slugify(candidate.title)

    candidate.price_type = candidate.price_type or "unknown"
    return candidate
