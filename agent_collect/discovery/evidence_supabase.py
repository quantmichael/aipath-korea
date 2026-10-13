"""Read-only join from a Discovery result to existing Agent evidence.

Callers supply a reviewed officiality assertion and access snapshot. This
adapter never infers either from a domain, fetches a website, or writes rows.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from agent_collect.discovery.evidence import (
    EvidenceAccessSnapshot, EvidenceOutcome, EvidenceState, OfficialEvidencePipeline,
    OfficialSourceReview, OpportunityIdentity, performance_metrics,
)
from agent_collect.extraction.schema import ExtractionStatus
from agent_collect.extraction.schema import StoredExtraction
from agent_collect.quality_gate.models import QualityVerification
from agent_collect.registry.approval import source_is_collectible
from agent_collect.repositories.discovery_supabase import SupabaseDiscoveryRepository
from agent_collect.repositories.extraction_supabase import SupabaseExtractionRepository
from agent_collect.repositories.supabase import (
    SupabaseCollectionRunRepository, SupabaseRawDocumentRepository,
    SupabaseSourceRepository,
)
from agent_collect.schemas.models import SourceType
from agent_collect.schemas.status import VerificationStatus


def _first(query: Any) -> dict[str, Any] | None:
    return next(iter(query.limit(1).execute().data or []), None)


def _timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("database timestamp lacks timezone")
    return result


@dataclass(frozen=True, slots=True)
class IntegratedEvidence:
    cohort: str
    evidence: EvidenceOutcome
    processing_job_id: UUID | None
    gate_status: str | None
    queue_status: str | None
    publish_ready: bool
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvidenceIntegrationMetrics:
    cohort: str
    search_result_count: int
    official_result_count: int
    evidence_resolved_count: int
    verified_opportunity_count: int
    active_opportunity_count: int
    upcoming_opportunity_count: int
    duplicate_opportunity_count: int
    review_required_count: int
    publish_ready_count: int
    evidence_resolution_rate: float
    verified_opportunity_yield: float
    publish_ready_conversion_rate: float


def integration_metrics(rows: tuple[IntegratedEvidence, ...], *, cohort: str) -> EvidenceIntegrationMetrics:
    """Rates: resolved/official, verified/all, publish-ready/verified."""
    if cohort not in {"synthetic", "operational"} or any(row.cohort != cohort for row in rows):
        raise ValueError("evidence cohorts must not be mixed")
    base = performance_metrics(tuple(row.evidence for row in rows))
    ready = sum(row.publish_ready for row in rows)
    review = sum(row.evidence.state is EvidenceState.REVIEW_REQUIRED or
                 row.gate_status == "REVIEW_REQUIRED" or bool(row.issues)
                 for row in rows)
    return EvidenceIntegrationMetrics(
        cohort, base.search_result_count, base.official_result_count,
        base.evidence_resolved_count,
        base.verified_opportunity_count, base.active_opportunity_count,
        base.upcoming_opportunity_count, base.duplicate_opportunity_count,
        review, ready, base.evidence_resolution_rate,
        base.verified_opportunity_yield,
        ready / base.verified_opportunity_count if base.verified_opportunity_count else 0.0,
    )


class SupabaseEvidenceIntegration:
    """Follow existing FK edges only; explicit IDs avoid ambiguous latest-row joins."""

    def __init__(self, client: Any, storage: Any) -> None:
        self.client = client
        self.storage = storage
        self.sources = SupabaseSourceRepository(client)
        self.runs = SupabaseCollectionRunRepository(client)
        self.documents = SupabaseRawDocumentRepository(client)
        self.discovery = SupabaseDiscoveryRepository(client)
        self.pipeline = OfficialEvidencePipeline()

    def assess(self, result_id: UUID, raw_document_id: UUID, *, now: datetime,
               cohort: str,
               processing_job_id: UUID | None = None,
               review: OfficialSourceReview | None = None,
               access: EvidenceAccessSnapshot | None = None,
               previous: tuple[OpportunityIdentity, ...] = (),
               previous_raw_sha256: str | None = None) -> IntegratedEvidence:
        if cohort not in {"synthetic", "operational"}:
            raise ValueError("explicit evidence cohort required")
        result = self.discovery.get_result(result_id)
        if result is None:
            raise KeyError("unknown Discovery result")
        issues: list[str] = []
        link = _first(self.client.table("agent_source_candidates")
                      .select("id,query_id,status,promoted_source_id")
                      .eq("search_result_id", str(result_id)))
        source = None
        if (link and link.get("query_id") == str(result.query_id) and
                link.get("status") == "PROMOTED" and link.get("promoted_source_id")):
            source = self.sources.get(UUID(link["promoted_source_id"]))
            if (source is None or source.provenance.get("search_result_id") != str(result_id) or
                    source.provenance.get("source_candidate_id") != link["id"]):
                source = None
                issues.append("discovery_source_link_mismatch")
        else:
            issues.append("promoted_source_link_missing")

        raw = self.documents.get(raw_document_id)
        run = self.runs.get(raw.run_id) if raw else None
        content = None
        if (source and raw and run and review and access and
                review.source_id == source.id and source.source_type is SourceType.OFFICIAL):
            raw_allowed, _ = access.allows(source, raw.source_url, now=now)
            result_allowed, _ = access.allows(
                source, result.canonical_url or result.original_url, now=now)
            if (source_is_collectible(source) and raw_allowed and result_allowed and
                    raw.source_id == source.id and run.source_id == source.id):
                try:
                    content = self.storage.get(raw.content_ref)
                except Exception:
                    issues.append("raw_storage_unavailable")
            else:
                issues.append("raw_access_not_approved")
        elif source and raw and run:
            issues.append("official_review_or_access_snapshot_missing")

        extraction = verification = None
        gate_status = queue_status = None
        job = None
        if processing_job_id is not None:
            job = _first(self.client.table("agent_processing_jobs")
                         .select("id,raw_document_id,candidate_id,verification_id,status,gate_status")
                         .eq("id", str(processing_job_id)))
            if job is None or job.get("raw_document_id") != str(raw_document_id):
                issues.append("processing_job_raw_mismatch")
                job = None
            elif job.get("status") not in {"SUCCEEDED", "REVIEW_REQUIRED", "REJECTED"}:
                issues.append("processing_job_not_terminal")
            elif job.get("candidate_id"):
                candidate = _first(self.client.table("agent_candidates").select("*")
                                   .eq("id", job["candidate_id"]))
                if candidate and candidate.get("raw_document_id") == str(raw_document_id):
                    try:
                        extraction = SupabaseExtractionRepository._row(candidate)
                    except (KeyError, ValueError, TypeError):
                        issues.append("processing_candidate_invalid")
                    if (candidate.get("status") in {"REVIEW_REQUIRED", "REJECTED"} or
                            extraction and extraction.status is ExtractionStatus.REVIEW_REQUIRED):
                        issues.append("candidate_review_required_protected")
                else:
                    issues.append("processing_candidate_raw_mismatch")
                if extraction and job.get("verification_id"):
                    row = _first(self.client.table("agent_verifications").select("*")
                                 .eq("id", job["verification_id"]))
                    if row and row.get("candidate_id") == str(extraction.id):
                        verification = QualityVerification(
                            extraction.id, row["result"], row["confidence"],
                            tuple(row.get("reasons") or ()), row.get("checks") or {},
                            tuple(row.get("evidence_refs") or ()),
                            _timestamp(row["verified_at"]),
                        )
                        gate_status = job.get("gate_status")
                        recorded = (row.get("checks") or {}).get("gate_decision") or {}
                        if recorded.get("status") != gate_status:
                            issues.append("gate_decision_mismatch")
                    else:
                        issues.append("processing_verification_mismatch")
            else:
                issues.append("processing_candidate_missing")

        evidence = self.pipeline.assess(
            result, now=now, source=source, review=review, access=access,
            run=run, raw=raw, content=content, extraction=extraction,
            verification=verification, previous=previous,
            previous_raw_sha256=previous_raw_sha256)
        ready = False
        if extraction and verification and job and gate_status == "PUBLISH_READY":
            queue = _first(self.client.table("agent_publish_queue")
                           .select("candidate_id,verification_id,status")
                           .eq("candidate_id", str(extraction.id)))
            if queue and queue.get("verification_id") == job.get("verification_id"):
                queue_status = queue.get("status")
            else:
                issues.append("publish_queue_verification_mismatch")
            ready = bool(
                evidence.verified_opportunity and not issues and
                job.get("status") == "SUCCEEDED" and
                verification.status == VerificationStatus.VERIFIED.value and
                queue_status == "READY"
            )
            if not ready and evidence.verified_opportunity is False:
                issues.append("gate_ready_without_official_evidence")
        return IntegratedEvidence(cohort, evidence, processing_job_id if job else None,
                                  gate_status, queue_status, ready,
                                  tuple(dict.fromkeys(issues)))


class SupabasePreGateEvidence:
    """Read-only official evidence check before the existing Gate can enqueue.

    Reviews and access snapshots must be explicitly supplied by a trusted caller.
    No operational snapshot loader exists yet, so an unconfigured runtime fails
    closed instead of inferring permission from an official-looking domain.
    """

    def __init__(self, client: Any, storage: Any, *,
                 reviews: dict[UUID, OfficialSourceReview] | None = None,
                 access: dict[UUID, EvidenceAccessSnapshot] | None = None) -> None:
        self.client = client
        self.storage = storage
        self.reviews = reviews or {}
        self.access = access or {}
        self.sources = SupabaseSourceRepository(client)
        self.runs = SupabaseCollectionRunRepository(client)
        self.documents = SupabaseRawDocumentRepository(client)
        self.discovery = SupabaseDiscoveryRepository(client)
        self.pipeline = OfficialEvidencePipeline()

    def __call__(self, extraction: StoredExtraction,
                 verification: QualityVerification):
        source = self.sources.get(extraction.source_id)
        raw = self.documents.get(extraction.raw_document_id)
        run = self.runs.get(extraction.run_id)
        if (source is None or raw is None or run is None or
                source.source_type is not SourceType.OFFICIAL or
                not source_is_collectible(source) or
                raw.source_id != source.id or run.source_id != source.id or
                raw.run_id != run.id):
            raise ValueError("official Source or exact provenance unavailable")
        approval = (source.provenance.get("source_review") or {}).get("approval") or {}
        events = (self.client.table("agent_source_review_events")
                  .select("id,review_decision,officiality_confirmed,collection_allowed,reviewed_at")
                  .eq("source_id", str(source.id)).eq("review_decision", "APPROVED")
                  .limit(20).execute().data or [])
        if not any(event["officiality_confirmed"] is True and
                   event["collection_allowed"] is True and
                   _timestamp(event["reviewed_at"]) == _timestamp(approval["reviewed_at"])
                   for event in events):
            raise ValueError("matching immutable approval audit missing")
        review, access = self.reviews.get(source.id), self.access.get(source.id)
        if review is None or access is None:
            raise ValueError("reviewed officiality or access snapshot unavailable")
        result_id = source.provenance.get("search_result_id")
        link_id = source.provenance.get("source_candidate_id")
        if not isinstance(result_id, str) or not isinstance(link_id, str):
            raise ValueError("promoted Discovery provenance missing")
        result = self.discovery.get_result(UUID(result_id))
        link = _first(self.client.table("agent_source_candidates")
                      .select("id,query_id,status,promoted_source_id")
                      .eq("search_result_id", result_id).eq("id", link_id))
        if (result is None or link is None or link.get("status") != "PROMOTED" or
                link.get("promoted_source_id") != str(source.id) or
                link.get("query_id") != str(result.query_id)):
            raise ValueError("exact Discovery promotion link missing")
        return self.pipeline.assess(
            result, now=datetime.now(timezone.utc), source=source,
            review=review, access=access, run=run, raw=raw,
            content=self.storage.get(raw.content_ref), extraction=extraction,
            verification=verification,
        )
