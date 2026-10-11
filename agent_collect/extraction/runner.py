"""Manual RawDocument-to-Conference extraction; never publishes to Service DB."""

import hashlib
from datetime import datetime, timezone
from uuid import UUID, uuid4
from agent_collect.budget.reservations import BudgetReservations, SupabaseBudgetReservations

from agent_collect.config.settings import AgentSettings
from agent_collect.extraction.budget import (
    ExtractionBlockReason, ExtractionBudgetGuard, ExtractionBudgetPolicy,
)
from agent_collect.extraction.preprocess import preprocess_html
from agent_collect.extraction.cache import UnsafeCache, reuse_artifact
from agent_collect.extraction.prompts import PROMPT_VERSION
from agent_collect.extraction.providers import (
    ExtractionProvider, ExtractionProviderError, ExtractionRequest,
)
from agent_collect.extraction.schema import (
    EXTRACTION_VERSION, ExtractionOutcome, ExtractionStatus, StoredExtraction,
    validate_provider_payload,
)
from agent_collect.extraction.validation import validate_candidate
from agent_collect.repositories.contracts import RawDocumentRepository
from agent_collect.repositories.extraction import ExtractionRepository
from agent_collect.storage.raw import RawContentStorage


class ExtractionRunner:
    def __init__(self, *, documents: RawDocumentRepository, storage: RawContentStorage,
                 repository: ExtractionRepository, provider: ExtractionProvider,
                 settings: AgentSettings, reservations: BudgetReservations | None = None) -> None:
        self.documents = documents
        self.storage = storage
        self.repository = repository
        self.provider = provider
        self.settings = settings
        # Every Supabase OpenAI call uses the shared ledger, even while the AI
        # planner is OFF. A missing migration 011 RPC fails closed.
        self.reservations = reservations or (
            SupabaseBudgetReservations(repository.client)
            if provider.name == "openai" and getattr(repository, "client", None) is not None
            else None)
        self.guard = ExtractionBudgetGuard(
            repository, policy=ExtractionBudgetPolicy(
                daily_limit=settings.extraction_daily_llm_limit,
                monthly_limit=settings.extraction_monthly_llm_limit,
                max_retries=settings.extraction_max_retries,
                max_input_chars=settings.extraction_max_input_chars,
                timezone_name=settings.extraction_budget_timezone,
            ), provider_enabled=settings.llm_enabled,
            credential_present=provider.credential_present,
        )

    def run_for_processing(self, raw_document_id: UUID, *, processing_job_id: UUID,
                           processing_version: str) -> ExtractionOutcome:
        return self.run(raw_document_id, processing_job_id=processing_job_id,
                        processing_version=processing_version)

    def run(self, raw_document_id: UUID, *, retry_count: int = 0,
            processing_job_id: UUID | None = None,
            processing_version: str | None = None) -> ExtractionOutcome:
        document = self.documents.get(raw_document_id)
        if document is None:
            raise KeyError("RawDocument not found")
        extracted_at = datetime.now(timezone.utc)
        input_hash = ""
        try:
            if (document.media_type or "").split(";", 1)[0].strip().lower() != "text/html":
                raise ValueError("UNSUPPORTED_MEDIA_TYPE")
            raw = self.storage.get(document.content_ref)
            if hashlib.sha256(raw).hexdigest() != document.content_sha256:
                raise ValueError("RAW_HASH_MISMATCH")
            prepared = preprocess_html(raw, source_url=document.source_url,
                                       max_input_chars=self.settings.extraction_max_input_chars)
            if not prepared.selected_text.strip():
                raise ValueError("NO_EXTRACTABLE_TEXT")
            input_hash = hashlib.sha256(prepared.model_input.encode("utf-8")).hexdigest()
        except Exception as error:
            code = (str(error) if isinstance(error, ValueError) and str(error) in
                    {"NO_EXTRACTABLE_TEXT", "UNSUPPORTED_MEDIA_TYPE"} else type(error).__name__)
            self.repository.log(event_type="EXTRACTION_FAILED", source_id=document.source_id,
                                run_id=document.run_id, details={"raw_document_id": str(document.id),
                                                                 "code": code})
            return ExtractionOutcome(document.id, document.source_id, document.run_id,
                                     ExtractionStatus.FAILED, None, None,
                                     self.provider.name, self.provider.model, PROMPT_VERSION,
                                     extracted_at, input_hash, (code,))
        fingerprint = hashlib.sha256(
            f"{document.content_sha256}:{EXTRACTION_VERSION}:{PROMPT_VERSION}:{self.provider.name}:{self.provider.model}".encode()
        ).hexdigest()
        decision = self.guard.check(raw_document_id=document.id, fingerprint=fingerprint,
                                    input_chars=len(prepared.model_input), retry_count=retry_count)
        if (processing_job_id is not None and decision.cached is None and
                self.repository.find_by_content_hash(document.content_sha256) is not None):
            # Processing cannot silently bill for an incompatible existing artifact.
            self.repository.log(event_type="EXTRACTION_CACHE_REJECTED",
                                source_id=document.source_id, run_id=document.run_id,
                                details={"raw_document_id": str(document.id),
                                         "code": "CACHE_VERSION_MISMATCH", "llm_calls": 0})
            return ExtractionOutcome(document.id, document.source_id, document.run_id,
                                     ExtractionStatus.FAILED, None, None, self.provider.name,
                                     self.provider.model, PROMPT_VERSION, extracted_at, input_hash,
                                     ("CACHE_VERSION_MISMATCH",))
        if decision.cached is not None:
            try:
                stored, candidate, reused = reuse_artifact(
                    decision.cached, document=document, documents=self.documents,
                    raw=raw, prepared=prepared, fingerprint=fingerprint,
                    provider=self.provider, settings=self.settings, extracted_at=extracted_at,
                    processing_job_id=processing_job_id, processing_version=processing_version)
                if reused:
                    stored = self.repository.save(stored)
                self.repository.log(
                    event_type="EXTRACTION_CACHE_REUSED" if reused else "EXTRACTION_CACHE_HIT",
                    source_id=document.source_id, run_id=document.run_id,
                    details={"raw_document_id": str(document.id), "candidate_id": str(stored.id),
                             "cache_hit": True, "cache_reused": reused, "llm_saved": True,
                             "reused_from_candidate_id": str(decision.cached.id),
                             "processing_job_id": str(processing_job_id) if processing_job_id else None})
                return ExtractionOutcome(document.id, document.source_id, document.run_id,
                                         ExtractionStatus.CACHED, stored.id, candidate,
                                         self.provider.name, self.provider.model, PROMPT_VERSION,
                                         extracted_at, input_hash, (decision.reason.value,))
            except (UnsafeCache, ValueError, TypeError, KeyError) as error:
                code = str(error) if isinstance(error, UnsafeCache) else "CACHE_INVALID_ARTIFACT"
                self.repository.log(event_type="EXTRACTION_CACHE_REJECTED",
                                    source_id=document.source_id, run_id=document.run_id,
                                    details={"raw_document_id": str(document.id), "code": code,
                                             "cache_hit": True, "cache_reused": False, "llm_calls": 0})
                return ExtractionOutcome(document.id, document.source_id, document.run_id,
                                         ExtractionStatus.FAILED, None, None, self.provider.name,
                                         self.provider.model, PROMPT_VERSION, extracted_at, input_hash, (code,))
        if not decision.allowed:
            self.repository.log(event_type="EXTRACTION_BLOCKED", source_id=document.source_id,
                                run_id=document.run_id, details={"raw_document_id": str(document.id),
                                                                 "reason": decision.reason.value,
                                                                 "daily_usage": decision.daily_usage,
                                                                 "monthly_usage": decision.monthly_usage})
            status = (ExtractionStatus.CACHED if decision.cached is not None
                      else ExtractionStatus.BUDGET_BLOCKED)
            return ExtractionOutcome(document.id, document.source_id, document.run_id,
                                     status, decision.cached.id if decision.cached else None,
                                     None, self.provider.name, self.provider.model,
                                     PROMPT_VERSION, extracted_at, input_hash,
                                     (decision.reason.value,))
        request = ExtractionRequest(
            source_url=document.source_url, content=prepared,
            max_output_tokens=self.settings.extraction_max_output_tokens,
            timeout_seconds=self.settings.extraction_timeout_seconds,
        )
        reservation_id = None
        if self.reservations is not None:
            reservation_id = self.reservations.reserve(
                kind="openai", scope="extraction",
                key=f"extraction:{fingerprint}:{max(retry_count, decision.previous_attempts)}")
            self.reservations.dispatch(reservation_id)
        # Durable audit before the billable call. DB failure aborts the call.
        self.repository.log(event_type="EXTRACTION_LLM_ATTEMPT", source_id=document.source_id,
                            run_id=document.run_id, details={"raw_document_id": str(document.id),
                                                             "fingerprint": fingerprint,
                                                             "provider": self.provider.name,
                                                             "model": self.provider.model,
                                                             "prompt_version": PROMPT_VERSION,
                                                             "input_hash": input_hash,
                                                             **({"budget_reservation_id": str(reservation_id)}
                                                                if reservation_id else {})})
        try:
            response = self.provider.extract(request)
            if reservation_id is not None:
                self.reservations.finish(reservation_id, status="SUCCEEDED")
            candidate = validate_provider_payload(response.payload, source_url=document.source_url)
            model_document_confidence = candidate.document_confidence
            candidate = validate_candidate(candidate, prepared=prepared,
                                           source_url=document.source_url,
                                           max_evidence_chars=self.settings.extraction_max_evidence_chars)
            status = (ExtractionStatus.NOT_CONFERENCE if not candidate.is_conference else
                      ExtractionStatus.REVIEW_REQUIRED if candidate.review_reasons else
                      ExtractionStatus.EXTRACTED)
            stored = StoredExtraction(
                id=uuid4(), raw_document_id=document.id, run_id=document.run_id,
                source_id=document.source_id, source_url=document.source_url,
                fingerprint=fingerprint, status=status, payload=candidate.as_payload(),
                provenance={"schema_version": EXTRACTION_VERSION,
                            "extraction_version": EXTRACTION_VERSION,
                            "extraction_status": status.value,
                            "provider": self.provider.name, "model": self.provider.model,
                            "prompt_version": PROMPT_VERSION, "input_hash": input_hash,
                            "content_sha256": document.content_sha256,
                            "extracted_at": extracted_at.isoformat(),
                            "input_truncated": prepared.truncated,
                            "model_document_confidence": model_document_confidence,
                            "validated_document_confidence": candidate.document_confidence,
                            "confidence_clamped": candidate.document_confidence < model_document_confidence,
                            "input_tokens": response.input_tokens,
                            "output_tokens": response.output_tokens},
            )
            self.repository.save(stored)
            try:
                self.repository.log(event_type="EXTRACTION_COMPLETED", source_id=document.source_id,
                                    run_id=document.run_id, details={"raw_document_id": str(document.id),
                                                                     "candidate_id": str(stored.id),
                                                                     "status": status.value,
                                                                     "model": self.provider.model},
                                    input_tokens=response.input_tokens,
                                    output_tokens=response.output_tokens)
            except Exception:
                # Candidate is already durable. A secondary log failure must not turn a
                # persisted result into a misleading extraction failure.
                return ExtractionOutcome(document.id, document.source_id, document.run_id,
                                         status, stored.id, candidate,
                                         self.provider.name, self.provider.model, PROMPT_VERSION,
                                         extracted_at, input_hash,
                                         candidate.review_reasons + ("COMPLETION_LOG_FAILED",))
            return ExtractionOutcome(document.id, document.source_id, document.run_id,
                                     status, stored.id, candidate,
                                     self.provider.name, self.provider.model, PROMPT_VERSION,
                                     extracted_at, input_hash, candidate.review_reasons)
        except Exception as error:
            code = error.code if isinstance(error, ExtractionProviderError) else type(error).__name__
            if reservation_id is not None:
                # A successful model response may fail local validation/storage;
                # its reservation is already SUCCEEDED and cannot be changed.
                try:
                    uncertain = code in {"TIMEOUT", "PROVIDER_ERROR"}
                    self.reservations.finish(reservation_id,
                                             status="UNKNOWN" if uncertain else "FAILED",
                                             error_code=code.upper()[:80])
                except RuntimeError:
                    pass
            self.repository.log(event_type="EXTRACTION_FAILED", source_id=document.source_id,
                                run_id=document.run_id, details={"raw_document_id": str(document.id),
                                                                 "code": code,
                                                                 "model": self.provider.model})
            return ExtractionOutcome(document.id, document.source_id, document.run_id,
                                     ExtractionStatus.FAILED, None, None,
                                     self.provider.name, self.provider.model, PROMPT_VERSION,
                                     extracted_at, input_hash, (code,))
