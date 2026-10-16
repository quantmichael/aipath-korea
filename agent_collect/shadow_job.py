"""Fail-closed, one-shot Cloud Run shadow entrypoint for Agent Automation."""

import json
import os
import sys
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Mapping
from uuid import uuid4

from agent_collect.config.settings import AgentSettings, load_settings
from agent_collect.runtime import AgentRuntime, resolve_supabase_config


_REQUIRED_FLAGS = {
    "AGENT_DISCOVERY_ENABLED": "discovery_enabled",
    "AGENT_LLM_ENABLED": "llm_enabled",
    "AGENT_AUTOMATION_ENABLED": "automation_enabled",
    "AGENT_DISCOVERY_WORKER_ENABLED": "discovery_worker_enabled",
    "AGENT_COLLECTION_WORKER_ENABLED": "collection_worker_enabled",
    "AGENT_PROCESSING_WORKER_ENABLED": "processing_worker_enabled",
}
_DISABLED_FLAGS = {
    "AGENT_PUBLISH_ENABLED": "publish_enabled",
    "AGENT_PUBLISH_WORKER_ENABLED": "publish_worker_enabled",
}


class ShadowConfigurationError(ValueError):
    """A safe-to-log startup rejection containing only setting names."""


def validate_shadow_environment(environ: Mapping[str, str]) -> AgentSettings:
    """Reject missing/unsafe deployment settings before opening any DB connection."""
    if environ.get("APP_ENV") != "production":
        raise ShadowConfigurationError("APP_ENV must explicitly be production")
    for name in (*_REQUIRED_FLAGS, *_DISABLED_FLAGS):
        if name not in environ:
            raise ShadowConfigurationError(f"missing required shadow setting: {name}")
    if environ.get("AGENT_RAW_STORAGE_PROVIDER") != "supabase":
        raise ShadowConfigurationError("AGENT_RAW_STORAGE_PROVIDER must explicitly be supabase")
    settings = load_settings(environ=environ)
    for name, attribute in _REQUIRED_FLAGS.items():
        if not getattr(settings, attribute):
            raise ShadowConfigurationError(f"shadow setting must be true: {name}")
    for name, attribute in _DISABLED_FLAGS.items():
        if getattr(settings, attribute):
            raise ShadowConfigurationError(f"shadow setting must be false: {name}")
    if settings.raw_storage_provider != "supabase":
        raise ShadowConfigurationError("resolved raw storage provider must be supabase")
    if environ.get("AGENT_LLM_MODEL") != "gpt-5.4-nano":
        raise ShadowConfigurationError("AGENT_LLM_MODEL must explicitly be gpt-5.4-nano")
    if settings.extraction_model != "gpt-5.4-nano":
        raise ShadowConfigurationError("resolved extraction model must be gpt-5.4-nano")
    if (settings.extraction_daily_llm_limit, settings.extraction_monthly_llm_limit) != (5, 100):
        raise ShadowConfigurationError("production OpenAI budget limits must remain 5/day and 100/month")
    try:
        config = resolve_supabase_config(environ)
    except RuntimeError as error:
        raise ShadowConfigurationError("missing Agent Supabase credentials") from error
    if not config.url.strip() or not config.service_key.strip():
        raise ShadowConfigurationError("missing Agent Supabase credentials")
    for name in ("AGENT_TAVILY_API_KEY", "OPENAI_API_KEY"):
        if not environ.get(name, "").strip():
            raise ShadowConfigurationError(f"missing required credential: {name}")
    return settings


def _log(record: dict[str, object]) -> None:
    print(json.dumps(record, separators=(",", ":")), flush=True)


def _worker_counts(result: object) -> dict[str, object]:
    if not is_dataclass(result):
        return {}
    return {
        field.name: getattr(result, field.name)
        for field in fields(result)
        if field.name.endswith("_count") and isinstance(getattr(result, field.name), int)
    }


def summary_exit_code(summary: object) -> int:
    if getattr(summary, "status", None) != "COMPLETED" or getattr(summary, "errors", None):
        return 1
    for result in getattr(summary, "results", {}).values():
        if any(_worker_counts(result).get(name, 0) > 0
               for name in ("failure_count", "failed_count")):
            return 1
    return 0


def main() -> int:
    try:
        settings = validate_shadow_environment(os.environ)
    except Exception as error:
        # The exception text may contain transport details; log only a safe type.
        _log({"event": "shadow_startup_rejected", "error_type": type(error).__name__,
              "reason": str(error) if isinstance(error, ShadowConfigurationError) else "invalid settings",
              "publish_enabled": False})
        return 2

    worker_id = f"cloud-run-shadow-{uuid4()}"
    _log({"event": "shadow_startup_validated", "app_env": settings.app_env,
          "raw_storage_provider": settings.raw_storage_provider,
          "publish_enabled": settings.publish_enabled,
          "publish_worker_enabled": settings.publish_worker_enabled,
          "worker_id": worker_id})
    try:
        runtime = AgentRuntime.connect()
        runtime.metadata_evidence()
        _log({"event": "metadata_sdk_initialized", "publish_enabled": False})
        summary = runtime.automation_runtime(
            Path("/tmp/agent-raw-unused"), worker_id=worker_id,
        ).run_once()
    except Exception as error:
        _log({"event": "shadow_run_failed", "worker_id": worker_id,
              "error_type": type(error).__name__, "publish_enabled": False})
        return 2

    for name, result in summary.results.items():
        _log({"event": "shadow_worker_summary", "automation_run_id": str(summary.run_id),
              "worker": name, "worker_id": getattr(result, "worker_id", worker_id),
              "started_at": getattr(result, "started_at", summary.started_at).isoformat(),
              "finished_at": getattr(result, "finished_at", summary.finished_at).isoformat(),
              "status": "FAILED" if any(
                  _worker_counts(result).get(key, 0) > 0
                  for key in ("failure_count", "failed_count")) else "COMPLETED",
              **_worker_counts(result), "publish_enabled": False})
    exit_code = summary_exit_code(summary)
    _log({"event": "shadow_automation_summary", "automation_run_id": str(summary.run_id),
          "worker_id": worker_id, "started_at": summary.started_at.isoformat(),
          "finished_at": summary.finished_at.isoformat(), "status": summary.status,
          "worker_errors": summary.errors, "publish_enabled": False,
          "publish_worker_enabled": False, "exit_code": exit_code})
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
