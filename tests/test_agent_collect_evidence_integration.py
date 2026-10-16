"""PHASE 12J FK-backed evidence join without network or database writes."""

import unittest
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from agent_collect.discovery.evidence import EvidenceState
from agent_collect.discovery.evidence_supabase import (
    SupabaseEvidenceIntegration, SupabasePreGateEvidence, integration_metrics,
)
from agent_collect.extraction.schema import ExtractionStatus
from tests.test_agent_collect_official_evidence import EvidenceFixture, NOW


class SelectOnlyQuery:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, field, value):
        self.rows = [row for row in self.rows if row.get(field) == value]
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class SelectOnlyClient:
    def __init__(self, tables):
        self.tables = tables
        self.reads = []

    def table(self, name):
        self.reads.append(name)
        return SelectOnlyQuery(list(self.tables.get(name, ())))


class ReadOnlyStorage:
    def __init__(self, content):
        self.content = content
        self.downloads = 0

    def get(self, _ref):
        self.downloads += 1
        return self.content


def sdk_fixture():
    fixture = EvidenceFixture()
    job_id, verification_id, link_id = uuid4(), uuid4(), uuid4()
    source = replace(fixture.source, provenance={
        **fixture.source.provenance,
        "search_result_id": str(fixture.result.id),
        "source_candidate_id": str(link_id),
    })
    tables = {
        "agent_search_results": [{
            "id": str(fixture.result.id), "query_id": str(fixture.result.query_id),
            "provider": fixture.result.provider, "rank": fixture.result.rank,
            "title": fixture.result.title, "original_url": fixture.result.original_url,
            "snippet": fixture.result.snippet, "canonical_url": fixture.result.canonical_url,
            "domain": fixture.result.domain, "discovered_at": NOW.isoformat(),
            "status": fixture.result.status.value, "filter_reasons": [],
            "provider_metadata": {},
        }],
        "agent_source_candidates": [{
            "id": str(link_id), "query_id": str(fixture.result.query_id),
            "search_result_id": str(fixture.result.id), "status": "PROMOTED",
            "promoted_source_id": str(source.id),
        }],
        "agent_sources": [{
            "id": str(source.id), "name": source.name, "source_url": source.url,
            "canonical_url": source.canonical_url, "source_type": source.source_type.value,
            "status": source.status.value, "is_active": source.is_active,
            "collection_method": source.collection_method.value,
            "confidence": source.confidence, "provenance": source.provenance,
            "collection_config": source.collection_config,
            "discovered_at": source.discovered_at.isoformat(),
        }],
        "agent_raw_documents": [{
            "id": str(fixture.raw.id), "run_id": str(fixture.run.id),
            "source_id": str(source.id), "source_url": fixture.raw.source_url,
            "canonical_url": fixture.raw.canonical_url,
            "content_ref": fixture.raw.content_ref,
            "content_sha256": fixture.raw.content_sha256,
            "media_type": fixture.raw.media_type, "http_status": fixture.raw.http_status,
            "collected_at": fixture.raw.collected_at.isoformat(),
            "provenance": fixture.raw.provenance,
        }],
        "agent_collection_runs": [{
            "id": str(fixture.run.id), "source_id": str(source.id),
            "status": fixture.run.status.value,
            "started_at": fixture.run.started_at.isoformat(),
            "finished_at": fixture.run.finished_at.isoformat(),
        }],
        "agent_processing_jobs": [{
            "id": str(job_id), "raw_document_id": str(fixture.raw.id),
            "candidate_id": str(fixture.extraction.id),
            "verification_id": str(verification_id),
            "status": "SUCCEEDED", "gate_status": "PUBLISH_READY",
        }],
        "agent_candidates": [{
            "id": str(fixture.extraction.id), "raw_document_id": str(fixture.raw.id),
            "run_id": str(fixture.run.id), "source_id": str(source.id),
            "source_url": fixture.raw.source_url, "item_fingerprint": "offline-fingerprint",
            "status": "EXTRACTED", "extracted_payload": fixture.extraction.payload,
            "provenance": {"extraction_status": ExtractionStatus.EXTRACTED.value},
        }],
        "agent_verifications": [{
            "id": str(verification_id), "candidate_id": str(fixture.extraction.id),
            "result": "VERIFIED", "confidence": .95, "reasons": [],
            "checks": {"gate_decision": {"status": "PUBLISH_READY"}},
            "evidence_refs": list(fixture.verification.evidence_refs),
            "verified_at": NOW.isoformat(),
        }],
        "agent_publish_queue": [{
            "candidate_id": str(fixture.extraction.id),
            "verification_id": str(verification_id), "status": "READY",
        }],
    }
    return fixture, job_id, SelectOnlyClient(tables), ReadOnlyStorage(fixture.html)


class IntegrationTests(unittest.TestCase):
    def test_pre_gate_uses_reviewed_source_and_immutable_approval_audit(self):
        class FixtureTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

        fixture, _, client, storage = sdk_fixture()
        approval = fixture.source.provenance["source_review"]["approval"]
        client.tables["agent_source_review_events"] = [{
            "id": str(uuid4()), "source_id": str(fixture.source.id),
            "review_decision": "APPROVED", "officiality_confirmed": True,
            "collection_allowed": True, "reviewed_at": approval["reviewed_at"],
        }]
        gate = SupabasePreGateEvidence(
            client, storage, reviews={fixture.source.id: fixture.review},
            access={fixture.source.id: fixture.access})
        with patch("agent_collect.discovery.evidence_supabase.datetime", FixtureTime):
            self.assertEqual(gate(fixture.extraction, fixture.verification).state,
                             EvidenceState.VERIFIED)
            client.tables["agent_source_review_events"] = []
            with self.assertRaises(ValueError):
                gate(fixture.extraction, fixture.verification)

    def test_empty_cohort_metrics_have_safe_denominators(self):
        metrics = integration_metrics((), cohort="synthetic")
        self.assertEqual((metrics.evidence_resolution_rate,
                          metrics.verified_opportunity_yield,
                          metrics.publish_ready_conversion_rate), (0, 0, 0))

    def run_fixture(self, fixture, job_id, client, storage, **changes):
        return SupabaseEvidenceIntegration(client, storage).assess(
            fixture.result.id, fixture.raw.id, processing_job_id=job_id,
            review=fixture.review, access=fixture.access, now=NOW,
            cohort="synthetic", **changes)

    def test_fk_join_existing_verification_and_queue_without_write(self):
        fixture, job_id, client, storage = sdk_fixture()
        outcome = self.run_fixture(fixture, job_id, client, storage)
        self.assertEqual(outcome.evidence.state, EvidenceState.VERIFIED)
        self.assertTrue(outcome.publish_ready)
        self.assertEqual(outcome.queue_status, "READY")
        self.assertEqual(storage.downloads, 1)
        self.assertEqual(outcome.issues, ())
        metrics = integration_metrics((outcome,), cohort="synthetic")
        self.assertEqual(metrics.publish_ready_count, 1)
        self.assertEqual(metrics.publish_ready_conversion_rate, 1)
        with self.assertRaises(ValueError):
            integration_metrics((outcome,), cohort="operational")

    def test_unauthorized_source_never_reads_storage(self):
        fixture, job_id, client, storage = sdk_fixture()
        blocked = replace(fixture.access, explicit_collection_permission=False)
        outcome = SupabaseEvidenceIntegration(client, storage).assess(
            fixture.result.id, fixture.raw.id, processing_job_id=job_id,
            review=fixture.review, access=blocked, now=NOW, cohort="synthetic")
        self.assertEqual(storage.downloads, 0)
        self.assertEqual(outcome.evidence.state, EvidenceState.REVIEW_REQUIRED)
        self.assertFalse(outcome.publish_ready)

    def test_unrelated_official_review_never_reads_storage(self):
        fixture, job_id, client, storage = sdk_fixture()
        outcome = SupabaseEvidenceIntegration(client, storage).assess(
            fixture.result.id, fixture.raw.id, processing_job_id=job_id,
            review=replace(fixture.review, source_id=uuid4()), access=fixture.access,
            now=NOW, cohort="synthetic")
        self.assertEqual(storage.downloads, 0)
        self.assertIn("official_review_or_access_snapshot_missing", outcome.issues)

    def test_search_only_or_mismatched_fk_never_guesses_source(self):
        fixture, job_id, client, storage = sdk_fixture()
        client.tables["agent_source_candidates"] = []
        outcome = self.run_fixture(fixture, job_id, client, storage)
        self.assertEqual(outcome.evidence.state, EvidenceState.REVIEW_REQUIRED)
        self.assertIn("promoted_source_link_missing", outcome.issues)
        self.assertEqual(storage.downloads, 0)

    def test_existing_review_required_candidate_is_not_promoted(self):
        fixture, job_id, client, storage = sdk_fixture()
        client.tables["agent_candidates"][0]["status"] = "REVIEW_REQUIRED"
        outcome = self.run_fixture(fixture, job_id, client, storage)
        self.assertFalse(outcome.publish_ready)
        self.assertIn("candidate_review_required_protected", outcome.issues)
        self.assertEqual(integration_metrics((outcome,), cohort="synthetic")
                         .review_required_count, 1)

    def test_gate_disagreement_blocks_publish_ready_conversion(self):
        fixture, job_id, client, storage = sdk_fixture()
        client.tables["agent_verifications"][0]["checks"]["gate_decision"]["status"] = "REVIEW_REQUIRED"
        outcome = self.run_fixture(fixture, job_id, client, storage)
        self.assertFalse(outcome.publish_ready)
        self.assertIn("gate_decision_mismatch", outcome.issues)

    def test_storage_sha_mismatch_blocks_resolved_evidence_and_queue_conversion(self):
        fixture, job_id, client, storage = sdk_fixture()
        storage.content = b"changed synthetic content"
        outcome = self.run_fixture(fixture, job_id, client, storage)
        self.assertIsNone(outcome.evidence.resolution)
        self.assertFalse(outcome.publish_ready)

    def test_processing_job_must_identify_the_same_raw_document(self):
        fixture, job_id, client, storage = sdk_fixture()
        client.tables["agent_processing_jobs"][0]["raw_document_id"] = str(uuid4())
        outcome = self.run_fixture(fixture, job_id, client, storage)
        self.assertFalse(outcome.publish_ready)
        self.assertIn("processing_job_raw_mismatch", outcome.issues)


if __name__ == "__main__":
    unittest.main()
