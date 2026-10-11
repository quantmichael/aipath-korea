from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from opportunity_status.evidence import build_agent_evidence, build_collect_evidence
from opportunity_status.simulation import target_status, transition_decision


class TimeEvidenceTests(unittest.TestCase):
    def test_bizinfo_raw_calendar_date_is_policy_date_not_exact_time(self):
        candidate = {
            "id": "candidate-1",
            "application_start_at": "2026-09-08T00:00:00+00:00",
            "application_deadline_at": "2026-10-06T00:00:00+00:00",
            "raw_payload": {
                "collector": "api", "row": {"reqstBeginEndDe": "2026-09-08 ~ 2026-10-06"},
            },
        }
        evidence = build_collect_evidence(candidate)
        self.assertEqual(evidence["application_deadline"], {
            "precision": "DATE_ONLY", "source_date": "2026-10-06", "source_ref": "api"
        })
        self.assertEqual(evidence["application_start"]["precision"], "DATE_ONLY")
        row = {"status": "open", "application_start_at": candidate["application_start_at"],
               "application_deadline_at": candidate["application_deadline_at"]}
        before = datetime(2026, 10, 6, 14, 59, 59, tzinfo=timezone.utc)
        after = datetime(2026, 10, 6, 15, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(target_status(row, evidence, before), "open")
        self.assertEqual(transition_decision(row, evidence, after),
                         ("closed", True, "SOURCE_EVIDENCE_OR_KST_POLICY"))

    def test_mismatched_source_date_and_unproven_clock_are_unknown(self):
        row = {"id": "c", "application_deadline_at": "2026-10-06T00:00:00+00:00",
               "raw_payload": {"collector": "api", "row": {"reqstBeginEndDe": "2026-10-07"}}}
        self.assertEqual(build_collect_evidence(row)["application_deadline"]["precision"], "UNKNOWN")
        row["raw_payload"] = {"collector": "manual_web_review"}
        self.assertEqual(build_collect_evidence(row)["application_deadline"]["precision"], "UNKNOWN")
        row["raw_payload"]["status_time_evidence"] = {
            "application_deadline_at": {
                "source_literal": "2026-10-06T09:00:00", "source_ref": "official-page"
            }
        }
        self.assertEqual(build_collect_evidence(row)["application_deadline"]["precision"], "UNKNOWN")

    def test_explicit_offset_and_matching_instant_is_exact(self):
        candidate = {"id": "c", "application_deadline_at": "2026-10-06T01:00:00+00:00",
                     "raw_payload": {"status_time_evidence": {
                         "application_deadline_at": {
                             "source_literal": "2026-10-06T10:00:00+09:00",
                             "source_ref": "official-page#deadline",
                         }
                     }}}
        detail = build_collect_evidence(candidate)["application_deadline"]
        self.assertEqual(detail["precision"], "EXACT_TIME")
        self.assertEqual(detail["source_literal"], "2026-10-06T10:00:00+09:00")

    def test_event_end_only_records_registration_hold_reason(self):
        candidate = {"id": "c", "event_end_at": "2026-10-06T05:00:00+00:00",
                     "raw_payload": {"status_time_evidence": {"event_end_at": {
                         "source_literal": "2026-10-06T14:00:00+09:00",
                         "source_ref": "official-page#event-end"}}}}
        evidence = build_collect_evidence(candidate)
        self.assertEqual(evidence["event_end"]["precision"], "EXACT_TIME")
        self.assertEqual(evidence["hold_reason"],
                         "REGISTRATION_DEADLINE_UNKNOWN_EVENT_END_ONLY")

    def test_nipa_discarded_clock_is_unknown_not_date_only(self):
        candidate = {"id": "c", "application_deadline_at": "2026-10-10T00:00:00+00:00",
                     "event_start_at": "2026-10-28T17:00:00+00:00",
                     "raw_payload": {"detail_collector": "nipa_detail",
                                     "status_date_evidence": {"application_deadline_at": "2026-10-10"}}}
        evidence = build_collect_evidence(candidate)
        self.assertEqual(evidence["application_deadline"]["precision"], "UNKNOWN")
        self.assertEqual(evidence["event_start"]["precision"], "UNKNOWN")

    def test_dacon_requires_extracted_field_for_date_only(self):
        candidate = {"id": "dacon-1", "event_end_at": "2026-10-06T00:00:00+00:00",
                     "raw_payload": {"detail_collector": "dacon_detail",
                                     "status_date_evidence": {"event_start_at": "2026-10-05"}}}
        self.assertEqual(build_collect_evidence(candidate)["event_end"]["precision"], "UNKNOWN")

    def test_dacon_direct_dates_exclude_clock_and_event_end_fallback(self):
        from collect.adapters.dacon import _direct_schedule_dates, _extract_schedule
        lines = ["10.06", "대회 종료", "10.07", "참가 신청 마감 18:00", "10.08", "대회 시작"]
        stored = _extract_schedule(lines)
        evidence = _direct_schedule_dates(lines)
        self.assertIn("application_deadline_at", stored)
        self.assertNotIn("application_deadline_at", evidence)
        self.assertEqual(evidence["event_start_at"], f"{datetime.now(timezone.utc).year}-10-08")

    def test_nipa_adapter_preserves_clock_without_inventing_offset(self):
        from collect.adapters.nipa import NipaAdapter
        from collect.models import OpportunityCandidate
        from unittest.mock import patch
        candidate = OpportunityCandidate(
            source_id="nipa", title="Test", slug="test",
            official_url="https://www.nipa.kr/home/2-2/16953",
        )
        with patch("collect.adapters.nipa._fetch_detail_text",
                   return_value="신청기간 : 2026-09-29 09:00 ~ 2026-10-10 23:00"):
            enriched = NipaAdapter().enrich_candidate(candidate)
        literal = enriched.raw_payload["status_time_evidence"]["application_deadline_at"]["source_literal"]
        self.assertEqual(literal, "2026-10-10T23:00:00")
        self.assertEqual(build_collect_evidence({"id": "nipa-1",
            "application_deadline_at": "2026-10-10T00:00:00+00:00",
            "raw_payload": enriched.raw_payload})["application_deadline"]["precision"], "UNKNOWN")

    def test_agent_uses_verified_temporal_metadata_only(self):
        candidate = SimpleNamespace(
            agent_item_id="agent-item", evidence_refs=("raw:001",),
            application_start_at=None,
            application_deadline_at=datetime(2026, 10, 6, 1, tzinfo=timezone.utc),
            event_start_at=datetime(2026, 10, 8, 1, tzinfo=timezone.utc),
            event_end_at=datetime(2026, 10, 9, 1, tzinfo=timezone.utc),
            application_time_kind="DATETIME_CONFIRMED",
            application_timezone_basis="CONFIRMED",
            event_time_kind="DATETIME_CONFIRMED", timezone_basis="CONFIRMED",
        )
        evidence = build_agent_evidence(candidate)
        self.assertEqual(evidence["application_deadline"]["precision"], "EXACT_TIME")
        self.assertEqual(evidence["event_end"]["precision"], "EXACT_TIME")
        candidate.timezone_basis = "DERIVED"
        self.assertEqual(build_agent_evidence(candidate)["event_end"]["precision"], "UNKNOWN")

    def test_legacy_closed_and_backward_transition_stay_protected(self):
        row = {"status": "closed", "application_deadline_at": "2026-10-10T00:00:00+00:00",
               "event_start_at": "2026-10-10T12:00:00+00:00",
               "event_end_at": "2026-10-12T00:00:00+00:00"}
        evidence = {"application_deadline": {"precision": "EXACT_TIME",
                  "source_literal": "2026-10-10T00:00:00+00:00"},
                  "event_start": {"precision": "EXACT_TIME",
                  "source_literal": "2026-10-10T12:00:00+00:00"},
                  "event_end": {"precision": "EXACT_TIME",
                  "source_literal": "2026-10-12T00:00:00+00:00"}}
        now = datetime(2026, 10, 11, tzinfo=timezone.utc)
        self.assertEqual(transition_decision(row, evidence, now),
                         ("ongoing", False, "LEGACY_CLOSED_PROVENANCE_UNKNOWN"))
        row["status"] = "ongoing"
        self.assertEqual(transition_decision(row, evidence, now),
                         ("ongoing", False, "ALREADY_CURRENT"))


class CollectPromotionEvidenceTests(unittest.TestCase):
    def test_existing_public_row_is_never_written_by_draft_upsert(self):
        from collect import repository
        original = {"id": "opportunity-1", "status": "open", "title": "Published title"}
        client = MagicMock()
        with patch.object(repository, "find_existing_opportunity_for_record",
                          return_value=original):
            result = repository.upsert_draft_opportunity(client, {"title": "Changed title"})
        self.assertEqual(result, ("opportunity-1", "protected_existing"))
        self.assertEqual(original, {"id": "opportunity-1", "status": "open",
                                    "title": "Published title"})
        client.table.assert_not_called()

    def test_draft_update_requires_status_still_draft(self):
        from collect import repository
        client = MagicMock()
        client.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        with patch.object(repository, "find_existing_opportunity_for_record",
                          return_value={"id": "opportunity-1", "status": "draft"}):
            result = repository.upsert_draft_opportunity(client, {"title": "Changed title"})
        self.assertEqual(result, ("opportunity-1", "protected_existing"))
        guard = client.table.return_value.update.return_value.eq.return_value.eq
        guard.assert_called_once_with("status", "draft")

    def test_new_draft_creation_is_preserved(self):
        from collect import repository
        client = MagicMock()
        client.table.return_value.insert.return_value.execute.return_value.data = [
            {"id": "new-opportunity"}]
        with patch.object(repository, "find_existing_opportunity_for_record", return_value=None):
            result = repository.upsert_draft_opportunity(client, {"title": "New title"})
        self.assertEqual(result, ("new-opportunity", "inserted"))
        payload = client.table.return_value.insert.call_args.args[0]
        self.assertEqual(payload["status"], "draft")

    def test_admin_publish_requires_row_still_draft(self):
        from collect import repository
        client = MagicMock()
        client.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        with self.assertRaisesRegex(RuntimeError, "no longer draft"):
            repository.publish_opportunity(client, "opportunity-1")
        guard = client.table.return_value.update.return_value.eq.return_value.eq
        guard.assert_called_once_with("status", "draft")

    def test_protected_reprocessing_skips_evidence_rpc(self):
        from collect import promote
        candidate = {"id": "candidate-1", "source_id": "source-1", "category_id": "category-1",
                     "title": "Test", "slug": "test", "official_url": "https://example.org/test"}
        client = MagicMock()
        with patch.object(promote, "create_supabase_client", return_value=client), \
             patch.object(promote, "fetch_promotable_candidates", return_value=[candidate]), \
             patch.object(promote, "upsert_draft_opportunity",
                          return_value=("opportunity-1", "protected_existing")), \
             patch.object(promote, "mark_candidate_promoted") as mark:
            result = promote.promote_candidates()
        self.assertEqual(result.skipped_count, 1)
        self.assertEqual(result.failed_count, 0)
        client.rpc.assert_not_called()
        mark.assert_not_called()

    def test_public_row_and_data_survive_evidence_rpc_outage(self):
        from collect import promote, repository
        original = {"id": "opportunity-1", "status": "open", "title": "Published title",
                    "application_deadline_at": "2026-10-10T00:00:00+00:00"}
        candidate = {"id": "candidate-1", "source_id": "source-1", "category_id": "category-1",
                     "title": "Changed title", "slug": "test",
                     "official_url": "https://example.org/test"}
        client = MagicMock()
        client.rpc.side_effect = RuntimeError("RPC unavailable")
        with patch.object(promote, "create_supabase_client", return_value=client), \
             patch.object(promote, "fetch_promotable_candidates", return_value=[candidate]), \
             patch.object(repository, "find_existing_opportunity_for_record",
                          return_value=original), \
             patch.object(promote, "mark_candidate_promoted") as mark:
            result = promote.promote_candidates()
        self.assertEqual(result.skipped_count, 1)
        self.assertEqual(result.failed_count, 0)
        self.assertEqual(original["status"], "open")
        self.assertEqual(original["title"], "Published title")
        client.table.assert_not_called()
        client.rpc.assert_not_called()
        mark.assert_not_called()

    def test_promotion_registers_evidence_before_marking_candidate(self):
        from collect import promote
        candidate = {"id": "candidate-1", "source_id": "source-1", "category_id": "category-1",
                     "title": "Test", "slug": "test", "official_url": "https://example.org/test",
                     "application_deadline_at": "2026-10-06T00:00:00+00:00",
                     "raw_payload": {"collector": "api", "row": {
                         "reqstBeginEndDe": "2026-09-08 ~ 2026-10-06"}}}
        client = MagicMock()
        calls = []
        client.rpc.side_effect = lambda name, args: (calls.append((name, args)) or client)
        with patch.object(promote, "create_supabase_client", return_value=client), \
             patch.object(promote, "fetch_promotable_candidates", return_value=[candidate]), \
             patch.object(promote, "upsert_draft_opportunity", return_value=("opportunity-1", "inserted")), \
             patch.object(promote, "mark_candidate_promoted", side_effect=lambda *_: calls.append("promoted")):
            result = promote.promote_candidates()
        self.assertEqual(result.promoted_count, 1)
        self.assertEqual(calls[0][0], "record_opportunity_status_evidence")
        self.assertEqual(calls[0][1]["p_evidence"]["application_deadline"]["precision"], "DATE_ONLY")
        self.assertEqual(calls[1], "promoted")

    def test_rpc_failure_leaves_candidate_unpromoted(self):
        from collect import promote
        candidate = {"id": "candidate-1", "source_id": "source-1", "category_id": "category-1",
                     "title": "Test", "slug": "test", "official_url": "https://example.org/test"}
        client = MagicMock()
        client.rpc.return_value.execute.side_effect = RuntimeError("RPC unavailable")
        with patch.object(promote, "create_supabase_client", return_value=client), \
             patch.object(promote, "fetch_promotable_candidates", return_value=[candidate]), \
             patch.object(promote, "upsert_draft_opportunity", return_value=("opportunity-1", "inserted")), \
             patch.object(promote, "mark_candidate_promoted") as mark:
            result = promote.promote_candidates()
        self.assertEqual(result.failed_count, 1)
        mark.assert_not_called()

    def test_existing_admin_publish_records_evidence_before_open(self):
        from collect import admin
        candidate = {"id": "candidate-1", "source_id": "source-1", "category_id": "category-1",
                     "title": "Test", "slug": "test", "official_url": "https://example.org/test",
                     "application_deadline_at": "2026-10-06T00:00:00+00:00",
                     "raw_payload": {"collector": "api", "row": {
                         "reqstBeginEndDe": "2026-09-08 ~ 2026-10-06"}}}
        client = MagicMock()
        calls = []
        client.rpc.side_effect = lambda name, args: (calls.append((name, args)) or client)
        with patch.object(admin, "fetch_candidates_by_ids", return_value=[candidate]), \
             patch.object(admin, "upsert_draft_opportunity", return_value=("opportunity-1", "inserted")), \
             patch.object(admin, "publish_opportunity", side_effect=lambda *_: calls.append("published")), \
             patch.object(admin, "mark_candidate_promoted", side_effect=lambda *_: calls.append("promoted")):
            result = admin._publish_candidates(client, ["candidate-1"])
        self.assertEqual(result.promoted_count, 1)
        self.assertEqual(calls[0][0], "record_opportunity_status_evidence")
        self.assertEqual(calls[1:], ["published", "promoted"])

    def test_admin_publish_rpc_failure_keeps_draft(self):
        from collect import admin
        candidate = {"id": "candidate-1", "source_id": "source-1", "category_id": "category-1",
                     "title": "Test", "slug": "test", "official_url": "https://example.org/test"}
        client = MagicMock()
        client.rpc.return_value.execute.side_effect = RuntimeError("RPC unavailable")
        with patch.object(admin, "fetch_candidates_by_ids", return_value=[candidate]), \
             patch.object(admin, "upsert_draft_opportunity", return_value=("opportunity-1", "inserted")), \
             patch.object(admin, "publish_opportunity") as publish:
            result = admin._publish_candidates(client, ["candidate-1"])
        self.assertEqual(result.failed_count, 1)
        publish.assert_not_called()

    def test_admin_reprocessing_never_reopens_existing_closed_row(self):
        from collect import admin
        candidate = {"id": "candidate-1", "source_id": "source-1", "category_id": "category-1",
                     "title": "Test", "slug": "test", "official_url": "https://example.org/test"}
        client = MagicMock()
        with patch.object(admin, "fetch_candidates_by_ids", return_value=[candidate]), \
             patch.object(admin, "upsert_draft_opportunity",
                          return_value=("opportunity-1", "protected_existing")), \
             patch.object(admin, "publish_opportunity") as publish:
            result = admin._publish_candidates(client, ["candidate-1"])
        self.assertEqual(result.skipped_count, 1)
        client.rpc.assert_not_called()
        publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
