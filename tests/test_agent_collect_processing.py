"""PHASE 8 offline storage/processing contracts; no network or LLM."""

import hashlib
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import uuid4

from agent_collect.extraction.schema import ExtractionStatus
from agent_collect.processing.models import (
    ProcessingClaim, ProcessingJob, ProcessingPolicy, ProcessingStatus,
)
from agent_collect.processing.worker import ProcessingWorker
from agent_collect.processing.supabase import SupabaseProcessingRepository
from agent_collect.quality_gate.models import GateStatus
from agent_collect.storage.raw import LocalRawContentStorage
from agent_collect.storage.supabase import SupabaseRawContentStorage


class _Bucket:
    def __init__(self):
        self.objects = {}
        self.uploads = 0
        self.fail_upload = False

    def exists(self, key):
        return key in self.objects

    def upload(self, key, content, options):
        if self.fail_upload:
            raise RuntimeError("provider unavailable")
        self.uploads += 1
        self.objects[key] = content

    def download(self, key):
        return self.objects[key]

    def remove(self, keys):
        for key in keys:
            self.objects.pop(key, None)


class _StorageClient:
    def __init__(self, public=False):
        self.public = public
        self.bucket = _Bucket()
        self.storage = self

    def get_bucket(self, name):
        return {"id": name, "public": self.public}

    def from_(self, name):
        return self.bucket


class StorageTests(unittest.TestCase):
    def test_private_content_addressed_put_get_duplicate_delete(self):
        client = _StorageClient()
        store = SupabaseRawContentStorage(client)
        content = b"<html>safe raw</html>"
        digest = hashlib.sha256(content).hexdigest()
        first = store.put(content, expected_sha256=digest)
        second = store.put(content, expected_sha256=digest)
        self.assertEqual(first.ref, second.ref)
        self.assertEqual(client.bucket.uploads, 1)
        self.assertTrue(first.ref.startswith("supabase:agent-raw:raw/sha256/"))
        self.assertEqual(store.get(first.ref), content)
        self.assertTrue(store.exists(first.ref))
        store.delete(first.ref)
        self.assertFalse(store.exists(first.ref))

    def test_storage_rejects_public_bucket_bad_ref_and_hash(self):
        with self.assertRaises(ValueError):
            SupabaseRawContentStorage(_StorageClient(public=True))
        store = SupabaseRawContentStorage(_StorageClient())
        with self.assertRaises(ValueError):
            store.put(b"x", expected_sha256="0" * 64)
        with self.assertRaises(ValueError):
            store.get("supabase:other:raw/sha256/aa/" + "a" * 64 + ".raw")

    def test_provider_failure_and_tamper(self):
        client = _StorageClient()
        store = SupabaseRawContentStorage(client)
        client.bucket.fail_upload = True
        with self.assertRaises(RuntimeError):
            store.put(b"x")
        client.bucket.fail_upload = False
        receipt = store.put(b"x")
        key = receipt.ref.split(":", 2)[2]
        client.bucket.objects[key] = b"tampered"
        with self.assertRaises(ValueError):
            store.get(receipt.ref)
        with self.assertRaises(ValueError):
            store.put(b"x")

    def test_local_provider_kept_and_exists(self):
        with TemporaryDirectory() as directory:
            store = LocalRawContentStorage(Path(directory))
            receipt = store.put(b"local")
            self.assertTrue(store.exists(receipt.ref))
            self.assertEqual(store.get(receipt.ref), b"local")
            store.delete(receipt.ref)
            self.assertFalse(store.exists(receipt.ref))


class _Jobs:
    def __init__(self, raw_id):
        self.job = ProcessingJob(uuid4(), raw_id, "conference-processing-v1",
                                 ProcessingStatus.PENDING, 0, datetime.now(timezone.utc))
        self.claimed = False
        self.seed_calls = 0
        self.checkpoints = []
        self.completions = []
        self.summaries = []

    def preview(self, version, ref_prefix, limit):
        return [self.job][:limit]

    def seed(self, version, ref_prefix, limit):
        self.seed_calls += 1
        return 1

    def list_due(self, version, ref_prefix, limit):
        return [self.job][:limit]

    def claim(self, job_id, *, worker_id, claim_id, policy):
        if self.claimed:
            return None
        self.claimed = True
        now = datetime.now(timezone.utc)
        return ProcessingClaim(job_id, self.job.raw_document_id, self.job.version,
                               claim_id, worker_id, now, now + timedelta(minutes=5))

    def checkpoint(self, claim, *, candidate_id, verification_id=None, gate_status=None):
        self.checkpoints.append((candidate_id, verification_id, gate_status))

    def complete(self, claim, *, outcome, policy, candidate_id=None,
                 verification_id=None, gate_status=None, error_code=None):
        self.completions.append((outcome, error_code))
        return outcome

    def record_summary(self, summary):
        self.summaries.append(summary)


class _RawStorage:
    ref_prefix = "local:"

    def __init__(self, data=b"<html>conference</html>"):
        self.data = data
        self.reads = 0

    def exists(self, ref):
        return self.data is not None

    def get(self, ref):
        self.reads += 1
        return self.data


class _Extractor:
    def __init__(self, candidate_id, status=ExtractionStatus.EXTRACTED):
        self.candidate_id = candidate_id
        self.status = status
        self.calls = 0

    def run(self, raw_id):
        self.calls += 1
        return SimpleNamespace(candidate_id=self.candidate_id, status=self.status)


class _Quality:
    def __init__(self, candidate_id, raw_id, gate=GateStatus.REVIEW_REQUIRED):
        self.candidate_id = candidate_id
        self.raw_id = raw_id
        self.gate = gate
        self.quality = self
        self.calls = 0

    def get_candidate(self, candidate_id):
        return SimpleNamespace(raw_document_id=self.raw_id)

    def run(self, candidate_id):
        self.calls += 1
        return SimpleNamespace(verification_id=uuid4(),
                               decision=SimpleNamespace(status=self.gate))


class _Normalizer:
    def __init__(self):
        self.calls = 0

    def run(self, candidate_id):
        self.calls += 1


class ProcessingTests(unittest.TestCase):
    def make_worker(self, *, gate=GateStatus.REVIEW_REQUIRED,
                    extraction_status=ExtractionStatus.EXTRACTED,
                    data=b"<html>conference</html>", hash_override=None,
                    different_raw=False):
        raw_id, candidate_id = uuid4(), uuid4()
        jobs = _Jobs(raw_id)
        document = SimpleNamespace(id=raw_id, content_ref="local:test",
                                   content_sha256=hash_override or hashlib.sha256(data or b"").hexdigest())
        docs = SimpleNamespace(get=lambda identifier: document)
        storage = _RawStorage(data)
        extractor = _Extractor(candidate_id, extraction_status)
        quality = _Quality(candidate_id, uuid4() if different_raw else raw_id, gate)
        normalizer = _Normalizer()
        worker = ProcessingWorker(jobs=jobs, documents=docs, storage=storage,
                                  extractor=extractor, normalizer=normalizer,
                                  quality_gate=quality, policy=ProcessingPolicy(),
                                  worker_id="offline-test")
        return worker, jobs, storage, extractor, normalizer, quality

    def test_dry_run_has_no_mutations_or_reads(self):
        worker, jobs, storage, extractor, _, _ = self.make_worker()
        summary = worker.run_once(dry_run=True)
        self.assertEqual(summary.due_count, 1)
        self.assertEqual(summary.claimed_count, 0)
        self.assertEqual(jobs.seed_calls, 0)
        self.assertEqual(storage.reads, 0)
        self.assertEqual(extractor.calls, 0)
        self.assertFalse(jobs.summaries)

    def test_review_reject_and_publish_ready_paths(self):
        for gate, expected in ((GateStatus.REVIEW_REQUIRED, ProcessingStatus.REVIEW_REQUIRED),
                               (GateStatus.REJECTED, ProcessingStatus.REJECTED),
                               (GateStatus.PUBLISH_READY, ProcessingStatus.SUCCEEDED)):
            with self.subTest(gate=gate):
                worker, jobs, _, extractor, normalizer, quality = self.make_worker(gate=gate)
                summary = worker.run_once()
                self.assertEqual(jobs.completions[-1][0], expected)
                self.assertEqual(len(jobs.checkpoints), 2)
                self.assertEqual((extractor.calls,normalizer.calls,quality.calls), (1,1,1))
                self.assertEqual(summary.processed_count, 1)

    def test_missing_hash_budget_and_extraction_failure(self):
        scenarios = [
            dict(data=None, error="RAW_NOT_FOUND"),
            dict(hash_override="0" * 64, error="HASH_MISMATCH"),
            dict(extraction_status=ExtractionStatus.BUDGET_BLOCKED, error="BUDGET_BLOCKED"),
            dict(extraction_status=ExtractionStatus.FAILED, error="EXTRACTION_FAILED"),
            dict(different_raw=True, error="CROSS_RAW_CACHE"),
        ]
        for scenario in scenarios:
            expected = scenario.pop("error")
            with self.subTest(error=expected):
                worker, jobs, _, _, _, _ = self.make_worker(**scenario)
                worker.run_once()
                self.assertEqual(jobs.completions[-1][1], expected)

    def test_double_claim_is_skipped_and_max_jobs_bounded(self):
        worker, jobs, _, _, _, _ = self.make_worker()
        jobs.claimed = True
        summary = worker.run_once()
        self.assertEqual((summary.due_count,summary.claimed_count), (1,0))
        self.assertFalse(jobs.completions)
        with self.assertRaises(ValueError):
            ProcessingPolicy(max_jobs_per_run=0)

    def test_retry_policy_and_version_identity(self):
        policy = ProcessingPolicy()
        self.assertEqual([policy.failure_delay_minutes(n) for n in range(1, 6)],
                         [15, 60, 360, 360, 360])
        self.assertEqual([policy.review_needed(n) for n in range(1, 5)],
                         [False, False, False, True])
        with self.assertRaises(ValueError):
            policy.failure_delay_minutes(0)
        raw_id = uuid4()
        first = ProcessingJob(uuid4(), raw_id, "conference-processing-v1",
                              ProcessingStatus.PENDING, 0, datetime.now(timezone.utc))
        second = ProcessingJob(uuid4(), raw_id, "conference-processing-v2",
                               ProcessingStatus.PENDING, 0, datetime.now(timezone.utc))
        self.assertNotEqual((first.raw_document_id,first.version),
                            (second.raw_document_id,second.version))

    def test_supabase_port_rpc_contract_has_no_caller_clock(self):
        class Call:
            def __init__(self, data):
                self.data = data

            def execute(self):
                return SimpleNamespace(data=self.data)

        class Client:
            def __init__(self):
                self.calls = []

            def rpc(self, name, args):
                self.calls.append((name,args))
                return Call([] if name in {"agent_claim_processing", "agent_list_due_processing",
                                         "agent_preview_processing"} else 0)

        client = Client()
        port = SupabaseProcessingRepository(client)
        self.assertEqual(port.preview("conference-processing-v1", "supabase:agent-raw:", 1), [])
        self.assertEqual(port.seed("conference-processing-v1", "supabase:agent-raw:", 1), 0)
        self.assertEqual(port.list_due("conference-processing-v1", "supabase:agent-raw:", 1), [])
        self.assertIsNone(port.claim(uuid4(), worker_id="worker", claim_id=uuid4(),
                                     policy=ProcessingPolicy()))
        self.assertEqual([name for name,_ in client.calls], [
            "agent_preview_processing", "agent_seed_processing_jobs",
            "agent_list_due_processing", "agent_claim_processing"])
        self.assertTrue(all("p_now" not in args for _,args in client.calls))

    def test_late_completion_exception_reconciles_to_db_success(self):
        worker, jobs, _, _, _, _ = self.make_worker(gate=GateStatus.PUBLISH_READY)
        calls = []

        def complete(*args, **kwargs):
            calls.append(kwargs["outcome"])
            if len(calls) == 1:
                raise RuntimeError("response lost after committed success")
            return ProcessingStatus.SUCCEEDED

        jobs.complete = complete
        summary = worker.run_once()
        self.assertEqual(calls, [ProcessingStatus.SUCCEEDED, ProcessingStatus.FAILED])
        self.assertEqual(summary.publish_ready_count, 1)
        self.assertEqual(summary.failed_count, 0)

    def test_sql_contract_is_agent_only_server_time_and_rpc_mutation(self):
        sql = Path("agent_collect/migrations/006_processing_foundation.sql").read_text()
        self.assertNotIn("drop table",sql.lower())
        self.assertNotIn("truncate",sql.lower())
        self.assertNotIn("service_role update",sql.lower())
        self.assertIn("clock_timestamp()",sql)
        self.assertIn("for update",sql.lower())
        self.assertIn("unique (raw_document_id, processing_version)",sql)
        self.assertIn("security definer set search_path = pg_catalog",sql)
        self.assertIn("agent_processing_completions",sql)
        self.assertIn("agent_preview_processing",sql)
        self.assertIn("v_job.verification_id is not null",sql)

    def test_existing_extraction_normalization_quality_end_to_end_offline(self):
        from agent_collect.config.settings import load_settings
        from agent_collect.extraction.providers import FakeExtractionProvider
        from agent_collect.extraction.runner import ExtractionRunner
        from agent_collect.normalization.runner import NormalizationRunner
        from agent_collect.quality_gate.runner import QualityGateRunner
        from agent_collect.repositories.extraction import InMemoryExtractionRepository
        from agent_collect.repositories.normalization import (
            InMemoryNormalizationRepository, NormalizationState,
        )
        from agent_collect.repositories.quality import InMemoryQualityRepository
        from agent_collect.schemas.models import RawDocument, Source, SourceType
        from agent_collect.schemas.status import SourceStatus
        from tests.test_agent_collect_extraction import HTML, URL, payload

        class LazyNormalization(InMemoryNormalizationRepository):
            def __init__(self, extracted):
                super().__init__()
                self.extracted = extracted

            def get(self, candidate_id):
                if candidate_id not in self.rows and candidate_id in self.extracted.rows:
                    self.rows[candidate_id] = NormalizationState(
                        self.extracted.rows[candidate_id], {})
                return super().get(candidate_id)

        with TemporaryDirectory() as directory:
            storage = LocalRawContentStorage(Path(directory))
            receipt = storage.put(HTML)
            source_id, run_id, raw_id = uuid4(), uuid4(), uuid4()
            document = RawDocument(raw_id, run_id, source_id, URL, receipt.ref,
                                   receipt.content_sha256, datetime.now(timezone.utc),
                                   media_type="text/html")
            documents = SimpleNamespace(get=lambda identifier: document)
            source = Source(source_id, "Fixture Conference", URL, SourceType.OFFICIAL,
                            SourceStatus.HOLD, datetime.now(timezone.utc), canonical_url=URL)
            sources = SimpleNamespace(get=lambda identifier: source)
            extracted = InMemoryExtractionRepository()
            normalized = LazyNormalization(extracted)
            quality_rows = InMemoryQualityRepository()
            quality_rows.rows = extracted.rows
            provider = FakeExtractionProvider(payload())
            settings = load_settings(environ={"AGENT_LLM_ENABLED": "true",
                                              "AGENT_LLM_MODEL": "gpt-5.4-nano"})
            extractor = ExtractionRunner(documents=documents, storage=storage,
                                         repository=extracted, provider=provider,
                                         settings=settings)
            gate = QualityGateRunner(sources=sources, documents=documents,
                                     quality=quality_rows,
                                     service_duplicates=SimpleNamespace(list_records=lambda: []),
                                     settings=settings)
            jobs = _Jobs(raw_id)
            worker = ProcessingWorker(
                jobs=jobs, documents=documents, storage=storage,
                extractor=extractor, normalizer=NormalizationRunner(normalized),
                quality_gate=gate, policy=ProcessingPolicy(), worker_id="offline-e2e",
            )
            summary = worker.run_once()
            self.assertEqual(len(provider.calls), 1)
            self.assertEqual(len(extracted.rows), 1)
            candidate_id = next(iter(extracted.rows))
            self.assertTrue(normalized.rows[candidate_id].normalized_payload)
            self.assertEqual(len(quality_rows.verifications), 1)
            self.assertEqual(summary.review_count, 1)
            self.assertEqual(summary.publish_ready_count, 0)
            self.assertFalse(quality_rows.queue)


if __name__ == "__main__":
    unittest.main()
