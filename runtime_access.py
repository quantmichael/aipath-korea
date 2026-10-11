"""Runtime access rules shared by the public API and legacy Collect."""

from __future__ import annotations

import base64
import json
import os


def is_read_only_runtime() -> bool:
    """Fail closed for Vercel Preview and explicit read-only deployments."""
    return (
        os.environ.get("AIPATH_READ_ONLY") == "1"
        or os.environ.get("VERCEL_ENV") == "preview"
        or os.environ.get("VERCEL_TARGET_ENV") == "preview"
    )


def require_collect_write_access() -> None:
    if is_read_only_runtime():
        raise RuntimeError("Collect is disabled in read-only deployments")


def require_read_only_supabase_key(key: str) -> None:
    """Accept only publishable or explicitly read-only role credentials."""
    if key == os.environ.get("SUPABASE_SECRET_KEY") or key.startswith("sb_secret_"):
        raise RuntimeError("Preview Supabase key must not be service_role")
    if key.startswith("sb_publishable_"):
        return
    parts = key.split(".")
    if len(parts) != 3:
        raise RuntimeError("Unsupported Preview Supabase key format")
    try:
        payload = json.loads(base64.urlsafe_b64decode(
            parts[1] + "=" * (-len(parts[1]) % 4)
        ))
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError("Invalid Preview Supabase key format") from None
    if payload.get("role") not in {"anon", "aipath_preview_reader"}:
        raise RuntimeError("Preview Supabase key must use a read-only role")
