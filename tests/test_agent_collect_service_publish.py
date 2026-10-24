"""Offline PHASE 7 Service publication boundary tests; no network/DB access."""

import unittest
from unittest.mock import MagicMock
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from agent_collect.adapters.service_db.adapter import (
    SupabaseServiceDBAdapter, _json, publish_candidate_from_snapshot,
)
from agent_collect.adapters.service_db.supabase_tables import SupabaseServiceTables
from agent_collect.adapters.service_db.contract import PublishAction, ServicePublicationStatus
from agent_collect.adapters.service_db.mapping import (
    ConflictStatus, final_conflict, map_source, source_creation_eligible, stable_external_id,
    stable_slug,
)
from agent_collect.adapters.service_db.policy import (
    DateKind, DeterministicPublicationStatusResolver, TimezoneBasis,
    assess_temporal, derived_timezone,
)
from agent_collect.repositories.publish_ledger import QueueAuthorization
from agent_collect.schemas.models import (
    DuplicateMethod, DuplicateResult, PublishCandidate, Source, SourceType, Vertical,
)
from agent_collect.schemas.status import SourceStatus, VerificationStatus


NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


class AtomicServiceTableTests(unittest.TestCase):
    def test_partial_service_snapshot_is_rejected_before_duplicate_decision(self):
        client = MagicMock()
        response = client.table.return_value.select.return_value.limit.return_value.execute.return_value
        response.count = 2
        response.data = [{"id": str(uuid4()), "official_url": "https://example.org/one"}]
        with self.assertRaisesRegex(RuntimeError, "opportunities scan incomplete"):
            SupabaseServiceTables(client).list_opportunities()

    def test_sdk_uses_single_registration_rpc_and_no_direct_insert(self):
        client = MagicMock()
        record_id = uuid4()
        client.rpc.return_value.execute.return_value.data = [
            {"opportunity_id": str(record_id), "disposition": "registered"}]
        client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {"id": str(record_id), "status": "open"}]
        tables = SupabaseServiceTables(client)
        row, disposition = tables.register_opportunity(
            {"slug": "fixture"}, {"evidence_ref": "fixture"})
        self.assertEqual((row["id"], disposition), (str(record_id), "registered"))
        self.assertEqual(client.rpc.call_args.args[0],
                         "register_opportunity_with_evidence")
        client.table.return_value.insert.assert_not_called()


def candidate() -> PublishCandidate:
    item_id = uuid4()
    return PublishCandidate(
        contract_version=1, agent_item_id=item_id, agent_source_id=uuid4(), run_id=uuid4(),
        vertical=Vertical.CONFERENCE, title="AI Research Conference 2027",
        summary="Research talks on artificial intelligence.", organizer="AI Society",
        official_url="https://example.org/2027", source_name="AI Society",
        source_homepage_url="https://example.org", verified_at=NOW,
        verification_status=VerificationStatus.VERIFIED, confidence=0.96,
        duplicate_result=DuplicateResult(item_id, False, DuplicateMethod.NONE, 1),
        evidence_refs=("raw:B001", "raw:L000"),
        application_start_at=NOW-timedelta(days=5),
        application_deadline_at=NOW+timedelta(days=40),
        event_start_at=datetime(2027, 2, 16, tzinfo=timezone.utc),
        event_end_at=datetime(2027, 2, 17, tzinfo=timezone.utc),
        event_time_kind="DATETIME_CONFIRMED", timezone_basis="CONFIRMED",
        application_time_kind="DATETIME_CONFIRMED", application_timezone_basis="CONFIRMED",
    )


class FakeTables:
    def __init__(self):
        self.source_id, self.category_id = uuid4(), uuid4()
        self.sources = [{"id": str(self.source_id), "name": "AI Society",
                         "homepage_url": "https://example.org/", "is_active": True}]
        self.category = {"id": str(self.category_id), "slug": "conference", "is_active": True}
        self.tags = [{"id": str(uuid4()), "slug": "ai"}]
        self.opportunities = []
        self.insert_calls = 0
        self.raise_insert = False
        self.time_evidence = {}
        self.raise_evidence = False
        self.archive_ids = set()

    def list_sources(self): return list(self.sources)
    def get_conference_category(self): return self.category
    def list_tags(self, slugs): return [row for row in self.tags if row["slug"] in slugs]
    def list_opportunities(self): return list(self.opportunities)
    def insert_opportunity(self, payload):
        self.insert_calls += 1
        if self.raise_insert:
            raise RuntimeError("mock failure")
        row = {"id": str(uuid4()), **payload}
        self.opportunities.append(row)
        return row
    def register_opportunity(self, payload, evidence):
        if self.raise_evidence:
            raise RuntimeError("mock evidence RPC failure")
        row = self.insert_opportunity(payload)
        self.time_evidence[row["id"]] = evidence
        return row, "registered"
    def get_opportunity(self, record_id):
        return next((row for row in self.opportunities if row["id"] == str(record_id)), None)
    def register_time_evidence(self, record_id, evidence):
        if self.raise_evidence:
            raise RuntimeError("mock evidence RPC failure")
        self.time_evidence[str(record_id)] = evidence
    def public_archive_contains(self, record_id):
        return str(record_id) in self.archive_ids


class FakeLedger:
    def __init__(self, publication):
        self.publication = publication
        self.status = "READY"
        self.verification_result = "VERIFIED"
        self.gate_status = "PUBLISH_READY"
        self.snapshot = _json(asdict(publication))
        self.logs = []
        self.published = None
        self.service_opportunity_id = None
        self.failed = None
        self.claim_count = 0
        self.fail_mark_once = False

    def get(self, candidate_id):
        if candidate_id != self.publication.agent_item_id:
            return None
        return QueueAuthorization(candidate_id, uuid4(), self.status, self.snapshot,
                                  self.verification_result, self.gate_status,
                                  service_opportunity_id=self.service_opportunity_id,
                                  attempt_count=self.claim_count)
    def claim(self, candidate_id, *, prior_attempts):
        if self.status not in {"READY", "FAILED"}:
            return False
        self.status = "PUBLISHING"
        self.claim_count += 1
        return True
    def mark_published(self, candidate_id, **kwargs):
        if self.fail_mark_once:
            self.fail_mark_once = False
            raise RuntimeError("mock queue update failure")
        self.status = "PUBLISHED"
        self.service_opportunity_id = kwargs["service_id"]
        self.published = kwargs
    def mark_failed(self, candidate_id, *, code):
        self.status = "FAILED"
        self.failed = code
    def log(self, candidate_id, *, event_type, details):
        self.logs.append((event_type, details))


class TemporalTests(unittest.TestCase):
    def test_date_only_unknown_confirmed_and_timezone(self):
        item = candidate()
        self.assertTrue(assess_temporal(item).publishable)
        self.assertEqual(assess_temporal(replace(item, event_time_kind="DATE_ONLY",
                                                 event_start_at=None)).reason,
                         "DATE_ONLY_NOT_REPRESENTABLE")
        self.assertEqual(assess_temporal(replace(item, event_time_kind="UNKNOWN")).date_kind,
                         DateKind.UNKNOWN)
        self.assertFalse(assess_temporal(replace(item, timezone_basis="DERIVED")).publishable)
        self.assertFalse(assess_temporal(replace(item, timezone_basis="UNKNOWN")).publishable)
        self.assertEqual(assess_temporal(replace(item, application_time_kind="DATE_ONLY")).reason,
                         "APPLICATION_DATETIME_NOT_CONFIRMED")
        self.assertEqual(assess_temporal(replace(item, application_timezone_basis="DERIVED")).reason,
                         "APPLICATION_TIMEZONE_NOT_CONFIRMED")
        self.assertEqual(derived_timezone(city="Seoul", country="South Korea"),
                         ("Asia/Seoul", TimezoneBasis.DERIVED))
        self.assertEqual(derived_timezone(city="Unknown", country="Unknown"),
                         (None, TimezoneBasis.UNKNOWN))
        with self.assertRaises(ValueError):
            replace(item, event_start_at=datetime(2027, 2, 16))

    def test_publication_statuses_and_unknown(self):
        item = candidate()
        resolver = DeterministicPublicationStatusResolver()
        self.assertEqual(resolver.resolve(item, at=NOW), ServicePublicationStatus.OPEN)
        self.assertEqual(resolver.resolve(item, at=NOW-timedelta(days=10)),
                         ServicePublicationStatus.SCHEDULED)
        self.assertEqual(resolver.resolve(item, at=NOW+timedelta(days=60)),
                         ServicePublicationStatus.CLOSED)
        self.assertEqual(resolver.resolve(item, at=item.event_start_at),
                         ServicePublicationStatus.ONGOING)
        self.assertEqual(resolver.resolve(item, at=item.event_end_at+timedelta(days=1)),
                         ServicePublicationStatus.ENDED)
        no_registration = replace(item, application_start_at=None, application_deadline_at=None)
        self.assertIsNone(resolver.resolve(no_registration, at=NOW))


class MappingTests(unittest.TestCase):
    def test_source_exact_domain_name_and_missing(self):
        item, tables = candidate(), FakeTables()
        self.assertEqual(map_source(item, tables.sources)[1], "EXACT_HOMEPAGE")
        rows = [{**tables.sources[0], "homepage_url": "https://www.example.org/elsewhere"}]
        self.assertEqual(map_source(item, rows)[1], "DOMAIN_AND_NAME")
        self.assertIsNone(map_source(item, [])[0])

    def test_source_creation_eligibility_requires_verified_official_active(self):
        item = candidate()
        source = Source(item.agent_source_id, "AI Society", "https://example.org",
                        SourceType.OFFICIAL, SourceStatus.ACTIVE, NOW,
                        is_active=True, canonical_url="https://example.org", confidence=0.95)
        self.assertTrue(source_creation_eligible(item, source, threshold=0.85))
        self.assertFalse(source_creation_eligible(item, replace(source, status=SourceStatus.HOLD,
                                                                 is_active=False), threshold=0.85))
        self.assertFalse(source_creation_eligible(item, replace(source, source_type=SourceType.UNKNOWN),
                                                  threshold=0.85))

    def test_final_conflict_and_distinct_year(self):
        item, tables = candidate(), FakeTables()
        base = {"id": str(uuid4()), "source_id": str(tables.source_id), "slug": "other",
                "external_id": None, "title": "Other title", "organizer": "Other",
                "official_url": item.official_url,
                "event_start_at": item.event_start_at.isoformat()}
        self.assertEqual(final_conflict(item, [base], source_id=tables.source_id).status,
                         ConflictStatus.DUPLICATE)
        different_year = {**base, "event_start_at": "2028-02-16T00:00:00+00:00"}
        self.assertEqual(final_conflict(item, [different_year], source_id=tables.source_id).status,
                         ConflictStatus.CONFLICT)
        other_url = {**base, "official_url": "https://other.org", "title": item.title}
        self.assertEqual(final_conflict(item, [other_url], source_id=tables.source_id).reason,
                         "TITLE_AND_DATE")
        organizer_only = {**base, "official_url": "https://other.org", "title": "Different",
                          "organizer": item.organizer}
        self.assertEqual(final_conflict(item, [organizer_only], source_id=tables.source_id).status,
                         ConflictStatus.CONFLICT)
        self.assertEqual(final_conflict(item, [], source_id=tables.source_id).status,
                         ConflictStatus.INSERT_ALLOWED)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.item = candidate()
        self.tables = FakeTables()
        self.ledger = FakeLedger(self.item)
        self.adapter = SupabaseServiceDBAdapter(tables=self.tables, ledger=self.ledger,
                                                publish_enabled=True)

    def test_publish_disabled_by_default(self):
        adapter = SupabaseServiceDBAdapter(tables=self.tables, ledger=self.ledger)
        self.assertIn("PUBLISH_FEATURE_DISABLED", adapter.dry_run(self.item, at=NOW).reasons)
        self.assertEqual(adapter.publish(self.item).action, PublishAction.HELD)
        self.assertEqual(self.tables.insert_calls, 0)

    def test_snapshot_decode_and_dry_run_payload(self):
        decoded = publish_candidate_from_snapshot(self.ledger.snapshot)
        self.assertEqual(decoded, self.item)
        plan = self.adapter.dry_run(self.item, at=NOW)
        self.assertTrue(plan.eligible)
        self.assertEqual(plan.source_id, self.tables.source_id)
        self.assertEqual(plan.category_id, self.tables.category_id)
        self.assertEqual(plan.publication_status, ServicePublicationStatus.OPEN)
        self.assertEqual(plan.conflict.status, ConflictStatus.INSERT_ALLOWED)
        self.assertEqual(plan.insert_payload["external_id"], stable_external_id(self.item))
        self.assertIn("runtime_agent_publish_smoke_test", plan.insert_payload["external_id"])
        self.assertEqual(self.tables.insert_calls, 0)

    def test_missing_source_category_queue_or_snapshot_fail_closed(self):
        self.tables.sources = []
        self.assertFalse(self.adapter.dry_run(self.item, at=NOW).eligible)
        self.tables.sources = FakeTables().sources
        self.tables.category = None
        self.assertFalse(self.adapter.dry_run(self.item, at=NOW).eligible)
        self.tables.category = {"id": str(uuid4()), "slug": "conference", "is_active": True}
        self.ledger.status = "HELD"
        self.assertFalse(self.adapter.dry_run(self.item, at=NOW).eligible)
        self.ledger.status = "READY"
        self.ledger.snapshot = {"tampered": True}
        self.assertFalse(self.adapter.dry_run(self.item, at=NOW).eligible)

    def test_existing_tag_match_unknown_tag_omitted_no_tag_write(self):
        item = replace(self.item, tag_slugs=("ai", "unknown-tag"))
        self.ledger.snapshot = _json(asdict(item))
        plan = self.adapter.dry_run(item, at=NOW)
        self.assertTrue(plan.eligible)
        self.assertEqual(len(plan.matched_tag_ids), 1)
        self.assertIn("tag:unknown-tag", plan.omitted_fields)
        self.assertIn("tag_link:ai", plan.omitted_fields)

    def test_date_only_and_derived_timezone_dry_run_never_insert(self):
        date_only = replace(self.item, event_time_kind="DATE_ONLY",
                            event_start_at=None, event_end_at=None)
        self.ledger.snapshot = _json(asdict(date_only))
        plan = self.adapter.dry_run(date_only, at=NOW)
        self.assertIn("DATE_ONLY_NOT_REPRESENTABLE", plan.reasons)
        self.assertFalse(plan.eligible)
        self.assertEqual(self.adapter.publish(date_only).action, PublishAction.HELD)
        derived = replace(self.item, timezone_basis="DERIVED")
        self.ledger.snapshot = _json(asdict(derived))
        self.assertIn("EVENT_TIMEZONE_NOT_CONFIRMED",
                      self.adapter.dry_run(derived, at=NOW).reasons)
        self.assertEqual(self.tables.insert_calls, 0)

    def test_actual_publish_success_readback_and_idempotent_rerun(self):
        first = self.adapter.publish(self.item)
        self.assertEqual(first.action, PublishAction.CREATED)
        self.assertEqual(first.service_record_id, UUID(self.tables.opportunities[0]["id"]))
        self.assertEqual(self.tables.insert_calls, 1)
        evidence = self.tables.time_evidence[str(first.service_record_id)]
        self.assertEqual(evidence["event_start"]["precision"], "EXACT_TIME")
        self.assertEqual(evidence["application_deadline"]["precision"], "EXACT_TIME")
        self.assertEqual(self.ledger.status, "PUBLISHED")
        self.assertTrue(any(event == "SERVICE_PUBLISHED" for event, _ in self.ledger.logs))
        second = self.adapter.publish(self.item)
        self.assertEqual(second.action, PublishAction.SKIPPED_DUPLICATE)
        self.assertEqual(self.tables.insert_calls, 1)

    def test_cron_transition_between_commit_and_readback_requires_provenance(self):
        original_register = self.tables.register_opportunity
        def concurrent_cron(payload, evidence):
            row, disposition = original_register(payload, evidence)
            row["status"] = "closed"
            self.tables.archive_ids.add(row["id"])
            return row, disposition
        self.tables.register_opportunity = concurrent_cron
        result = self.adapter.publish(self.item)
        self.assertEqual(result.action, PublishAction.CREATED)
        self.assertEqual(result.published_status, ServicePublicationStatus.CLOSED)

    def test_unproven_closed_readback_is_rejected(self):
        original_register = self.tables.register_opportunity
        def unproven_status(payload, evidence):
            row, disposition = original_register(payload, evidence)
            row["status"] = "closed"
            return row, disposition
        self.tables.register_opportunity = unproven_status
        result = self.adapter.publish(self.item)
        self.assertEqual(result.action, PublishAction.FAILED)

    def test_evidence_rpc_failure_retries_without_second_service_insert(self):
        self.tables.raise_evidence = True
        first = self.adapter.publish(self.item)
        self.assertEqual(first.action, PublishAction.FAILED)
        self.assertEqual(self.tables.insert_calls, 0)
        self.assertEqual(self.tables.time_evidence, {})
        self.tables.raise_evidence = False
        second = self.adapter.publish(self.item)
        self.assertEqual(second.action, PublishAction.CREATED)
        self.assertEqual(self.tables.insert_calls, 1)
        self.assertEqual(len(self.tables.time_evidence), 1)

    def test_queue_update_failure_reconciles_without_second_service_insert(self):
        self.ledger.fail_mark_once = True
        result = self.adapter.publish(self.item)
        self.assertEqual(result.action, PublishAction.CREATED)
        self.assertEqual(self.tables.insert_calls, 1)
        self.assertEqual(self.ledger.status, "PUBLISHED")

    def test_published_rerun_requires_matching_queue_id_and_readback(self):
        self.adapter.publish(self.item)
        self.ledger.service_opportunity_id = uuid4()
        self.assertEqual(self.adapter.publish(self.item).action, PublishAction.FAILED)
        self.ledger.service_opportunity_id = UUID(self.tables.opportunities[0]["id"])
        self.tables.opportunities[0]["title"] = "Tampered title"
        self.assertEqual(self.adapter.publish(self.item).action, PublishAction.FAILED)
        self.assertEqual(self.tables.insert_calls, 1)

    def test_publish_failure_has_no_false_success(self):
        self.tables.raise_insert = True
        result = self.adapter.publish(self.item)
        self.assertEqual(result.action, PublishAction.FAILED)
        self.assertEqual(self.ledger.status, "FAILED")
        self.assertEqual(self.tables.insert_calls, 1)
        self.assertEqual(len(self.tables.opportunities), 0)

    def test_duplicate_conflict_prevents_insert(self):
        self.tables.opportunities.append({
            "id": str(uuid4()), "source_id": str(self.tables.source_id),
            "external_id": None, "slug": "existing", "title": "Existing",
            "organizer": "Other", "official_url": self.item.official_url,
            "event_start_at": self.item.event_start_at.isoformat(), "status": "open",
        })
        plan = self.adapter.dry_run(self.item, at=NOW)
        self.assertEqual(plan.conflict.status, ConflictStatus.DUPLICATE)
        self.assertFalse(plan.eligible)
        result = self.adapter.publish(self.item)
        self.assertEqual(result.action, PublishAction.SKIPPED_DUPLICATE)
        self.assertEqual(self.tables.insert_calls, 0)


if __name__ == "__main__":
    unittest.main()
