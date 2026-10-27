"""Read only the approved public projection from the existing aipath-korea DB."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from .domain import SECTIONS, https_url, instant

PROJECT_URL = "https://fehlvyoskgphlodzawfn.supabase.co"


def configured_reader():
    """Return no reader until a public Supabase key is configured on the server."""
    key = os.environ.get("AIPATH2_SUPABASE_PUBLISHABLE_KEY")
    if not key:
        return None
    return lambda: read_public_content(PROJECT_URL, key)


def read_public_content(url: str, key: str, *, transport=None):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != urlsplit(PROJECT_URL).netloc or parsed.path not in {"", "/"}:
        raise ValueError("Unexpected Supabase project URL")
    if not key:
        raise ValueError("Supabase publishable key required")
    try:
        with httpx.Client(timeout=5, transport=transport) as client:
            response = client.get(
                f"{PROJECT_URL}/rest/v1/aipath2_public_content",
                params={"select": "*", "order": "published_at.desc"},
                headers={"apikey": key, "Accept": "application/json"},
            )
            response.raise_for_status()
            records = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OSError("AIPATH content database is unavailable") from exc
    if not isinstance(records, list):
        raise ValueError("Public content projection must be a list")
    return [_article_from_row(row) for row in records]


def _article_from_row(row):
    if not isinstance(row, dict) or row.get("section") not in SECTIONS:
        raise ValueError("Invalid public article section")
    section = row["section"]
    slug = row.get("slug")
    if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("Invalid public article slug")
    if not row.get("title") or not row.get("summary") or not row.get("body_markdown"):
        raise ValueError("Incomplete public article")
    payload = row.get("section_payload")
    if not isinstance(payload, dict):
        raise ValueError("Invalid public article payload")
    # The DB contains the public projection only; do not include private test IDs.
    required = {
        "ai_now": {"what_changed", "why_it_matters", "who_is_affected", "availability", "limitations", "next_action"},
        "ai_tips": {"task", "prerequisites", "steps", "example_input", "example_result", "troubleshooting", "tests"},
        "prompt_lab": {"purpose", "not_for", "template", "variables", "example_input", "example_output", "limitations", "tests"},
    }[section]
    if set(payload) != required:
        raise ValueError("Public article fields mismatch")
    list_fields = ({"prerequisites", "steps", "troubleshooting"} if section == "ai_tips" else
                   {"not_for", "limitations"} if section == "prompt_lab" else set())
    for name, value in payload.items():
        if name in list_fields:
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError("Invalid public article list")
        elif name == "tests":
            if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
                raise ValueError("Invalid public article tests")
        elif name == "variables":
            if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
                raise ValueError("Invalid public article variables")
        elif not isinstance(value, str):
            raise ValueError("Invalid public article field")
    now = datetime.now(timezone.utc)
    for date_key in ("published_at", "verified_at", "next_review_at"):
        if not row.get(date_key) or instant(row[date_key]) > now and date_key != "next_review_at":
            raise ValueError("Invalid public article date")
    sources = row.get("source_links")
    if not isinstance(sources, list) or not sources or not all(
        isinstance(s, dict) and isinstance(s.get("title"), str) and s["title"]
        and (s.get("url") is None or isinstance(s["url"], str)) for s in sources
    ):
        raise ValueError("Public article needs sources")
    for source in sources:
        https_url(source.get("url"))
    related = row.get("related_content_ids") or []
    if not isinstance(related, list):
        raise ValueError("Invalid related content")
    if not isinstance(row.get("id"), str):
        raise ValueError("Invalid public article ID")
    return {
        "id": row["id"], "section": section,
        "path": "/" + SECTIONS[section][0] + "/" + slug,
        "title": row["title"], "summary": row["summary"],
        "body_markdown": row["body_markdown"],
        "published_at": row["published_at"], "updated_at": row.get("updated_at"),
        "verified_at": row["verified_at"], "ai_assisted": row.get("ai_assisted") is True,
        "correction_note": row.get("correction_note"),
        "stale": instant(row["next_review_at"]) <= now,
        "section_payload": payload, "related_content_ids": related,
        "sources": sources,
    }
