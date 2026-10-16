"""Offline runtime, CLI, and Agent migration scope checks."""

import io
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from agent_collect.__main__ import main
from agent_collect.config.settings import load_settings
from agent_collect.discovery.providers import FakeSearchProvider, SearchHit
from agent_collect.repositories.discovery import InMemoryDiscoveryRepository
from agent_collect.repositories.memory import (
    InMemoryCollectionRunRepository,
    InMemoryRawDocumentRepository,
    InMemorySourceRepository,
)
from agent_collect.runtime import AgentRuntime, resolve_supabase_config


class RuntimeConfigTests(unittest.TestCase):
    def test_discovery_runtime_flag_policy_and_provider(self) -> None:
        runtime = AgentRuntime(
            sources=InMemorySourceRepository(), runs=InMemoryCollectionRunRepository(),
            documents=InMemoryRawDocumentRepository(), settings=load_settings(environ={}),
        )
        provider = FakeSearchProvider([SearchHit("AI Conference", "https://example.org/ai-conference")])
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            runtime.discovery_runner(provider, repository=InMemoryDiscoveryRepository())
        runtime.settings = load_settings(environ={
            "AGENT_DISCOVERY_ENABLED": "true", "AGENT_DISCOVERY_SEARCH_PROVIDER": "fake",
            "AGENT_DISCOVERY_MAX_RESULTS": "1", "AGENT_DISCOVERY_PROMOTION_THRESHOLD": "0.90",
        })
        runner = runtime.discovery_runner(provider, repository=InMemoryDiscoveryRepository())
        self.assertEqual((runner.policy.max_results, runner.gate.threshold), (1, 0.90))
        self.assertIsNotNone(runner.quality_evaluator)

    def test_agent_credentials_override_backend_credentials(self) -> None:
        config = resolve_supabase_config({
            "AGENT_SUPABASE_URL": "https://agent.example.test",
            "AGENT_SUPABASE_SERVICE_KEY": "agent-key",
            "SUPABASE_URL": "https://service.example.test",
            "SUPABASE_SECRET_KEY": "service-key",
        })
        self.assertEqual(config.url, "https://agent.example.test")
        self.assertEqual(config.service_key, "agent-key")

    def test_existing_server_credentials_are_supported(self) -> None:
        config = resolve_supabase_config({
            "SUPABASE_URL": "https://service.example.test",
            "SUPABASE_SECRET_KEY": "service-key",
        })
        self.assertEqual(config.service_key, "service-key")
        with self.assertRaisesRegex(RuntimeError, "missing AGENT_SUPABASE"):
            resolve_supabase_config({})

    def test_metadata_sdk_initialization_does_not_query_db(self) -> None:
        client = Mock()
        runtime = AgentRuntime(
            sources=SimpleNamespace(client=client), runs=InMemoryCollectionRunRepository(),
            documents=InMemoryRawDocumentRepository(), settings=load_settings(environ={}),
        )
        sdk = runtime.metadata_evidence()
        self.assertIs(sdk.client, client)
        self.assertEqual(client.mock_calls, [])

    def test_cli_config_check_requires_no_external_dependency_or_network(self) -> None:
        output = io.StringIO()
        with patch.dict("os.environ", {}, clear=True), redirect_stdout(output):
            self.assertEqual(main(["config-check"]), 0)
        self.assertIn('"credentials": "missing"', output.getvalue())


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = AgentRuntime(
            sources=InMemorySourceRepository(),
            runs=InMemoryCollectionRunRepository(),
            documents=InMemoryRawDocumentRepository(),
            settings=load_settings(environ={}),
        )

    def test_register_duplicate_and_activation_flag_is_refused(self) -> None:
        with patch("agent_collect.__main__.AgentRuntime.connect", return_value=self.runtime):
            with patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(main(["register-source", "--name", "Example",
                                       "--url", "https://example.org/", "--activate"]), 2)
            first = io.StringIO()
            with redirect_stdout(first):
                self.assertEqual(main(["register-source", "--name", "Example",
                                       "--url", "https://example.org/"]), 0)
            self.assertIn('"created": true', first.getvalue())
            self.assertIn('"active": false', first.getvalue())
            second = io.StringIO()
            with redirect_stdout(second):
                self.assertEqual(main(["register-source", "--name", "Other",
                                       "--url", "https://example.org/#fragment"]), 0)
            self.assertIn('"created": false', second.getvalue())

    def test_show_run_unknown_is_nonzero(self) -> None:
        with patch("agent_collect.__main__.AgentRuntime.connect", return_value=self.runtime), \
             patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(main(["show-run", "--run-id", str(uuid4())]), 2)


class MigrationScopeTests(unittest.TestCase):
    def test_migrations_only_touch_agent_tables(self) -> None:
        root = Path(__file__).resolve().parents[1] / "agent_collect" / "migrations"
        first = (root / "001_agent_foundation.sql").read_text()
        second = (root / "002_collection_foundation.sql").read_text()
        names = re.findall(r"(?im)^create table public\.(\w+)", first)
        self.assertEqual(len(names), 7)
        self.assertTrue(all(name.startswith("agent_") for name in names))
        for sql in (first, second):
            self.assertNotRegex(sql, r"(?im)^\s*(drop\s+table|truncate|delete\s+from)\b")
            for target in re.findall(r"(?im)^\s*alter table public\.(\w+)", sql):
                self.assertTrue(target.startswith("agent_"), target)
        self.assertIn("drop constraint if exists agent_sources_collection_method_check", second)


if __name__ == "__main__":
    unittest.main()
