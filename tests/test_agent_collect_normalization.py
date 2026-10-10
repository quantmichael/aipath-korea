"""Deterministic temporal normalization and Agent-only persistence tests."""

import unittest
from datetime import datetime, timezone
from uuid import uuid4

from agent_collect.extraction.preprocess import preprocess_html
from agent_collect.extraction.schema import FIELD_NAMES, ExtractionStatus, StoredExtraction
from agent_collect.extraction.validation import (
    parse_evidenced_date, parse_explicit_datetime, validate_candidate,
)
from agent_collect.normalization.engine import normalize_extracted_payload
from agent_collect.normalization.runner import NormalizationRunner
from agent_collect.repositories.normalization import InMemoryNormalizationRepository
from agent_collect.repositories.normalization_supabase import SupabaseNormalizationRepository
from agent_collect.repositories.quality_supabase import SupabaseQualityRepository


SOURCE_URL = "https://conference.example.org/2027"


def field(value=None, *, status="MISSING", evidence=None, refs=()):
    return {
        "value": value, "raw_value": value,
        "validation_status": status, "evidence_text": evidence,
        "evidence_block_ids": list(refs), "link_ids": [],
        "source_url": SOURCE_URL, "confidence": 0.9 if value else None,
    }


def extracted(**overrides):
    fields = {name: field() for name in FIELD_NAMES}
    fields.update(overrides)
    return {"schema_version": "conference-grounded-v2", "is_conference": True,
            "document_confidence": 0.94, "fields": fields, "review_reasons": []}


def dated(value):
    return field(value, status="VALID", evidence=value, refs=("B001",))


def stored(payload):
    return StoredExtraction(uuid4(), uuid4(), uuid4(), uuid4(), SOURCE_URL, "fingerprint",
                            ExtractionStatus.EXTRACTED, payload,
                            {"extraction_status": "EXTRACTED", "model": "fixture"})


class NormalizationEngineTests(unittest.TestCase):
    def test_date_only_does_not_create_clock_time(self):
        result = normalize_extracted_payload(extracted(
            event_start=dated("2027-10-02"), event_end=dated("2027-10-07"),
            registration_start=dated("2027-08-01"),
            registration_deadline=dated("2027-09-01"),
        )).payload
        self.assertEqual(result["event_time_kind"], "DATE_ONLY")
        self.assertEqual(result["application_time_kind"], "DATE_ONLY")
        self.assertEqual(result["event_start"], "2027-10-02")
        self.assertIsNone(result["event_start_at"])
        self.assertIsNone(result["application_deadline_at"])
        self.assertIn("EVENT_DATE_ONLY", result["reason_codes"])

    def test_explicit_datetime_and_registration_period(self):
        payload = extracted(
            event_start=dated("2027-10-02T09:30:00+03:00"),
            event_end=dated("2027-10-07T18:00:00+03:00"),
            registration_start=dated("2027-08-01T09:00:00+03:00"),
            registration_deadline=dated("2027-09-01T17:00:00+03:00"),
        )
        normalized = normalize_extracted_payload(payload).payload
        self.assertEqual(normalized["status"], "NORMALIZED")
        self.assertEqual(normalized["event_time_kind"], "DATETIME_CONFIRMED")
        self.assertEqual(normalized["timezone_basis"], "CONFIRMED")
        self.assertEqual(normalized["application_time_kind"], "DATETIME_CONFIRMED")
        self.assertEqual(normalized["application_timezone_basis"], "CONFIRMED")
        self.assertEqual(normalized["event_start_at"], "2027-10-02T09:30:00+03:00")
        self.assertEqual(normalized["application_deadline_at"], "2027-09-01T17:00:00+03:00")

    def test_derived_timezone_is_only_a_hint(self):
        payload = extracted(
            event_start=dated("2027-04-23"),
            city=dated("Seoul"), country=dated("South Korea"),
        )
        result = normalize_extracted_payload(payload).payload
        self.assertEqual(result["timezone_basis"], "DERIVED")
        self.assertEqual(result["fields"]["event_start"]["timezone_hint"], "Asia/Seoul")
        self.assertIsNone(result["event_start_at"])
        payload["fields"]["city"] = field()
        self.assertEqual(normalize_extracted_payload(payload).payload["timezone_basis"], "UNKNOWN")

    def test_malformed_or_ambiguous_dates_fail_closed(self):
        for value in ("2027-02-30", "03/04/2027", "2027-10-02T09:00"):
            with self.subTest(value=value):
                result = normalize_extracted_payload(extracted(event_start=dated(value))).payload
                self.assertEqual(result["event_time_kind"], "UNKNOWN")
                self.assertIsNone(result["event_start_at"])
        self.assertIsNone(parse_explicit_datetime("2027-10-02T09:00"))
        self.assertIsNone(parse_evidenced_date("Dec 30 - Jan 2 2027", field_name="event_start"))
        invalid_end = field(None, status="DATE_MISMATCH")
        invalid_end["raw_value"] = "7–2 October 2027"
        result = normalize_extracted_payload(extracted(
            event_start=dated("2027-10-02"), event_end=invalid_end,
        )).payload
        self.assertEqual(result["event_time_kind"], "UNKNOWN")
        self.assertIn("EVENT_END:FIELD_INVALID", result["reason_codes"])

    def test_out_of_order_period_is_diagnostic_and_not_publishable(self):
        result = normalize_extracted_payload(extracted(
            event_start=dated("2027-10-07T09:00+03:00"),
            event_end=dated("2027-10-02T18:00+03:00"),
            registration_start=dated("2027-09-01T09:00+03:00"),
            registration_deadline=dated("2027-08-01T18:00+03:00"),
        )).payload
        self.assertEqual(result["event_time_kind"], "UNKNOWN")
        self.assertEqual(result["application_time_kind"], "UNKNOWN")
        self.assertIsNone(result["event_start_at"])
        self.assertIsNone(result["application_start_at"])
        self.assertIn("EVENT_DATE_ORDER_INVALID", result["reason_codes"])
        self.assertIn("APPLICATION_DATE_ORDER_INVALID", result["reason_codes"])


class ExtractionIntegrityTests(unittest.TestCase):
    def _validated_date(self, evidence, start, end):
        from agent_collect.extraction.schema import ConferenceCandidate, FieldEvidence

        prepared = preprocess_html(f"<p>{evidence}</p>".encode(),
                                   source_url=SOURCE_URL, max_input_chars=12000)
        fields = {name: FieldEvidence(None, None, None, SOURCE_URL) for name in FIELD_NAMES}
        for name, value in (("event_start", start), ("event_end", end)):
            fields[name] = FieldEvidence(value, None, 0.9, SOURCE_URL,
                                         evidence_block_ids=("B001",))
        return validate_candidate(ConferenceCandidate(fields, 0.96, True),
                                  prepared=prepared, source_url=SOURCE_URL,
                                  max_evidence_chars=240)

    def test_only_explicit_date_ranges_resolve_to_correct_endpoints(self):
        cases = (
            ("2–7 October 2027", "2 October 2027", "7 October 2027",
             "2027-10-02", "2027-10-07"),
            ("August 7–13, 2027", "August 7–13, 2027", "August 7–13, 2027",
             "2027-08-07", "2027-08-13"),
            ("Nov 30 - Dec 2 2027", "2027-11-30", "2027-12-02",
             "2027-11-30", "2027-12-02"),
        )
        for evidence, start, end, expected_start, expected_end in cases:
            with self.subTest(evidence=evidence):
                result = self._validated_date(evidence, start, end)
                self.assertEqual(result.fields["event_start"].value, expected_start)
                self.assertEqual(result.fields["event_end"].value, expected_end)

    def test_ambiguous_range_and_wrong_evidence_still_fail(self):
        result = self._validated_date("Dec 30 - Jan 2 2027", "Dec 30 - Jan 2 2027",
                                      "Dec 30 - Jan 2 2027")
        self.assertIsNone(result.fields["event_start"].value)
        result = self._validated_date("August 7–13, 2027", "2027-08-06", "2027-08-13")
        self.assertIsNone(result.fields["event_start"].value)
        inverted = self._validated_date("August 13–7, 2027", "2027-08-07", "2027-08-13")
        self.assertIsNone(inverted.fields["event_start"].value)

    def test_explicit_offset_datetime_requires_exact_cited_evidence(self):
        confirmed = self._validated_date(
            "2027-10-02T09:30:00+03:00 to 2027-10-07T18:00:00+03:00",
            "2027-10-02T09:30:00+03:00", "2027-10-07T18:00:00+03:00",
        )
        self.assertEqual(confirmed.fields["event_start"].value,
                         "2027-10-02T09:30:00+03:00")
        self.assertEqual(confirmed.fields["event_end"].validation_status, "VALID")
        unsupported = self._validated_date(
            "October 2–7, 2027", "2027-10-02T09:30:00+03:00", "2027-10-07"
        )
        self.assertIsNone(unsupported.fields["event_start"].value)

    def test_misplaced_real_link_id_recovers_without_inventing_url(self):
        from agent_collect.extraction.schema import ConferenceCandidate, FieldEvidence

        prepared = preprocess_html(b"<h1>AI Conference</h1>", source_url=SOURCE_URL,
                                   max_input_chars=12000)
        fields = {name: FieldEvidence(None, None, None, SOURCE_URL) for name in FIELD_NAMES}
        fields["official_url"] = FieldEvidence(None, None, 0.9, SOURCE_URL,
                                                 evidence_block_ids=("B001", "L000"))
        result = validate_candidate(ConferenceCandidate(fields, 0.9, True),
                                    prepared=prepared, source_url=SOURCE_URL,
                                    max_evidence_chars=240)
        self.assertEqual(result.fields["official_url"].value, SOURCE_URL)
        self.assertEqual(result.fields["official_url"].link_ids, ("L000",))
        fields["official_url"] = FieldEvidence(None, None, 0.9, SOURCE_URL,
                                                 evidence_block_ids=("B999", "L000"))
        invalid = validate_candidate(ConferenceCandidate(fields, 0.9, True),
                                     prepared=prepared, source_url=SOURCE_URL,
                                     max_evidence_chars=240)
        self.assertIsNone(invalid.fields["official_url"].value)


class FakeCandidateTable:
    def __init__(self, row):
        self.row = row
        self.filters = {}
        self.patch = None
        self.columns = None
        self.writes = 0

    def select(self, columns):
        self.columns = columns
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, amount):
        return self

    def update(self, patch):
        self.patch = patch
        return self

    def execute(self):
        from types import SimpleNamespace
        matches = all(self.row.get(key) == value for key, value in self.filters.items())
        self.filters = {}
        if not matches:
            return SimpleNamespace(data=[])
        if self.patch is not None:
            self.row.update(self.patch)
            self.patch = None
            self.writes += 1
            return SimpleNamespace(data=[{"id": self.row["id"]}])
        return SimpleNamespace(data=[dict(self.row)])


class FakeClient:
    def __init__(self, row):
        self.name = None
        self.candidates = FakeCandidateTable(row)

    def table(self, name):
        self.name = name
        if name != "agent_candidates":
            raise AssertionError("normalization touched a non-Agent table")
        return self.candidates


class PersistenceTests(unittest.TestCase):
    def test_memory_runner_is_idempotent_and_auditable(self):
        item = stored(extracted(event_start=dated("2027-04-23")))
        repo = InMemoryNormalizationRepository([item])
        runner = NormalizationRunner(repo)
        first = runner.run(item.id)
        second = runner.run(item.id)
        self.assertEqual(first, second)
        self.assertEqual(first.normalized_payload["event_time_kind"], "DATE_ONLY")
        self.assertEqual(first.extraction.provenance["normalization"]["method"], "deterministic")
        self.assertEqual(first.extraction.provenance["normalization"]["input_sha256"],
                         first.normalized_payload["input_sha256"])

    def test_supabase_writer_persists_and_reads_back_agent_candidate_only(self):
        item = stored(extracted(event_start=dated("2027-04-23")))
        row = {
            "id": str(item.id), "raw_document_id": str(item.raw_document_id),
            "run_id": str(item.run_id), "source_id": str(item.source_id),
            "source_url": item.source_url, "item_fingerprint": item.fingerprint,
            "provenance": dict(item.provenance), "extracted_payload": item.payload,
            "normalized_payload": {}, "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        client = FakeClient(row)
        repo = SupabaseNormalizationRepository(client)
        result = NormalizationRunner(repo).run(item.id)
        self.assertEqual(client.candidates.writes, 1)
        self.assertEqual(result.normalized_payload, row["normalized_payload"])
        self.assertEqual(result.extraction.provenance["normalization"],
                         row["provenance"]["normalization"])
        NormalizationRunner(repo).run(item.id)
        self.assertEqual(client.candidates.writes, 1)
        quality = SupabaseQualityRepository(client)
        self.assertEqual(quality.get_normalization_diagnostics(item.id)["status"], "REVIEW_REQUIRED")
        client.candidates.row["extracted_payload"] = {**item.payload, "document_confidence": 0.5}
        self.assertEqual(quality.get_normalization_diagnostics(item.id)["status"], "STALE")
        self.assertEqual(quality.get_normalized_event_times(item.id), (None, None))


if __name__ == "__main__":
    unittest.main()
