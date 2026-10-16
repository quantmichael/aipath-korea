"""Container startup contract without credentials, DB or provider calls."""

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from agent_collect.shadow_job import main, summary_exit_code, validate_shadow_environment


@dataclass
class FakeWorkerSummary:
    worker_id: str
    started_at: datetime
    finished_at: datetime
    due_count: int
    failed_count: int


def shadow_env():
    return {
        "APP_ENV": "production",
        "AGENT_RAW_STORAGE_PROVIDER": "supabase",
        "AGENT_DISCOVERY_ENABLED": "true",
        "AGENT_LLM_ENABLED": "true",
        "AGENT_LLM_MODEL": "gpt-5.4-nano",
        "AGENT_PUBLISH_ENABLED": "false",
        "AGENT_AUTOMATION_ENABLED": "true",
        "AGENT_DISCOVERY_WORKER_ENABLED": "true",
        "AGENT_COLLECTION_WORKER_ENABLED": "true",
        "AGENT_PROCESSING_WORKER_ENABLED": "true",
        "AGENT_PUBLISH_WORKER_ENABLED": "false",
        "AGENT_SUPABASE_URL": "https://example.supabase.co",
        "AGENT_SUPABASE_SERVICE_KEY": "dummy-secret",
        "AGENT_TAVILY_API_KEY": "dummy-tavily",
        "OPENAI_API_KEY": "dummy-openai",
    }


class ShadowJobTests(unittest.TestCase):
    def test_production_resolution_and_missing_credentials(self):
        env = shadow_env()
        settings = validate_shadow_environment(env)
        self.assertEqual(settings.app_env, "production")
        self.assertEqual(settings.raw_storage_provider, "supabase")
        self.assertEqual((settings.extraction_daily_llm_limit,
                          settings.extraction_monthly_llm_limit), (5, 100))
        self.assertFalse(settings.publish_enabled or settings.publish_worker_enabled)
        del env["OPENAI_API_KEY"]
        with self.assertRaises(ValueError):
            validate_shadow_environment(env)

    def test_publish_guard_rejects_missing_or_enabled_before_connect(self):
        for name in ("AGENT_PUBLISH_ENABLED", "AGENT_PUBLISH_WORKER_ENABLED"):
            for unsafe in (None, "true"):
                with self.subTest(name=name, unsafe=unsafe):
                    env = shadow_env()
                    if unsafe is None:
                        del env[name]
                    else:
                        env[name] = unsafe
                    with patch.dict(os.environ, env, clear=True), patch(
                        "agent_collect.shadow_job.AgentRuntime.connect"
                    ) as connect, redirect_stdout(io.StringIO()) as output:
                        self.assertEqual(main(), 2)
                        connect.assert_not_called()
                        self.assertIn("shadow_startup_rejected", output.getvalue())
                        self.assertNotIn("dummy-secret", output.getvalue())

    def test_invalid_config_rejected_before_connect(self):
        for name, value in (("APP_ENV", "development"),
                            ("AGENT_RAW_STORAGE_PROVIDER", "local"),
                            ("AGENT_LLM_MODEL", ""),
                            ("AGENT_EXTRACTION_MODEL", "other-model"),
                            ("AGENT_EXTRACTION_DAILY_LLM_LIMIT", "6"),
                            ("AGENT_AUTOMATION_ENABLED", "false"),
                            ("AGENT_DISCOVERY_ENABLED", "false")):
            with self.subTest(name=name):
                env = shadow_env()
                env[name] = value
                with patch.dict(os.environ, env, clear=True), patch(
                    "agent_collect.shadow_job.AgentRuntime.connect"
                ) as connect, redirect_stdout(io.StringIO()):
                    self.assertEqual(main(), 2)
                    connect.assert_not_called()

    def test_one_shot_success_and_worker_failure_exit_code(self):
        now = datetime.now(timezone.utc)
        worker = FakeWorkerSummary("worker", now, now, 1, 0)
        summary = SimpleNamespace(run_id=uuid4(), started_at=now, finished_at=now,
                                  status="COMPLETED", errors={}, results={"processing": worker})
        run_once = Mock(return_value=summary)
        runner = SimpleNamespace(run_once=run_once)
        metadata = Mock(return_value=object())
        runtime = SimpleNamespace(automation_runtime=lambda *a, **k: runner,
                                  metadata_evidence=metadata)
        with patch.dict(os.environ, shadow_env(), clear=True), patch(
            "agent_collect.shadow_job.AgentRuntime.connect", return_value=runtime
        ) as connect, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(), 0)
            connect.assert_called_once()
            metadata.assert_called_once_with()
            run_once.assert_called_once_with()
            records = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertIn("metadata_sdk_initialized", [r["event"] for r in records])
            self.assertEqual(records[-1]["exit_code"], 0)
            self.assertFalse(records[-1]["publish_enabled"])
        worker.failed_count = 1
        self.assertEqual(summary_exit_code(summary), 1)
        with patch.dict(os.environ, shadow_env(), clear=True), patch(
            "agent_collect.shadow_job.AgentRuntime.connect", return_value=runtime
        ), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(), 1)
            self.assertEqual(json.loads(output.getvalue().splitlines()[-1])["exit_code"], 1)
        summary.status = "LOCKED"
        self.assertEqual(summary_exit_code(summary), 1)


if __name__ == "__main__":
    unittest.main()
