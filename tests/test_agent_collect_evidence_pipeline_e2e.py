"""Synthetic raw bytes through existing extraction, normalization, gate and evidence."""

import hashlib
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from agent_collect.config.settings import load_settings
from agent_collect.dedup.engine import candidate_record
from agent_collect.discovery.evidence import (
    EvidenceState, OfficialEvidencePipeline, OpportunityIdentity,
)
from agent_collect.extraction.preprocess import preprocess_html
from agent_collect.extraction.providers import FakeExtractionProvider
from agent_collect.extraction.runner import ExtractionRunner
from agent_collect.extraction.schema import FIELD_NAMES, ExtractionStatus
from agent_collect.normalization.runner import NormalizationRunner
from agent_collect.quality_gate.models import GateStatus, QualityCandidate
from agent_collect.quality_gate.runner import QualityGateRunner
from agent_collect.repositories.extraction import InMemoryExtractionRepository
from agent_collect.repositories.memory import InMemoryRawDocumentRepository
from agent_collect.repositories.normalization import InMemoryNormalizationRepository
from agent_collect.repositories.quality import InMemoryQualityRepository
from agent_collect.storage.raw import LocalRawContentStorage
from tests.test_agent_collect_official_evidence import EvidenceFixture, NOW


class NormalizedQuality(InMemoryQualityRepository):
    def __init__(self, rows, normalization):
        super().__init__(rows)
        self.normalization = normalization

    def payload(self, candidate_id):
        return self.normalization.rows[candidate_id].normalized_payload

    def get_normalized_event_times(self, candidate_id):
        row = self.payload(candidate_id)
        return tuple(datetime.fromisoformat(row[name]) if row.get(name) else None
                     for name in ("event_start_at", "event_end_at"))

    def get_normalized_registration_times(self, candidate_id):
        row = self.payload(candidate_id)
        return tuple(datetime.fromisoformat(row[name]) if row.get(name) else None
                     for name in ("application_start_at", "application_deadline_at"))

    def get_temporal_metadata(self, candidate_id):
        row = self.payload(candidate_id)
        return row["event_time_kind"], row["timezone_basis"]

    def get_application_temporal_metadata(self, candidate_id):
        row = self.payload(candidate_id)
        return row["application_time_kind"], row["application_timezone_basis"]

    def get_normalization_diagnostics(self, candidate_id):
        return self.payload(candidate_id)


class EmptyServiceDuplicates:
    def __init__(self, records=()):
        self.records = records

    def list_records(self):
        return list(self.records)


class PipelineE2ETests(unittest.TestCase):
    def exercise(self, *, start="2027-10-10T09:00+09:00",
                 deadline="2027-10-20T18:00+09:00", missing_start=False,
                 missing_deadline=False,
                 duplicate=False, guard=False, permission=True,
                 guard_error=False, missing_assessor=False):
        fixture = EvidenceFixture(start=start, deadline=deadline)
        html = (
            "<h1>AI Research Call</h1>"
            "<p>Summary: AI researchers are invited.</p>"
            "<p>Organizer: AI Agency</p>"
            f"<p>Registration opens {start}</p>"
            f"<p>Registration closes {deadline}</p>"
            "<p>Event starts 2027-11-10T09:00+09:00</p>"
            "<p>Event ends 2027-11-11T18:00+09:00</p>"
            '<a href="https://agency.example.org/apply/ai-2026">Register</a>'
        ).encode()
        prepared = preprocess_html(html, source_url=fixture.raw.source_url,
                                   max_input_chars=12000)
        fields = {name: {"value": None, "evidence_block_ids": [],
                         "link_ids": [], "confidence": None} for name in FIELD_NAMES}
        values = {
            "title": ("AI Research Call", "AI Research Call"),
            "summary": ("AI researchers are invited", "Summary:"),
            "organizer": ("AI Agency", "Organizer:"),
            "registration_start": (start, "Registration opens"),
            "registration_deadline": (deadline, "Registration closes"),
            "event_start": ("2027-11-10T09:00+09:00", "Event starts"),
            "event_end": ("2027-11-11T18:00+09:00", "Event ends"),
        }
        for name, (value, marker) in values.items():
            if name == "registration_start" and missing_start:
                continue
            if name == "registration_deadline" and missing_deadline:
                continue
            block = next(item for item in prepared.evidence_blocks if marker in item.text)
            fields[name] = {"value": value, "evidence_block_ids": [block.block_id],
                            "link_ids": [], "confidence": .95}
        fields["official_url"] = {"value": None, "evidence_block_ids": [],
                                  "link_ids": ["L000"], "confidence": .95}
        fields["registration_url"] = {"value": None, "evidence_block_ids": [],
                                      "link_ids": ["L001"], "confidence": .95}
        provider = FakeExtractionProvider({
            "is_conference": True, "document_confidence": .95, "fields": fields,
        })
        settings = load_settings(environ={"AGENT_LLM_ENABLED": "true",
                                          "AGENT_LLM_MODEL": "offline-fixture"})
        with tempfile.TemporaryDirectory() as folder:
            storage = LocalRawContentStorage(Path(folder))
            receipt = storage.put(html)
            raw = replace(fixture.raw, content_ref=receipt.ref,
                          content_sha256=hashlib.sha256(html).hexdigest())
            documents = InMemoryRawDocumentRepository()
            documents.save(raw)
            extracted = InMemoryExtractionRepository()
            extraction = ExtractionRunner(documents=documents, storage=storage,
                                          repository=extracted, provider=provider,
                                          settings=settings).run(raw.id)
            self.assertIsNotNone(extraction.candidate_id)
            candidate = extracted.rows[extraction.candidate_id]
            normalization = InMemoryNormalizationRepository([candidate])
            normalized = NormalizationRunner(normalization).run(candidate.id)
            quality = NormalizedQuality([candidate], normalization)
            duplicate_records = (
                (replace(candidate_record(QualityCandidate.from_stored(candidate)),
                         id=str(uuid4()), origin="service", raw_document_id=None),)
                if duplicate else ()
            )
            guarded_access = (fixture.access if permission else
                              replace(fixture.access, explicit_collection_permission=False))
            def assess_before_gate(stored, verification):
                if guard_error:
                    raise RuntimeError("missing reviewed snapshot")
                return OfficialEvidencePipeline().assess(
                    fixture.result, now=NOW, source=fixture.source,
                    review=fixture.review, access=guarded_access,
                    run=fixture.run, raw=raw, content=storage.get(receipt.ref),
                    extraction=stored, verification=verification,
                )
            gate = QualityGateRunner(
                sources=fixture.sources, documents=documents, quality=quality,
                service_duplicates=EmptyServiceDuplicates(duplicate_records), settings=settings,
                evidence_assessor=(assess_before_gate if guard and not missing_assessor
                                   else None),
                require_official_evidence=guard,
            ).run(candidate.id)
            prior = ((OpportunityIdentity(
                raw.canonical_url, fixture.source.id, None, raw.content_sha256,
                "AI Research Call", start, deadline),) if duplicate else ())
            evidence = OfficialEvidencePipeline().assess(
                fixture.result, now=NOW, source=fixture.source, review=fixture.review,
                access=fixture.access, run=fixture.run, raw=raw, content=storage.get(receipt.ref),
                extraction=candidate, verification=gate.verification, previous=prior,
            )
            return extraction, normalized, gate, evidence, len(provider.calls), len(quality.queue)

    def test_valid_official_notice_reaches_existing_publish_gate_without_publishing(self):
        extraction, normalized, gate, evidence, calls, queue_count = self.exercise()
        self.assertEqual(extraction.status, ExtractionStatus.EXTRACTED)
        self.assertEqual(normalized.normalized_payload["status"], "NORMALIZED")
        self.assertEqual(gate.decision.status, GateStatus.PUBLISH_READY)
        self.assertTrue(gate.queued)
        self.assertEqual(evidence.state, EvidenceState.VERIFIED)
        self.assertEqual((calls, queue_count), (1, 1))

    def test_missing_recruitment_start_remains_review_required(self):
        extraction, _, gate, evidence, _, queue_count = self.exercise(missing_start=True)
        self.assertEqual(extraction.status, ExtractionStatus.EXTRACTED)
        self.assertEqual(evidence.state, EvidenceState.REVIEW_REQUIRED)
        self.assertIn("registration_start:missing_evidence", evidence.reasons)
        self.assertEqual(gate.decision.status, GateStatus.REVIEW_REQUIRED)
        self.assertFalse(gate.queued)
        self.assertEqual(queue_count, 0)

    def test_missing_recruitment_deadline_blocks_guarded_queue(self):
        _, _, gate, evidence, _, queue_count = self.exercise(
            guard=True, missing_deadline=True)
        self.assertEqual(evidence.state, EvidenceState.REVIEW_REQUIRED)
        self.assertEqual(gate.decision.status, GateStatus.REVIEW_REQUIRED)
        self.assertEqual(queue_count, 0)

    def test_expired_exact_recruitment_is_held_by_the_existing_gate(self):
        _, _, gate, evidence, _, queue_count = self.exercise(
            start="2026-01-01T09:00+09:00", deadline="2026-01-02T18:00+09:00")
        self.assertEqual(gate.decision.status, GateStatus.REVIEW_REQUIRED)
        self.assertIn("RECRUITMENT_EXPIRED", gate.verification.reasons)
        self.assertFalse(evidence.verified_opportunity)
        self.assertEqual(queue_count, 0)

    def test_existing_service_opportunity_duplicate_never_queues_again(self):
        _, _, gate, evidence, _, queue_count = self.exercise(duplicate=True)
        self.assertEqual(gate.decision.status, GateStatus.REVIEW_REQUIRED)
        self.assertEqual(evidence.state, EvidenceState.DUPLICATE)
        self.assertFalse(evidence.verified_opportunity)
        self.assertEqual(queue_count, 0)

    def test_verified_official_evidence_is_an_input_to_existing_gate(self):
        _, _, gate, evidence, _, queued = self.exercise(guard=True)
        self.assertEqual(evidence.state, EvidenceState.VERIFIED)
        self.assertEqual(gate.verification.checks["official_evidence"]["state"], "VERIFIED")
        self.assertEqual(gate.decision.status, GateStatus.PUBLISH_READY)
        self.assertEqual(queued, 1)

    def test_permission_failure_blocks_an_otherwise_publish_ready_candidate(self):
        _, _, gate, _, _, queued = self.exercise(guard=True, permission=False)
        self.assertEqual(gate.decision.status, GateStatus.REVIEW_REQUIRED)
        self.assertIn("OFFICIAL_EVIDENCE_REVIEW_REQUIRED", gate.verification.reasons)
        self.assertEqual(queued, 0)

    def test_missing_evidence_assessor_fails_closed(self):
        _, _, gate, _, _, queued = self.exercise(guard=True, missing_assessor=True)
        self.assertEqual(gate.decision.status, GateStatus.REVIEW_REQUIRED)
        self.assertIn("OFFICIAL_EVIDENCE_UNAVAILABLE", gate.verification.reasons)
        self.assertEqual(queued, 0)

    def test_evidence_assessor_error_fails_closed(self):
        _, _, gate, _, _, queued = self.exercise(guard=True, guard_error=True)
        self.assertEqual(gate.decision.status, GateStatus.REVIEW_REQUIRED)
        self.assertIn("OFFICIAL_EVIDENCE_ERROR", gate.verification.reasons)
        self.assertEqual(queued, 0)


if __name__ == "__main__":
    unittest.main()
