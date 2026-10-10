"""Offline PHASE 6 quality rules and read-only boundary tests."""

import unittest
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from agent_collect.adapters.service_db.duplicate_lookup import SupabaseServiceDuplicateLookup
from agent_collect.config.settings import load_settings
from agent_collect.dedup.engine import DeduplicationEngine, candidate_record, normalize_title
from agent_collect.extraction.schema import ExtractionStatus, StoredExtraction
from agent_collect.quality_gate.gate import PublishGate
from agent_collect.quality_gate.models import (
    DuplicateRecord, DuplicateStatus, GateStatus, QualityCandidate, ReviewReason,
)
from agent_collect.quality_gate.runner import QualityGateRunner
from agent_collect.repositories.memory import InMemoryRawDocumentRepository, InMemorySourceRepository
from agent_collect.repositories.quality import InMemoryQualityRepository
from agent_collect.schemas.models import RawDocument, Source, SourceType
from agent_collect.schemas.status import SourceStatus, VerificationStatus
from agent_collect.verification.engine import VerificationEngine


NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


def fixture(*, conference=True, confidence=0.95):
    fields = {}
    for name, value in {
        "title": "AAAI 2027", "summary": "AI conference.",
        "organizer": "AI Society", "official_url": "https://example.org/aaai/2027",
        "event_start": "2027-02-16", "event_end": "2027-02-17",
        "registration_url": "https://example.org/register/2027",
    }.items():
        fields[name] = {"value": value, "raw_value": value, "validation_status": "VALID",
                        "evidence_text": value, "evidence_block_ids": ["B001"],
                        "link_ids": ["L001"] if name.endswith("url") else [],
                        "confidence": 0.95}
    row = StoredExtraction(
        id=uuid4(), raw_document_id=uuid4(), run_id=uuid4(), source_id=uuid4(),
        source_url="https://example.org/aaai/2027", fingerprint="fp",
        status=ExtractionStatus.REVIEW_REQUIRED,
        payload={"fields": fields, "document_confidence": confidence,
                 "is_conference": conference}, provenance={"phase": "test"},
    )
    candidate = replace(QualityCandidate.from_stored(row),
                        event_start_at=datetime(2027, 2, 16, tzinfo=timezone.utc),
                        event_end_at=datetime(2027, 2, 17, tzinfo=timezone.utc))
    source = Source(row.source_id, "AI Society", "https://example.org", SourceType.OFFICIAL,
                    SourceStatus.HOLD, NOW, canonical_url="https://example.org")
    raw = RawDocument(row.raw_document_id, row.run_id, row.source_id,
                      row.source_url, "local:test", "a" * 64, NOW)
    return row, candidate, source, raw


class DedupTests(unittest.TestCase):
    def setUp(self):
        _, self.candidate, _, _ = fixture()
        self.engine = DeduplicationEngine(similarity_threshold=0.85)

    def compare(self, **updates):
        other = replace(candidate_record(self.candidate), id=str(uuid4()),
                        raw_document_id=str(uuid4()), **updates)
        return self.engine.check(self.candidate, [other])

    def test_canonical_url_tracking_fragment_and_slash(self):
        result = self.compare(canonical_url="https://example.org/aaai/2027/?utm_source=x#top")
        self.assertEqual(result.status, DuplicateStatus.DUPLICATE)
        self.assertEqual(result.match_type, "canonical_url")

    def test_title_normalization(self):
        self.assertEqual(normalize_title("  ＡＡＡＩ—2027! "), "aaai 2027")
        result = self.compare(canonical_url=None, official_url=None,
                              registration_url=None, title="AAAI—2027")
        self.assertEqual(result.status, DuplicateStatus.DUPLICATE)

    def test_same_title_different_year_is_unique_even_with_reused_url(self):
        result = self.compare(title="AAAI 2028", event_start="2028-02-16")
        self.assertEqual(result.status, DuplicateStatus.UNIQUE)

    def test_same_domain_different_event_is_unique(self):
        result = self.compare(canonical_url="https://example.org/other",
                              official_url="https://example.org/other",
                              registration_url=None, title="Another Event",
                              organizer="Other Group", event_start="2027-06-01")
        self.assertEqual(result.status, DuplicateStatus.UNIQUE)

    def test_similar_but_distinct_conference_acronyms_are_unique(self):
        result = self.compare(canonical_url=None, official_url=None,
                              registration_url=None, title="IJCAI 2027",
                              organizer=None, event_start=None)
        self.assertEqual(result.status, DuplicateStatus.UNIQUE)

    def test_registration_and_service_match(self):
        result = self.compare(canonical_url=None, official_url=None)
        self.assertEqual(result.match_type, "registration_url")
        service = replace(candidate_record(self.candidate), id=str(uuid4()), origin="service")
        match = self.engine.check(self.candidate, [service])
        self.assertEqual(match.matched_service_record_id, service.id)

    def test_exact_official_url_service_match(self):
        service = replace(candidate_record(self.candidate), id="service-1",
                          origin="service", canonical_url=None,
                          raw_document_id=None, registration_url=None)
        result = self.engine.check(self.candidate, [service])
        self.assertEqual(result.status, DuplicateStatus.DUPLICATE)
        self.assertEqual(result.match_type, "official_url")
        self.assertEqual(result.matched_service_record_id, "service-1")

    def test_same_raw_document_extraction_versions_require_review(self):
        older = replace(candidate_record(self.candidate), id="older",
                        title=None, organizer=None, official_url=None,
                        canonical_url=None, registration_url=None, event_start=None)
        result = self.engine.check(self.candidate, [older])
        self.assertEqual(result.status, DuplicateStatus.POSSIBLE_DUPLICATE)
        self.assertEqual(result.match_type, "same_raw_document")

    def test_possible_organizer_date_and_similarity_threshold(self):
        result = self.compare(canonical_url=None, official_url=None, registration_url=None,
                              title="Different AI Meeting")
        self.assertEqual(result.status, DuplicateStatus.POSSIBLE_DUPLICATE)
        near = DeduplicationEngine(similarity_threshold=0.5).check(
            self.candidate, [replace(candidate_record(self.candidate), id="other",
                                     raw_document_id=str(uuid4()),
                                     canonical_url=None, official_url=None,
                                     registration_url=None, title="AAAI 2027 Conference",
                                     organizer=None, event_start=None)])
        self.assertEqual(near.match_type, "title_similarity")
        strict = DeduplicationEngine(similarity_threshold=0.99).check(
            self.candidate, [replace(candidate_record(self.candidate), id="other",
                                     raw_document_id=str(uuid4()),
                                     canonical_url=None, official_url=None,
                                     registration_url=None, title="AAAI 2027 Conference",
                                     organizer=None, event_start=None)])
        self.assertEqual(strict.status, DuplicateStatus.UNIQUE)


class VerificationTests(unittest.TestCase):
    def setUp(self):
        _, self.candidate, self.source, self.raw = fixture()
        self.duplicate = DeduplicationEngine().check(self.candidate, [])
        self.engine = VerificationEngine(threshold=0.75)

    def verify(self, candidate=None, source=None, raw=None, duplicate=None):
        return self.engine.verify(candidate or self.candidate,
                                  source=self.source if source is None else source,
                                  raw_document=self.raw if raw is None else raw,
                                  duplicate=duplicate or self.duplicate)

    def test_valid_and_ready(self):
        result = self.verify()
        self.assertEqual(result.status, VerificationStatus.VERIFIED)
        self.assertEqual(PublishGate(threshold=0.85).decide(
            self.candidate, self.duplicate, result).status, GateStatus.PUBLISH_READY)

    def test_confirmed_datetime_keeps_exact_calendar_date_policy(self):
        fields = {**self.candidate.fields,
                  "event_start": {**self.candidate.field("event_start"),
                                  "value": "2027-02-16T09:00:00+00:00"},
                  "event_end": {**self.candidate.field("event_end"),
                                "value": "2027-02-17T18:00:00+00:00"}}
        candidate = replace(self.candidate, fields=fields,
                            event_start_at=datetime(2027, 2, 16, 9, tzinfo=timezone.utc),
                            event_end_at=datetime(2027, 2, 17, 18, tzinfo=timezone.utc))
        verification = self.verify(candidate)
        self.assertEqual(verification.status, VerificationStatus.VERIFIED)
        self.assertEqual(PublishGate(threshold=0.85).decide(
            candidate, self.duplicate, verification).status, GateStatus.PUBLISH_READY)

    def test_missing_required_fields(self):
        for name in ("title", "summary", "organizer", "official_url", "event_start"):
            with self.subTest(name=name):
                fields = {**self.candidate.fields,
                          name: {**self.candidate.field(name), "value": None,
                                 "validation_status": "MISSING"}}
                result = self.verify(replace(self.candidate, fields=fields))
                self.assertEqual(result.status, VerificationStatus.REVIEW_REQUIRED)
                self.assertIn(f"MISSING_REQUIRED_FIELD:{name}", result.reasons)

    def test_invalid_evidence_date_url_and_provenance(self):
        fields = {**self.candidate.fields, "title": {**self.candidate.field("title"),
                  "evidence_block_ids": []}}
        self.assertIn("INSUFFICIENT_EVIDENCE:title",
                      self.verify(replace(self.candidate, fields=fields)).reasons)
        fields = {**self.candidate.fields, "event_start": {**self.candidate.field("event_start"),
                  "value": "2027-02-30"}}
        self.assertIn(ReviewReason.INVALID_DATE.value,
                      self.verify(replace(self.candidate, fields=fields)).reasons)
        fields = {**self.candidate.fields, "official_url": {**self.candidate.field("official_url"),
                  "value": "http://127.0.0.1/private"}}
        self.assertIn(ReviewReason.INVALID_URL.value,
                      self.verify(replace(self.candidate, fields=fields)).reasons)
        self.assertIn(ReviewReason.MISSING_RAW_DOCUMENT.value,
                      self.verify(raw=replace(self.raw, id=uuid4())).reasons)
        self.assertIn(ReviewReason.MISSING_PROVENANCE.value,
                      self.verify(source=replace(self.source, id=uuid4())).reasons)

    def test_possible_duplicate_unknown_trust_and_low_confidence(self):
        possible = replace(self.duplicate, status=DuplicateStatus.POSSIBLE_DUPLICATE)
        self.assertIn(ReviewReason.POSSIBLE_DUPLICATE.value,
                      self.verify(duplicate=possible).reasons)
        unknown = replace(self.source, source_type=SourceType.UNKNOWN)
        self.assertIn(ReviewReason.UNKNOWN_SOURCE_TRUST.value,
                      self.verify(source=unknown).reasons)
        self.assertIn(ReviewReason.LOW_CONFIDENCE.value,
                      self.verify(replace(self.candidate, confidence=0.5)).reasons)
        low_trust = replace(self.source, source_type=SourceType.COMMUNITY)
        self.assertIn(ReviewReason.LOW_SOURCE_TRUST.value,
                      self.verify(source=low_trust).reasons)

    def test_duplicate_nonconference_and_rejected_source(self):
        duplicate = replace(self.duplicate, status=DuplicateStatus.DUPLICATE)
        self.assertEqual(self.verify(duplicate=duplicate).status, VerificationStatus.REVIEW_REQUIRED)
        self.assertEqual(self.verify(replace(self.candidate, is_conference=False)).status,
                         VerificationStatus.REJECTED)
        rejected = replace(self.source, status=SourceStatus.REJECTED)
        self.assertEqual(self.verify(source=rejected).status, VerificationStatus.REJECTED)

    def test_gate_never_queues_unverified_duplicate_or_unnormalized_date(self):
        gate = PublishGate(threshold=0.85)
        verified = self.verify()
        duplicate = replace(self.duplicate, status=DuplicateStatus.DUPLICATE)
        self.assertNotEqual(gate.decide(self.candidate, duplicate, verified).status,
                            GateStatus.PUBLISH_READY)
        self.assertEqual(gate.decide(replace(self.candidate, event_start_at=None),
                                     self.duplicate, verified).status,
                         GateStatus.REVIEW_REQUIRED)


class FakeServiceLookup:
    def __init__(self, records=()):
        self.records = list(records)
        self.calls = 0

    def list_records(self):
        self.calls += 1
        return self.records


class RunnerTests(unittest.TestCase):
    def test_ready_queue_idempotency_and_review_not_queued(self):
        row, candidate, source, raw = fixture()
        sources, docs = InMemorySourceRepository(), InMemoryRawDocumentRepository()
        sources.save(source)
        docs.save(raw)
        quality = InMemoryQualityRepository([row])
        lookup = FakeServiceLookup()
        runner = QualityGateRunner(sources=sources, documents=docs, quality=quality,
                                   service_duplicates=lookup, settings=load_settings(environ={}))
        # Stored extraction contains only a calendar date: no implicit timezone is added.
        result = runner.run(row.id)
        self.assertEqual(result.decision.status, GateStatus.REVIEW_REQUIRED)
        self.assertFalse(result.queued)
        self.assertEqual(len(quality.verifications), 1)
        self.assertEqual(quality.verifications[0].gate_summary["status"], GateStatus.REVIEW_REQUIRED.value)
        self.assertEqual(quality.verifications[0].value.checks["normalization"]["status"], "MISSING")
        self.assertIn("NORMALIZATION_MISSING",
                      quality.verifications[0].value.checks["normalization"]["reason_codes"])
        self.assertEqual(len(quality.queue), 0)
        self.assertEqual(lookup.calls, 1)

        # Explicitly normalized date is sufficient for the queue branch.
        quality.normalized_event_times[row.id] = (candidate.event_start_at, candidate.event_end_at)
        quality.temporal_metadata[row.id] = ("DATETIME_CONFIRMED", "CONFIRMED")
        quality.normalized_registration_times[row.id] = (
            datetime(2026, 12, 1, tzinfo=timezone.utc),
            datetime(2027, 1, 1, tzinfo=timezone.utc),
        )
        quality.application_temporal_metadata[row.id] = ("DATETIME_CONFIRMED", "CONFIRMED")
        first, second = runner.run(row.id), runner.run(row.id)
        self.assertEqual(first.decision.status, GateStatus.PUBLISH_READY)
        queued = quality.queue[row.id]["publish_candidate"]
        self.assertEqual(queued["application_time_kind"], "DATETIME_CONFIRMED")
        self.assertEqual(queued["application_timezone_basis"], "CONFIRMED")
        self.assertEqual(queued["application_deadline_at"], "2027-01-01T00:00:00+00:00")
        self.assertTrue(first.queued)
        self.assertFalse(second.queued)
        self.assertEqual(len(quality.queue), 1)
        snapshot = quality.queue[row.id]["publish_candidate"]
        self.assertEqual(snapshot["title"], "AAAI 2027")
        self.assertEqual(snapshot["event_time_kind"], "DATETIME_CONFIRMED")
        self.assertEqual(snapshot["timezone_basis"], "CONFIRMED")

    def test_service_lookup_failure_fails_closed(self):
        row, _, source, raw = fixture()
        sources, docs = InMemorySourceRepository(), InMemoryRawDocumentRepository()
        sources.save(source)
        docs.save(raw)
        quality = InMemoryQualityRepository([row])
        class BrokenLookup:
            def list_records(self):
                raise RuntimeError("database unavailable")
        result = QualityGateRunner(
            sources=sources, documents=docs, quality=quality,
            service_duplicates=BrokenLookup(), settings=load_settings(environ={}),
        ).run(row.id)
        self.assertEqual(result.decision.status, GateStatus.REVIEW_REQUIRED)
        self.assertIn(ReviewReason.SERVICE_LOOKUP_FAILED.value, result.verification.reasons)


class ReadOnlyAdapterTests(unittest.TestCase):
    def test_only_select_on_opportunities(self):
        calls = []
        class Query:
            def select(self, *args, **kwargs):
                calls.append(("select", args, kwargs)); return self
            def limit(self, value):
                calls.append(("limit", value)); return self
            def execute(self):
                return SimpleNamespace(data=[{"id": "1", "title": "A", "organizer": "B",
                                              "official_url": "https://example.org",
                                              "event_start_at": None}], count=1)
        class Client:
            def table(self, name):
                calls.append(("table", name)); return Query()
        rows = SupabaseServiceDuplicateLookup(Client()).list_records()
        self.assertEqual(rows[0].origin, "service")
        self.assertEqual([item[0] for item in calls], ["table", "select", "limit"])
        self.assertEqual(calls[0][1], "opportunities")


if __name__ == "__main__":
    unittest.main()
