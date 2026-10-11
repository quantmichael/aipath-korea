from __future__ import annotations

import base64
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from api import opportunities as api
from collect import repository
from runtime_access import is_read_only_runtime


class _Query:
    def __init__(self, rows):
        self.rows = rows
        self.statuses = None
        self.slug = None

    def select(self, _columns):
        return self

    def in_(self, field, values):
        if field == "status":
            self.statuses = set(values)
        return self

    def eq(self, field, value):
        if field == "slug":
            self.slug = value
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, _count):
        return self

    def execute(self):
        rows = self.rows
        if self.statuses is not None:
            rows = [row for row in rows if row["status"] in self.statuses]
        if self.slug is not None:
            rows = [row for row in rows if row["slug"] == self.slug]
        return SimpleNamespace(data=rows)


class _ReadOnlyClient:
    def __init__(self):
        self.rows = [
            {"id": "open-id", "slug": "open-item", "status": "open"},
            {"id": "closed-id", "slug": "legacy-closed", "status": "closed"},
        ]

    def table(self, name):
        assert name == "opportunities"
        return _Query(self.rows)

    def rpc(self, name, _params):
        assert name == "opportunity_public_archive_ids"
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=[]))


class PreviewReadOnlyTests(unittest.TestCase):
    def test_preview_environment_and_explicit_flag_are_read_only(self):
        with patch.dict(api.os.environ, {"VERCEL_ENV": "preview"}, clear=True):
            self.assertTrue(is_read_only_runtime())
        with patch.dict(api.os.environ, {"VERCEL_TARGET_ENV": "preview"}, clear=True):
            self.assertTrue(is_read_only_runtime())
        with patch.dict(api.os.environ, {"AIPATH_READ_ONLY": "1"}, clear=True):
            self.assertTrue(is_read_only_runtime())
        with patch.dict(api.os.environ, {"VERCEL_ENV": "production"}, clear=True):
            self.assertFalse(is_read_only_runtime())

    def test_preview_uses_only_separate_read_only_key(self):
        env = {"VERCEL_ENV": "preview", "SUPABASE_URL": "https://example.invalid",
               "SUPABASE_SECRET_KEY": "production-secret",
               "SUPABASE_READ_ONLY_KEY": "sb_publishable_test"}
        with patch.dict(api.os.environ, env, clear=True), \
             patch.object(api, "create_client", return_value=_ReadOnlyClient()) as create:
            self.assertEqual(api.get_opportunities()["count"], 1)
            self.assertEqual(api.get_opportunity("open-item")["opportunity"]["status"], "open")
            create.assert_called_with(env["SUPABASE_URL"], env["SUPABASE_READ_ONLY_KEY"])

    def test_preview_never_falls_back_to_service_role_key(self):
        env = {"VERCEL_ENV": "preview", "SUPABASE_URL": "https://example.invalid",
               "SUPABASE_SECRET_KEY": "production-secret"}
        with patch.dict(api.os.environ, env, clear=True), \
             patch.object(api, "create_client") as create:
            with self.assertRaises(RuntimeError):
                api.create_supabase_client()
            create.assert_not_called()

    def test_preview_rejects_service_role_in_read_only_slot(self):
        payload = base64.urlsafe_b64encode(json.dumps(
            {"role": "service_role"}
        ).encode()).decode().rstrip("=")
        for candidate in ("sb_secret_test", f"x.{payload}.x", "same-secret"):
            with self.subTest(candidate=candidate[:12]), \
                 patch.dict(api.os.environ, {
                     "VERCEL_ENV": "preview", "SUPABASE_URL": "https://example.invalid",
                     "SUPABASE_SECRET_KEY": "same-secret",
                     "SUPABASE_READ_ONLY_KEY": candidate,
                 }, clear=True), patch.object(api, "create_client") as create:
                with self.assertRaises(RuntimeError):
                    api.create_supabase_client()
                create.assert_not_called()

    def test_collect_repository_cannot_open_preview_database(self):
        with patch.dict(repository.os.environ, {
            "VERCEL_ENV": "preview", "SUPABASE_URL": "https://example.invalid",
            "SUPABASE_SECRET_KEY": "production-secret",
        }, clear=True), patch.object(repository, "create_client") as create:
            with self.assertRaisesRegex(RuntimeError, "read-only"):
                repository.create_supabase_client()
            create.assert_not_called()

    def test_all_collect_http_routes_reject_before_work_or_secret_logging(self):
        client = TestClient(api.app)
        with patch.dict(api.os.environ, {
            "VERCEL_ENV": "preview", "CRON_SECRET": "do-not-log-me",
        }, clear=True), patch.object(api, "run_collection") as run, \
             patch.object(api, "promote_candidates") as promote, \
             patch.object(api, "apply_candidate_action") as action, \
             patch.object(api, "list_review_candidates") as review:
            responses = [
                client.get("/api/collect/run", params={"secret": "do-not-log-me"}),
                client.post("/api/collect/promote", headers={"x-collect-secret": "do-not-log-me"}),
                client.get("/api/collect/candidates", headers={"x-collect-secret": "do-not-log-me"}),
                client.post("/api/collect/candidates/action", json={
                    "candidate_ids": ["candidate"], "action": "publish"},
                    headers={"x-collect-secret": "do-not-log-me"}),
            ]
            self.assertTrue(all(response.status_code == 403 for response in responses))
            self.assertTrue(all("do-not-log-me" not in response.text for response in responses))
            run.assert_not_called()
            promote.assert_not_called()
            action.assert_not_called()
            review.assert_not_called()

    def test_production_still_uses_original_secret_key(self):
        env = {"VERCEL_ENV": "production", "SUPABASE_URL": "https://example.invalid",
               "SUPABASE_SECRET_KEY": "production-secret"}
        with patch.dict(api.os.environ, env, clear=True), \
             patch.object(api, "create_client", return_value=object()) as create:
            api.create_supabase_client()
            create.assert_called_once_with(env["SUPABASE_URL"], env["SUPABASE_SECRET_KEY"])


if __name__ == "__main__":
    unittest.main()
