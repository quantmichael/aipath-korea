from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from api import opportunities as api


class _Query:
    def __init__(self, rows):
        self.rows = rows
        self.allowed = None
        self.slug = None
        self.max_rows = None

    def select(self, _fields):
        return self

    def in_(self, field, values):
        assert field == "status"
        self.allowed = set(values)
        return self

    def eq(self, field, value):
        assert field == "slug"
        self.slug = value
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, count):
        self.max_rows = count
        return self

    def execute(self):
        data = [row for row in self.rows if row["status"] in self.allowed]
        if self.slug is not None:
            data = [row for row in data if row["slug"] == self.slug]
        if self.max_rows is not None:
            data = data[: self.max_rows]
        return SimpleNamespace(data=data)


class _ProofQuery:
    def __init__(self, proofs, fail=False):
        self.proofs = proofs
        self.fail = fail
        self.ids = set()

    def select(self, _fields):
        return self

    def in_(self, field, values):
        assert field == "id"
        self.ids = set(values)
        return self

    def execute(self):
        if self.fail:
            raise RuntimeError("mock provenance unavailable")
        return SimpleNamespace(data=[p for p in self.proofs if p["id"] in self.ids])


class _Client:
    def __init__(self, rows, *, archive_ids=(), windows=(), proofs=(),
                 fail_rpc=False, fail_proofs=False):
        self.rows = rows
        self.archive_ids = set(archive_ids)
        self.windows = list(windows)
        self.proofs = list(proofs)
        self.fail_rpc = fail_rpc
        self.fail_proofs = fail_proofs

    def table(self, name):
        if name == "opportunities":
            return _Query(self.rows)
        if name == "opportunity_candidates":
            return _ProofQuery(self.proofs, self.fail_proofs)
        raise AssertionError(name)

    def rpc(self, name, args):
        if self.fail_rpc:
            raise RuntimeError("mock RPC unavailable")
        ids = set(args["p_ids"])
        if name == "opportunity_public_archive_ids":
            data = [{"opportunity_id": item} for item in self.archive_ids & ids]
        elif name == "opportunity_recommendation_windows":
            data = [item for item in self.windows if item["opportunity_id"] in ids]
        else:
            raise AssertionError(name)
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=data))


def _legacy_fixture():
    legacy_id, deadline = next(iter(api.LEGACY_RECOMMENDATION_DEADLINES.items()))
    origin = api.LEGACY_COLLECT_ORIGINS[legacy_id]
    row = {"id": legacy_id, "status": "open",
           "source_id": origin["source_id"],
           "external_id": origin["external_id"],
           "created_at": origin["opportunity_created_at"],
           "updated_at": "2026-10-02T07:50:59+00:00",
           "application_deadline_at": deadline}
    proof = {"id": origin["candidate_id"],
             "source_id": origin["source_id"],
             "external_id": origin["external_id"],
             "collection_run_id": origin["collection_run_id"],
             "created_at": origin["candidate_created_at"]}
    return row, proof


class PublicArchiveTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"id": status, "slug": status, "status": status}
            for status in ("draft", "scheduled", "open", "closed", "ongoing", "ended", "cancelled")
        ]

    def test_legacy_closed_and_ended_are_not_exposed(self):
        with patch.object(api, "create_supabase_client", return_value=_Client(self.rows)):
            result = api.get_opportunities()
        self.assertEqual(
            [row["status"] for row in result["opportunities"]],
            ["scheduled", "open", "ongoing"],
        )

    def test_worker_closed_and_ended_remain_available(self):
        with patch.object(api, "create_supabase_client",
                   return_value=_Client(self.rows, archive_ids={"closed", "ended"})):
            self.assertEqual(api.get_opportunities()["count"], 5)
            self.assertEqual(api.get_opportunity("closed")["opportunity"]["status"], "closed")
            self.assertEqual(api.get_opportunity("ended")["opportunity"]["status"], "ended")

    def test_legacy_closed_detail_and_failed_archive_rpc_fail_closed(self):
        with patch.object(api, "create_supabase_client", return_value=_Client(self.rows)):
            with self.assertRaises(HTTPException) as caught:
                api.get_opportunity("closed")
            self.assertEqual(caught.exception.status_code, 404)
        with patch.object(api, "create_supabase_client",
                   return_value=_Client(self.rows, fail_rpc=True)):
            self.assertEqual(api.get_opportunities()["count"], 3)

    def test_recommendations_only_include_currently_open_status(self):
        self.assertEqual(
            api.RECOMMENDABLE_OPPORTUNITY_STATUSES,
            ["open"],
        )

    def test_recommendation_date_guard(self):
        now = datetime(2026, 10, 11, tzinfo=timezone.utc)
        exact = {"application_precision": "EXACT_TIME",
                 "application_cutoff_at": "2026-10-12T00:00:00+00:00"}
        date_only = {"application_precision": "DATE_ONLY",
                     "application_cutoff_at": "2026-10-11T15:00:00+00:00"}
        cases = [
            ({"status": "open"}, exact, True),
            ({"status": "scheduled"}, date_only, False),
            ({"status": "ongoing", "event_end_at": "2026-10-10T00:00:00+00:00"},
             {"event_end_cutoff_at": "2026-10-10T00:00:00+00:00"}, False),
            ({"status": "ongoing", "event_end_at": "2026-10-12T09:00:00+09:00"},
             {"event_end_cutoff_at": "2026-10-12T09:00:00+09:00"}, False),
            ({"status": "open", "application_deadline_at": "2026-10-12T00:00:00+00:00",
              "event_end_at": "2026-10-10T00:00:00+00:00"}, exact, False),
            ({"status": "open", "event_end_at": "2026-10-12T00:00:00+00:00"},
             exact, False),
            ({"status": "open"}, None, False),
            ({"status": "open"}, {"application_precision": "UNKNOWN"}, False),
        ]
        for row, window, expected in cases:
            with self.subTest(row=row):
                self.assertEqual(api._recommendation_date_safe(row, window, now), expected)

    def test_date_only_kst_deadline_matches_worker_boundary(self):
        row = {"status": "open", "application_deadline_at": "2026-10-10T00:00:00+00:00"}
        window = {"application_precision": "DATE_ONLY",
                  "application_cutoff_at": "2026-10-10T15:00:00+00:00"}
        before = datetime(2026, 10, 10, 14, 59, 59, tzinfo=timezone.utc)
        at_cutoff = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)
        self.assertTrue(api._recommendation_date_safe(row, window, before))
        self.assertFalse(api._recommendation_date_safe(row, window, at_cutoff))

    def test_exact_time_boundary(self):
        cutoff = datetime(2026, 10, 11, tzinfo=timezone.utc)
        row = {"status": "open"}
        window = {"application_precision": "EXACT_TIME",
                  "application_cutoff_at": cutoff.isoformat()}
        self.assertTrue(api._recommendation_date_safe(row, window, cutoff))
        self.assertFalse(api._recommendation_date_safe(
            row, window, cutoff + timedelta(microseconds=1)))

    def test_legacy_unknown_fallback_is_limited_to_audited_future_rows(self):
        base, proof = _legacy_fixture()
        cutoff = datetime.fromisoformat(base["application_deadline_at"])
        self.assertEqual(api._recommendation_basis(
            base, None, cutoff - timedelta(seconds=1), proof),
            "legacy_unverified_future")
        self.assertIsNone(api._recommendation_basis(base, None, cutoff, proof))
        self.assertIsNone(api._recommendation_basis(
            {**base, "id": "not-a-legacy-id"}, None,
            cutoff - timedelta(seconds=1), proof))
        self.assertIsNone(api._recommendation_basis(
            {**base, "status": "scheduled"}, None,
            cutoff - timedelta(seconds=1), proof))
        self.assertIsNone(api._recommendation_basis(
            {**base, "application_deadline_at":
             (cutoff + timedelta(days=1)).isoformat()}, None,
            cutoff - timedelta(seconds=1), proof))
        self.assertIsNone(api._recommendation_basis(
            {**base, "updated_at": "2026-10-11T00:00:00+00:00"}, None,
            cutoff - timedelta(seconds=1), proof))
        self.assertIsNone(api._recommendation_basis(
            {**base, "event_start_at": (cutoff - timedelta(days=1)).isoformat()},
            None, cutoff - timedelta(seconds=1), proof))
        self.assertIsNone(api._recommendation_basis(
            base, {"application_precision": "UNKNOWN"},
            cutoff - timedelta(seconds=1), proof))

    def test_legacy_fallback_holds_null_and_past_event_dates(self):
        base, proof = _legacy_fixture()
        now = datetime.fromisoformat(base["application_deadline_at"]) - timedelta(days=1)
        self.assertIsNone(api._recommendation_basis(
            {**base, "application_deadline_at": None}, None, now, proof))
        self.assertIsNone(api._recommendation_basis(
            {**base, "event_end_at": (now - timedelta(seconds=1)).isoformat()},
            None, now, proof))

    def test_missing_collect_origin_and_agent_row_never_use_legacy_fallback(self):
        base, proof = _legacy_fixture()
        now = datetime.fromisoformat(base["application_deadline_at"]) - timedelta(days=1)
        self.assertIsNone(api._recommendation_basis(base, None, now))
        self.assertIsNone(api._recommendation_basis(
            base, None, now, {**proof, "collection_run_id": None}))
        self.assertIsNone(api._recommendation_basis(
            {**base, "source_id": "agent-source"}, None, now, proof))
        self.assertIsNone(api._recommendation_basis(
            {**base, "external_id": "runtime_agent_publish_smoke_test:abc"},
            None, now, proof))
        self.assertIsNone(api._recommendation_basis(
            {**base, "created_at": "2026-09-01T00:00:00+00:00"},
            None, now, proof))

    def test_manual_future_rows_have_no_legacy_allowlist_entry(self):
        self.assertNotIn("a01dde02-2fae-44d4-8252-d7090366d8b4",
                         api.LEGACY_RECOMMENDATION_DEADLINES)
        self.assertNotIn("c15c936e-97d1-4c12-8773-9fff81e9f9d1",
                         api.LEGACY_RECOMMENDATION_DEADLINES)

    def test_date_only_candidate_reaches_recommender_until_kst_cutoff(self):
        class BeforeCutoff(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 10, 10, 14, 59, 59, tzinfo=timezone.utc)

        rows = [{"id": "date-only", "slug": "date-only", "status": "open",
                 "application_deadline_at": "2026-10-10T00:00:00+00:00"}]
        windows = [{"opportunity_id": "date-only", "application_precision": "DATE_ONLY",
                    "application_cutoff_at": "2026-10-10T15:00:00+00:00"}]
        payload = api.RecommendationRequest(
            level="beginner", interest="AI", goal="learning"
        )
        with patch.dict(api.os.environ, {"OPENAI_API_KEY": "test-only"}), \
             patch.object(api, "datetime", BeforeCutoff), \
             patch.object(api, "create_supabase_client",
                          return_value=_Client(rows, windows=windows)), \
             patch.object(api, "OpenAI") as ai_client:
            ai_client.return_value.responses.parse.return_value.output_parsed = (
                api.AIRecommendationResult(recommendations=[])
            )
            api.recommend_opportunities(payload)
            ai_client.return_value.responses.parse.assert_called_once()

    def test_expired_candidates_never_reach_ai(self):
        rows = [{"id": "expired", "slug": "expired", "status": "open",
                 "application_deadline_at": "2020-01-01T00:00:00+00:00"}]
        windows = [{"opportunity_id": "expired", "application_precision": "EXACT_TIME",
                    "application_cutoff_at": "2020-01-01T00:00:00+00:00"}]
        payload = api.RecommendationRequest(
            level="beginner", interest="AI", goal="learning"
        )
        with patch.dict(api.os.environ, {"OPENAI_API_KEY": "test-only"}), \
             patch.object(api, "create_supabase_client", return_value=_Client(rows, windows=windows)), \
             patch.object(api, "OpenAI") as ai_client:
            result = api.recommend_opportunities(payload)
        self.assertEqual(result["count"], 0)
        ai_client.assert_not_called()

    def test_zero_candidates_keep_response_contract_without_openai_key(self):
        rows = [{"id": "expired", "slug": "expired", "status": "open",
                 "application_deadline_at": "2020-01-01T00:00:00+00:00"}]
        payload = api.RecommendationRequest(
            level="beginner", interest="AI", goal="learning"
        )
        with patch.dict(api.os.environ, {}, clear=True), \
             patch.object(api, "create_supabase_client", return_value=_Client(rows)), \
             patch.object(api, "OpenAI") as ai_client:
            result = api.recommend_opportunities(payload)
        self.assertEqual(set(result), {"count", "recommendations", "message"})
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["recommendations"], [])
        ai_client.assert_not_called()

    def test_legacy_recommendation_discloses_uncertainty_and_keeps_shape(self):
        row, proof = _legacy_fixture()
        deadline = row["application_deadline_at"]
        row = {**row, "slug": "legacy", "title": "Legacy item",
               "event_end_at": "2026-10-13T00:00:00+00:00"}
        payload = api.RecommendationRequest(
            level="beginner", interest="AI", goal="learning"
        )
        class BeforeDeadline(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.fromisoformat(deadline) - timedelta(days=1)
        with patch.dict(api.os.environ, {"OPENAI_API_KEY": "test-only"}), \
             patch.object(api, "datetime", BeforeDeadline), \
             patch.object(api, "create_supabase_client",
                          return_value=_Client([row], proofs=[proof])), \
             patch.object(api, "OpenAI") as ai_client:
            ai_client.return_value.responses.parse.return_value.output_parsed = (
                api.AIRecommendationResult(recommendations=[
                    api.AIRecommendation(slug="legacy", reason="관심 분야와 맞습니다.")
                ])
            )
            result = api.recommend_opportunities(payload)
        self.assertEqual(set(result), {"count", "recommendations", "message"})
        self.assertEqual(result["count"], 1)
        self.assertEqual(set(result["recommendations"][0]), {"opportunity", "reason"})
        self.assertNotIn("updated_at", result["recommendations"][0]["opportunity"])
        self.assertNotIn("created_at", result["recommendations"][0]["opportunity"])
        self.assertNotIn("id", result["recommendations"][0]["opportunity"])
        self.assertNotIn("source_id", result["recommendations"][0]["opportunity"])
        self.assertNotIn("external_id", result["recommendations"][0]["opportunity"])
        self.assertNotIn("event_end_at", result["recommendations"][0]["opportunity"])
        self.assertNotIn("deadline_evidence", result["recommendations"][0]["opportunity"])
        self.assertIn(api.LEGACY_RECOMMENDATION_NOTICE,
                      result["recommendations"][0]["reason"])
        self.assertIn('"deadline_evidence": "legacy_unverified_future"',
                      ai_client.return_value.responses.parse.call_args.kwargs["input"])

    def test_collect_provenance_lookup_failure_excludes_legacy_without_ai(self):
        row, proof = _legacy_fixture()
        row = {**row, "slug": "legacy"}
        deadline = row["application_deadline_at"]
        class BeforeDeadline(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.fromisoformat(deadline) - timedelta(days=1)
        payload = api.RecommendationRequest(
            level="beginner", interest="AI", goal="learning"
        )
        with patch.dict(api.os.environ, {"OPENAI_API_KEY": "test-only"}), \
             patch.object(api, "datetime", BeforeDeadline), \
             patch.object(api, "create_supabase_client",
                          return_value=_Client([row], proofs=[proof],
                                               fail_proofs=True)), \
             patch.object(api, "OpenAI") as ai_client:
            result = api.recommend_opportunities(payload)
        self.assertEqual(result["count"], 0)
        ai_client.assert_not_called()

    def test_legacy_notice_keeps_reason_length_limit(self):
        reason = api._legacy_recommendation_reason("가" * 300)
        self.assertLessEqual(len(reason), 300)
        self.assertTrue(reason.endswith(api.LEGACY_RECOMMENDATION_NOTICE))

    def test_verified_recommendation_is_distinct_from_legacy(self):
        row = {"id": "new", "slug": "new", "status": "open",
               "title": "Verified item", "updated_at": "2026-10-12T00:00:00+00:00",
               "application_deadline_at": "2099-01-01T00:00:00+00:00"}
        window = {"opportunity_id": "new", "application_precision": "EXACT_TIME",
                  "application_cutoff_at": "2099-01-01T00:00:00+00:00"}
        payload = api.RecommendationRequest(
            level="beginner", interest="AI", goal="learning"
        )
        with patch.dict(api.os.environ, {"OPENAI_API_KEY": "test-only"}), \
             patch.object(api, "create_supabase_client",
                          return_value=_Client([row], windows=[window])), \
             patch.object(api, "OpenAI") as ai_client:
            ai_client.return_value.responses.parse.return_value.output_parsed = (
                api.AIRecommendationResult(recommendations=[
                    api.AIRecommendation(slug="new", reason="관심 분야와 맞습니다.")
                ])
            )
            result = api.recommend_opportunities(payload)
        self.assertEqual(result["recommendations"][0]["reason"],
                         "관심 분야와 맞습니다.")
        self.assertIn('"deadline_evidence": "verified"',
                      ai_client.return_value.responses.parse.call_args.kwargs["input"])

    def test_recommendation_rpc_failure_does_not_fall_back_to_raw_deadline(self):
        rows = [{"id": "future", "slug": "future", "status": "open",
                 "application_deadline_at": "2099-01-01T00:00:00+00:00"}]
        payload = api.RecommendationRequest(
            level="beginner", interest="AI", goal="learning"
        )
        with patch.dict(api.os.environ, {"OPENAI_API_KEY": "test-only"}), \
             patch.object(api, "create_supabase_client",
                          return_value=_Client(rows, fail_rpc=True)), \
             patch.object(api, "OpenAI") as ai_client:
            with self.assertRaises(HTTPException) as caught:
                api.recommend_opportunities(payload)
        self.assertEqual(caught.exception.status_code, 500)
        ai_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
