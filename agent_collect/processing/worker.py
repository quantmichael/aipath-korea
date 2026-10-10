"""One-shot RawDocument → existing extraction/normalization/quality services."""

import hashlib
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from agent_collect.extraction.schema import ExtractionStatus
from agent_collect.processing.models import (
    ProcessingClaim, ProcessingError, ProcessingPolicy, ProcessingStatus,
    ProcessingSummary,
)
from agent_collect.processing.repository import ProcessingRepository
from agent_collect.quality_gate.models import GateStatus
from agent_collect.repositories.contracts import RawDocumentRepository
from agent_collect.storage.raw import RawContentStorage


class ProcessingWorker:
    def __init__(self, *, jobs: ProcessingRepository, documents: RawDocumentRepository,
                 storage: RawContentStorage, extractor: Any, normalizer: Any,
                 quality_gate: Any, policy: ProcessingPolicy, worker_id: str) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        self.jobs = jobs
        self.documents = documents
        self.storage = storage
        self.extractor = extractor
        self.normalizer = normalizer
        self.quality_gate = quality_gate
        self.policy = policy
        self.worker_id = worker_id

    def run_once(self, *, dry_run: bool = False) -> ProcessingSummary:
        started = datetime.now(timezone.utc)
        if dry_run:
            due = tuple(self.jobs.preview(self.policy.version,
                                          self.storage.ref_prefix,
                                          self.policy.max_jobs_per_run))
            return ProcessingSummary(self.worker_id, started, datetime.now(timezone.utc),
                                     True, len(due), 0, 0, 0, 0, 0, 0, 0, due)
        self.jobs.seed(self.policy.version, self.storage.ref_prefix,
                       self.policy.max_jobs_per_run)
        due = tuple(self.jobs.list_due(self.policy.version, self.storage.ref_prefix,
                                       self.policy.max_jobs_per_run))
        claimed = processed = ready = review = rejected = failed = budget = 0
        for job in due:
            if job.id is None:
                raise RuntimeError("unpersisted job cannot be claimed")
            claim = self.jobs.claim(job.id, worker_id=self.worker_id,
                                    claim_id=uuid4(), policy=self.policy)
            if claim is None:
                continue
            claimed += 1
            outcome = self._process(claim)
            if outcome is ProcessingStatus.SUCCEEDED:
                processed += 1
                ready += 1
            elif outcome is ProcessingStatus.REVIEW_REQUIRED:
                processed += 1
                review += 1
            elif outcome is ProcessingStatus.REJECTED:
                processed += 1
                rejected += 1
            elif outcome is ProcessingStatus.BUDGET_BLOCKED:
                budget += 1
            else:
                failed += 1
        summary = ProcessingSummary(self.worker_id, started, datetime.now(timezone.utc),
                                    False, len(due), claimed, processed, ready, review,
                                    rejected, failed, budget, due)
        self.jobs.record_summary(summary)
        return summary

    def _finish(self, claim: ProcessingClaim, outcome: ProcessingStatus,
                *, candidate_id: UUID | None = None,
                verification_id: UUID | None = None, gate_status: str | None = None,
                error_code: str | None = None) -> ProcessingStatus:
        return self.jobs.complete(
            claim, outcome=outcome, policy=self.policy,
            candidate_id=candidate_id, verification_id=verification_id,
            gate_status=gate_status, error_code=error_code,
        )

    def _process(self, claim: ProcessingClaim) -> ProcessingStatus:
        candidate_id: UUID | None = None
        verification_id: UUID | None = None
        gate_status: str | None = None
        stage = "RAW"
        try:
            document = self.documents.get(claim.raw_document_id)
            if document is None or document.id != claim.raw_document_id:
                return self._finish(claim, ProcessingStatus.FAILED,
                                    error_code=ProcessingError.RAW_NOT_FOUND.value)
            if not self.storage.exists(document.content_ref):
                return self._finish(claim, ProcessingStatus.FAILED,
                                    error_code=ProcessingError.RAW_NOT_FOUND.value)
            content = self.storage.get(document.content_ref)
            if hashlib.sha256(content).hexdigest() != document.content_sha256:
                return self._finish(claim, ProcessingStatus.FAILED,
                                    error_code=ProcessingError.HASH_MISMATCH.value)
            stage = "EXTRACTION"
            processing_extract = getattr(self.extractor, "run_for_processing", None)
            extraction = (processing_extract(document.id, processing_job_id=claim.job_id,
                                             processing_version=claim.version)
                          if processing_extract else self.extractor.run(document.id))
            if extraction.status is ExtractionStatus.BUDGET_BLOCKED:
                return self._finish(claim, ProcessingStatus.BUDGET_BLOCKED,
                                    error_code="BUDGET_BLOCKED")
            if extraction.status is ExtractionStatus.FAILED or extraction.candidate_id is None:
                provider_codes = {"TIMEOUT", "RATE_LIMIT", "AUTHENTICATION_FAILURE",
                                  "PROVIDER_ERROR", "SDK_UNAVAILABLE", "INCOMPLETE_RESPONSE",
                                  "MALFORMED_RESPONSE"}
                errors = set(getattr(extraction, "errors", ()) or ())
                cache_errors = sorted(error for error in errors if error.startswith("CACHE_"))
                code = (ProcessingError.PROVIDER_FAILED if errors & provider_codes
                        else ProcessingError.EXTRACTION_FAILED)
                return self._finish(claim, ProcessingStatus.FAILED,
                                    error_code=cache_errors[0] if cache_errors else code.value)
            candidate_id = extraction.candidate_id
            # The existing extraction cache can return a Candidate from a
            # different RawDocument. Never process that Candidate as this job.
            stored = self.quality_gate.quality.get_candidate(candidate_id)
            if stored is None or stored.raw_document_id != document.id:
                return self._finish(claim, ProcessingStatus.FAILED,
                                    error_code=ProcessingError.CROSS_RAW_CACHE.value)
            self.jobs.checkpoint(claim, candidate_id=candidate_id)
            stage = "NORMALIZATION"
            self.normalizer.run(candidate_id)
            stage = "QUALITY_GATE"
            quality = self.quality_gate.run(candidate_id)
            verification_id = quality.verification_id
            gate_status = quality.decision.status.value
            self.jobs.checkpoint(claim, candidate_id=candidate_id,
                                 verification_id=verification_id,
                                 gate_status=gate_status)
            outcome = {
                GateStatus.PUBLISH_READY.value: ProcessingStatus.SUCCEEDED,
                GateStatus.REVIEW_REQUIRED.value: ProcessingStatus.REVIEW_REQUIRED,
                GateStatus.REJECTED.value: ProcessingStatus.REJECTED,
            }[gate_status]
            return self._finish(claim, outcome, candidate_id=candidate_id,
                                verification_id=verification_id,
                                gate_status=gate_status)
        except Exception as error:
            code = {
                "EXTRACTION": ProcessingError.PROVIDER_FAILED,
                "NORMALIZATION": ProcessingError.VALIDATION_FAILED,
                "QUALITY_GATE": ProcessingError.QUALITY_GATE_FAILED,
            }.get(stage, ProcessingError.INTERNAL_ERROR)
            if stage == "RAW":
                code = (ProcessingError.RAW_NOT_FOUND if isinstance(error, FileNotFoundError)
                        else ProcessingError.HASH_MISMATCH if isinstance(error, ValueError)
                        else ProcessingError.PROVIDER_FAILED)
            # A late exception may follow a committed Gate checkpoint. The DB
            # completion RPC reconciles that checkpoint before any backoff.
            return self._finish(claim, ProcessingStatus.FAILED,
                                error_code=code.value)
