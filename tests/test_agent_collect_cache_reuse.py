"""Cache rebinding safety and full offline processing without provider calls."""

import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from tests import test_agent_collect_extraction as extraction_tests
from tests.test_agent_collect_extraction import HTML, URL, payload
from tests.test_agent_collect_processing import _Jobs
from agent_collect.extraction.providers import FakeExtractionProvider
from agent_collect.extraction.schema import ExtractionStatus
from agent_collect.normalization.runner import NormalizationRunner
from agent_collect.processing.models import ProcessingPolicy
from agent_collect.processing.worker import ProcessingWorker
from agent_collect.quality_gate.runner import QualityGateRunner
from agent_collect.repositories.normalization import InMemoryNormalizationRepository, NormalizationState
from agent_collect.repositories.quality import InMemoryQualityRepository
from agent_collect.schemas.models import Source, SourceType
from agent_collect.schemas.status import SourceStatus


class CacheReuseTests(unittest.TestCase):
    _document = extraction_tests.ExtractionTests._document
    runner = extraction_tests.ExtractionTests.runner
    setUp = extraction_tests.ExtractionTests.setUp

    def prime(self):
        self.repo.rows.clear(); self.repo.logs.clear()
        self.provider = FakeExtractionProvider(payload())
        self.extractor = self.runner(self.provider)
        first = self.extractor.run(self.document.id)
        self.artifact = self.repo.rows[first.candidate_id]
        other = self._document(HTML, url=URL + '?fresh=1')
        self.other = replace(other, source_id=uuid4(), run_id=uuid4())
        self.documents.save(self.other)
        self.job_id = uuid4()

    def reuse(self):
        return self.extractor.run_for_processing(self.other.id, processing_job_id=self.job_id,
                                                 processing_version='conference-processing-v1')

    def test_same_key_rebinds_without_llm_and_preserves_origin(self):
        self.prime(); before = deepcopy(self.artifact)
        result = self.reuse()
        self.assertEqual(result.status, ExtractionStatus.CACHED)
        self.assertNotEqual(result.candidate_id, self.artifact.id)
        row = self.repo.rows[result.candidate_id]
        self.assertEqual((row.raw_document_id, row.source_id, row.run_id),
                         (self.other.id, self.other.source_id, self.other.run_id))
        self.assertEqual(row.provenance['processing_job_id'], str(self.job_id))
        self.assertEqual(row.provenance['processing_version'], 'conference-processing-v1')
        self.assertEqual(row.provenance['reused_from_candidate_id'], str(self.artifact.id))
        self.assertEqual(row.provenance['reused_from_raw_document_id'], str(self.document.id))
        self.assertEqual(self.artifact, before)
        self.assertTrue(row.provenance['llm_saved'])
        self.assertEqual(len(self.provider.calls), 1)
        self.assertTrue(all(f['source_url'] == self.other.source_url for f in row.payload['fields'].values()))
        self.assertEqual(row.payload['fields']['official_url']['value'], self.other.source_url)

    def test_reprocessing_candidate_idempotent(self):
        self.prime(); first = self.reuse(); again = self.reuse()
        self.assertEqual(first.candidate_id, again.candidate_id)
        self.assertEqual(len(self.repo.rows), 2)
        self.assertEqual(len(self.provider.calls), 1)

    def test_version_metadata_mismatch_rejected(self):
        for key in ('extraction_version', 'prompt_version', 'model', 'provider'):
            with self.subTest(key=key):
                self.prime()
                changed = replace(self.artifact, provenance={**self.artifact.provenance, key:'different'})
                self.repo.rows[self.artifact.id] = changed
                self.assertEqual(self.reuse().status, ExtractionStatus.FAILED)
                self.assertEqual(len(self.provider.calls), 1)

    def test_processing_new_prompt_cache_miss_never_bills(self):
        self.prime()
        with patch('agent_collect.extraction.runner.PROMPT_VERSION', 'different'):
            result = self.reuse()
        self.assertEqual(result.errors, ('CACHE_VERSION_MISMATCH',))
        self.assertEqual(len(self.provider.calls), 1)

    def test_processing_new_extraction_cache_miss_never_bills(self):
        self.prime()
        with patch('agent_collect.extraction.runner.EXTRACTION_VERSION', 'different'):
            result = self.reuse()
        self.assertEqual(result.errors, ('CACHE_VERSION_MISMATCH',))
        self.assertEqual(len(self.provider.calls), 1)

    def test_schema_mismatch_rejected(self):
        self.prime()
        self.repo.rows[self.artifact.id] = replace(self.artifact, payload={**self.artifact.payload,'schema_version':'different'})
        self.assertEqual(self.reuse().errors, ('CACHE_SCHEMA_MISMATCH',))
        self.assertEqual(len(self.provider.calls), 1)

    def test_fingerprint_mismatch_rejected_without_fallback(self):
        self.prime()
        with patch.object(self.repo, 'find_by_fingerprint',
                          return_value=replace(self.artifact, fingerprint='wrong')):
            self.assertEqual(self.reuse().errors, ('CACHE_KEY_MISMATCH',))
        self.assertEqual(len(self.provider.calls), 1)

    def test_valid_value_tamper_rejected(self):
        self.prime(); data = deepcopy(self.artifact.payload)
        data['fields']['title']['value'] = 'fabricated'
        self.repo.rows[self.artifact.id] = replace(self.artifact, payload=data)
        self.assertEqual(self.reuse().errors, ('CACHE_EVIDENCE_MISMATCH',))
        self.assertEqual(len(self.provider.calls), 1)

    def test_evidence_tamper_rejected(self):
        self.prime(); data = deepcopy(self.artifact.payload)
        data['fields']['title']['evidence_text'] = 'fabricated'
        self.repo.rows[self.artifact.id] = replace(self.artifact, payload=data)
        self.assertEqual(self.reuse().errors, ('CACHE_EVIDENCE_MISMATCH',))
        self.assertEqual(len(self.provider.calls), 1)

    def test_missing_origin_provenance_rejected(self):
        self.prime()
        self.repo.rows[self.artifact.id] = replace(self.artifact, run_id=uuid4())
        self.assertEqual(self.reuse().errors, ('CACHE_PROVENANCE_MISMATCH',))

    def test_invalid_artifact_rejected(self):
        self.prime()
        self.repo.rows[self.artifact.id] = replace(self.artifact, status=ExtractionStatus.FAILED)
        self.assertEqual(self.reuse().errors, ('CACHE_INVALID_ARTIFACT',))

    def test_input_hash_mismatch_rejected(self):
        self.prime()
        self.repo.rows[self.artifact.id] = replace(self.artifact, provenance={**self.artifact.provenance,'input_hash':'bad'})
        self.assertEqual(self.reuse().errors, ('CACHE_INPUT_MISMATCH',))

    def test_cache_works_after_budget_exhausted(self):
        self.prime()
        for _ in range(5):
            self.repo.log(event_type='EXTRACTION_LLM_ATTEMPT',source_id=self.source_id,
                          run_id=self.run_id,details={})
        self.assertEqual(self.reuse().status, ExtractionStatus.CACHED)
        self.assertEqual(len(self.provider.calls), 1)

    def test_changed_relative_links_rejected(self):
        self.prime()
        self.other = replace(self.other, source_url='https://different.example/2027')
        self.documents.save(self.other)
        self.assertEqual(self.reuse().errors, ('CACHE_EVIDENCE_MISMATCH',))

    def test_cache_pipeline_continues_and_terminal_replay_is_idempotent(self):
        self.prime()
        class LazyNormalization(InMemoryNormalizationRepository):
            def get(repo, cid):
                if cid not in repo.rows:
                    repo.rows[cid] = NormalizationState(self.repo.rows[cid], {})
                return super().get(cid)
        normalization = LazyNormalization()
        quality = InMemoryQualityRepository(); quality.rows = self.repo.rows
        source = Source(self.other.source_id,'Cached Conference',self.other.source_url,
                        SourceType.OFFICIAL,SourceStatus.HOLD,datetime.now(timezone.utc))
        gate = QualityGateRunner(sources=SimpleNamespace(get=lambda _:source),
                                documents=self.documents,quality=quality,
                                service_duplicates=SimpleNamespace(list_records=lambda:[]),
                                settings=self.extractor.settings)
        jobs = _Jobs(self.other.id)
        worker = ProcessingWorker(jobs=jobs,documents=self.documents,storage=self.storage,
                                  extractor=self.extractor,normalizer=NormalizationRunner(normalization),
                                  quality_gate=gate,policy=ProcessingPolicy(),worker_id='cache-test')
        result = worker.run_once()
        self.assertEqual((result.failed_count,result.processed_count,result.review_count),(0,1,1))
        cid = jobs.checkpoints[0][0]
        self.assertTrue(normalization.rows[cid].normalized_payload)
        self.assertEqual(len(quality.verifications),1)
        checks = quality.verifications[0].value.checks
        self.assertIn('duplicate',checks)
        self.assertIn('normalization',checks)
        self.assertEqual(quality.verifications[0].gate_summary['status'],'REVIEW_REQUIRED')
        self.assertTrue(all(ref.startswith(str(self.other.id)+':') for ref in quality.verifications[0].value.evidence_refs))
        # Replay after a Gate-save/checkpoint crash reuses the same verification.
        replay = gate.run(cid)
        self.assertEqual(replay.verification_id, quality.verifications[0].id)
        self.assertEqual(len(quality.verifications), 1)
        worker.run_once()  # claimed terminal job cannot be claimed again
        self.assertEqual(len(quality.verifications),1)
        self.assertEqual(len(self.repo.rows),2)
        self.assertFalse(quality.queue)
        self.assertEqual(len(self.provider.calls),1)

    def test_supabase_verification_response_loss_and_replay_do_not_duplicate(self):
        from agent_collect.repositories.quality_supabase import SupabaseQualityRepository
        from agent_collect.quality_gate.models import QualityVerification
        class Client:
            def __init__(client):
                client.rows = {}; client.inserts = 0
            def table(client, name):
                self.assertEqual(name, 'agent_verifications')
                class Query:
                    identifier = None
                    payload = None
                    def select(query, *_): return query
                    def eq(query, _, value): query.identifier = value; return query
                    def limit(query, _): return query
                    def insert(query, value): query.payload = value; return query
                    def execute(query):
                        if query.payload is not None:
                            client.inserts += 1
                            client.rows[query.payload['id']] = query.payload
                            raise RuntimeError('response lost after committed INSERT')
                        return SimpleNamespace(data=[client.rows[query.identifier]] if query.identifier in client.rows else [])
                return Query()
        client = Client(); repo = SupabaseQualityRepository(client)
        verification = QualityVerification(uuid4(),'REVIEW_REQUIRED',0.5,('missing',),
                                           {'cache_reuse_key':'stable'},(),datetime.now(timezone.utc))
        first = repo.save_verification(verification, {}, {'status':'REVIEW_REQUIRED'})
        replay = repo.save_verification(verification, {}, {'status':'REVIEW_REQUIRED'})
        self.assertEqual(first.id, replay.id)
        self.assertEqual(client.inserts, 1)
        changed = replace(verification,checks={'cache_reuse_key':'new-policy'})
        self.assertNotEqual(repo.save_verification(changed,{}, {'status':'REVIEW_REQUIRED'}).id,first.id)
