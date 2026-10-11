"""Synthetic Agent E2E against the *separate* Preview Supabase project only.

The key files are supplied by the operator, never logged, and are not checked
into Git. No Tavily, OpenAI, or live source HTTP call is made.
"""

import argparse
import hashlib
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from supabase import create_client

from agent_collect.collectors.planner import CollectionPlanner
from agent_collect.collectors.runner import CollectionRunner
from agent_collect.collectors.transport import HttpResponse
from agent_collect.config.settings import load_settings
from agent_collect.discovery.auto_approval import (
    AUTO_REVIEWER, ApprovalScope, AutoApprovalAssessor, AutoApprovalExecutor,
)
from agent_collect.discovery.evaluation import RuleBasedEvaluator
from agent_collect.discovery.models import DiscoveryQuery
from agent_collect.discovery.providers import FakeSearchProvider, SearchHit
from agent_collect.discovery.runner import DiscoveryRunner, PromotionGate
from agent_collect.registry.approval import SourceApprovalService, source_is_collectible
from agent_collect.registry.service import SourceRegistryService
from agent_collect.repositories.discovery_supabase import SupabaseDiscoveryRepository
from agent_collect.repositories.source_review_supabase import SupabaseSourceReviewRepository
from agent_collect.repositories.supabase import (
    SupabaseCollectionRunRepository, SupabaseRawDocumentRepository,
    SupabaseSourceRepository,
)
from agent_collect.schemas.models import CollectionMethod, SourceType
from agent_collect.schemas.status import SourceStatus
from agent_collect.scheduling.policy import CollectionSchedulePolicy
from agent_collect.scheduling.supabase import SupabaseCollectionScheduleRepository
from agent_collect.scheduling.worker import CollectionWorker
from agent_collect.storage.supabase import SupabaseRawContentStorage


PREVIEW_REF = "qonlnoyppopyztulvgzv"
PREVIEW_URL = f"https://{PREVIEW_REF}.supabase.co"
BUCKET = "agent-raw-preview"
ORIGIN = "https://official.example.org"
OFFICIALITY = b"Official AI Conference organization website."
TERMS = b"Explicitly permits automated private collection of public conference calls."
RAW = b"<html><title>AI Conference</title><body>Official AI Conference call</body></html>"
FIXTURE_URLS: list[str] = []


def read_key(path: Path) -> str:
    metadata = path.stat()
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError("key file must not be group/world readable")
    key = path.read_text().strip()
    if not key or "\n" in key:
        raise ValueError("invalid one-line key file")
    return key


class FixtureTransport:
    def __init__(self, url: str, raw: bytes) -> None:
        self.url = url
        self.raw = raw
        self.terms = TERMS
        self.calls: list[str] = []

    def fetch(self, url, *, headers, timeout, max_bytes, max_redirects):
        self.calls.append(url)
        if url == ORIGIN + "/robots.txt":
            content = b"User-agent: *\nAllow: /\n"
        elif url == ORIGIN + "/about":
            content = OFFICIALITY
        elif url == ORIGIN + "/terms":
            content = self.terms
        elif url == self.url:
            content = self.raw
        else:
            raise AssertionError("unrecognized fixture URL")
        return HttpResponse(200, {"content-type": "text/html"}, content, url)


def count(client, table: str, column: str, value: str) -> int:
    return len(client.table(table).select("id").eq(column, value).execute().data or [])


def probe(service, anon) -> None:
    service.table("agent_sources").select("id").limit(1).execute()
    service.rpc("agent_list_due_sources", {"p_limit": 1}).execute()
    bucket = service.storage.get_bucket(BUCKET)
    if getattr(bucket, "public", None) is not False:
        raise AssertionError("Preview raw bucket is not private")
    denied = False
    try:
        anon.table("agent_sources").select("id").limit(1).execute()
    except Exception:
        denied = True
    if not denied:
        raise AssertionError("anon can read Agent Sources")
    denied = False
    try:
        anon.rpc("agent_review_source", {
            "p_source_id": str(uuid4()), "p_expected_status": "CANDIDATE",
            "p_target_status": "REVIEW", "p_reviewer": "unauthorized",
            "p_reason": "must fail", "p_decision": "REVIEW_STARTED",
            "p_decision_id": str(uuid4()), "p_source_type": None,
            "p_officiality_confirmed": None, "p_collection_allowed": None,
        }).execute()
    except Exception:
        denied = True
    if not denied:
        raise AssertionError("anon can execute Source approval RPC")
    print("PASS Preview PostgREST service reads and anon Source/RPC denial")


def e2e(service, anon) -> None:
    marker = uuid4().hex
    url = ORIGIN + "/calls/phase11d-" + marker
    raw = RAW + b"<!--phase11d-" + marker.encode("ascii") + b"-->"
    FIXTURE_URLS.append(url)
    now = datetime.now(timezone.utc)
    scope = ApprovalScope(
        scope_id="phase11d-" + marker[:12], origin=ORIGIN, path_prefix="/calls/",
        reviewed_by="PHASE11D_SYNTHETIC_FIXTURE", reviewed_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=1), officiality_evidence_url=ORIGIN + "/about",
        officiality_sha256=hashlib.sha256(OFFICIALITY).hexdigest(),
        terms_url=ORIGIN + "/terms", terms_sha256=hashlib.sha256(TERMS).hexdigest(),
        permission_basis="explicit_license",
    )
    transport = FixtureTransport(url, raw)
    assessor = AutoApprovalAssessor((scope,), user_agent="AIPATH-AgentCollect/0.1",
                                    transport=transport)
    sources = SupabaseSourceRepository(service)
    registry = SourceRegistryService(sources)
    discovery = SupabaseDiscoveryRepository(service)
    review_repo = SupabaseSourceReviewRepository(service)
    review = SourceApprovalService(review_repo)
    def schedule_confirmed(source):
        return bool(service.table("agent_source_schedules").select("source_id")
                    .eq("source_id", str(source.id)).limit(1).execute().data)
    runner = DiscoveryRunner(
        provider=FakeSearchProvider([SearchHit("AI Conference", url, "AI conference call")]),
        repository=discovery, registry=registry, evaluator=RuleBasedEvaluator(),
        gate=PromotionGate(registry, threshold=0.9), auto_assessor=assessor,
        auto_executor=AutoApprovalExecutor(review, schedule_verifier=schedule_confirmed),
    )
    outcome = runner.run(DiscoveryQuery(uuid4(), "AI conference synthetic fixture", "fake", now))
    if (outcome.query.result_count, outcome.query.candidate_count,
            outcome.auto_approved_count, outcome.collection_scheduled_count) != (1, 1, 1, 1):
        raise AssertionError("Preview Discovery to approval counters differ")
    source = sources.find_by_canonical_url(url)
    if source is None or source.status is not SourceStatus.ACTIVE or not source_is_collectible(source):
        raise AssertionError("Preview Source did not activate")
    if source.provenance.get("discovery_query_id") != str(outcome.query.id):
        raise AssertionError("Source lost its Discovery execution ID")
    events = review_repo.list_events(source.id)
    if len(events) != 3 or any(event.command.reviewer != AUTO_REVIEWER for event in events):
        raise AssertionError("machine approval audit incomplete")
    activation = events[-1].command
    service.rpc("agent_review_source", {
        "p_source_id": str(source.id), "p_expected_status": activation.expected_status.value,
        "p_target_status": activation.target_status.value, "p_reviewer": activation.reviewer,
        "p_reason": activation.reason, "p_decision": activation.decision.value,
        "p_decision_id": str(activation.decision_id), "p_source_type": None,
        "p_officiality_confirmed": None, "p_collection_allowed": None,
    }).execute()
    if len(review_repo.list_events(source.id)) != 3:
        raise AssertionError("replayed activation duplicated the audit")
    if not schedule_confirmed(source):
        raise AssertionError("Collection Schedule absent")
    again = runner.run(DiscoveryQuery(uuid4(), "AI conference duplicate fixture", "fake",
                                      datetime.now(timezone.utc)))
    if again.query.promoted_count != 0 or count(service, "agent_sources", "canonical_url", url) != 1:
        raise AssertionError("duplicate Source was registered")
    failure_url = ORIGIN + "/calls/phase11d-failure-" + marker
    FIXTURE_URLS.append(failure_url)
    failure_source = registry.register(
        name="PHASE11D rejected approval fixture", url=failure_url,
        source_type=SourceType.OFFICIAL,
        collection_method=CollectionMethod.HTTP_HTML, confidence=0.95,
    ).source
    review.begin_review(failure_source.id, reviewer=AUTO_REVIEWER,
                        reason="synthetic Preview RPC failure probe")
    denied = False
    try:
        service.rpc("agent_review_source", {
            "p_source_id": str(failure_source.id), "p_expected_status": "REVIEW",
            "p_target_status": "APPROVED", "p_reviewer": AUTO_REVIEWER,
            "p_reason": "synthetic permission denied", "p_decision": "APPROVED",
            "p_decision_id": str(uuid4()), "p_source_type": None,
            "p_officiality_confirmed": True, "p_collection_allowed": False,
        }).execute()
    except Exception:
        denied = True
    if (not denied or sources.get(failure_source.id).status is not SourceStatus.REVIEW
            or len(review_repo.list_events(failure_source.id)) != 1):
        raise AssertionError("failed Source approval RPC changed state or audit")
    review.hold(failure_source.id, reviewer=AUTO_REVIEWER,
                reason="PHASE11D failed approval fixture complete")
    print("PASS Discovery, machine audit, schedule, duplicate/replay, RPC failure recovery")

    storage = SupabaseRawContentStorage(service, BUCKET)
    digest = hashlib.sha256(raw).hexdigest()
    ref = f"supabase:{BUCKET}:{storage.key_for(digest)}"
    if storage.exists(ref):
        raise AssertionError("unique synthetic raw object already exists before Collection")
    runs = SupabaseCollectionRunRepository(service)
    documents = SupabaseRawDocumentRepository(service)
    collector = CollectionRunner(
        sources=sources, runs=runs, documents=documents, storage=storage,
        planner=CollectionPlanner(transport=transport),
        settings=load_settings(environ={}), environ={},
        auto_permission_checker=assessor.revalidate_source,
    )
    schedules = SupabaseCollectionScheduleRepository(service, sources)
    policy = CollectionSchedulePolicy()
    worker = CollectionWorker(schedules=schedules, runner=collector, policy=policy,
                              worker_id="PHASE11D_PREVIEW")
    summary = worker.run_once()
    if summary.success_count != 1 or count(service, "agent_raw_documents", "source_id", str(source.id)) != 1:
        raise AssertionError("scheduled collection did not store one RawDocument")
    row = service.table("agent_raw_documents").select("*").eq("source_id", str(source.id)).single().execute().data
    run = (service.table("agent_collection_runs").select("id,source_id,status,stored_count")
           .eq("id", row["run_id"]).single().execute().data)
    completion = (service.table("agent_collection_completions")
                  .select("source_id,run_id,outcome").eq("run_id", row["run_id"])
                  .single().execute().data)
    if (run["source_id"] != str(source.id) or run["status"] != "SUCCEEDED"
            or run["stored_count"] != 1 or completion["source_id"] != str(source.id)
            or completion["outcome"] != "SUCCEEDED"):
        raise AssertionError("Collection Run, completion ledger, and RawDocument disagree")
    if row["content_sha256"] != digest or row["content_ref"] != ref or storage.get(ref) != raw:
        raise AssertionError("stored raw SHA-256 or bytes mismatch")
    if storage.put(raw).ref != ref:
        raise AssertionError("duplicate storage put changed content reference")
    denied = False
    try:
        anon.storage.from_(BUCKET).download(row["content_ref"].split(BUCKET + ":", 1)[1])
    except Exception:
        denied = True
    if not denied:
        raise AssertionError("anon downloaded private RawDocument")
    replay = worker.run_once()
    if (replay.claimed_count != 0 or
            count(service, "agent_collection_runs", "source_id", str(source.id)) != 1 or
            count(service, "agent_raw_documents", "source_id", str(source.id)) != 1):
        raise AssertionError("repeat schedule run collected the same Source again")
    print("PASS scheduled Collection, private Storage, SHA-256, duplicate object, anon denial")

    class FailedStorage:
        def put(self, *args, **kwargs):
            raise OSError("synthetic Preview storage fault")

    failing_collector = CollectionRunner(
        sources=sources, runs=runs, documents=documents, storage=FailedStorage(),
        planner=CollectionPlanner(transport=transport),
        settings=load_settings(environ={}), environ={},
        auto_permission_checker=assessor.revalidate_source,
    )
    failed_storage = failing_collector.run_source(source.id)
    if (failed_storage.succeeded or
            count(service, "agent_raw_documents", "source_id", str(source.id)) != 1):
        raise AssertionError("Storage failure created another RawDocument")
    print("PASS synthetic Storage failure leaves failed Run without new RawDocument")

    transport.terms = b"Permission withdrawn in synthetic fixture"
    blocked = collector.run_source(source.id)
    if blocked.succeeded or count(service, "agent_raw_documents", "source_id", str(source.id)) != 1:
        raise AssertionError("withdrawn permission did not block collection")
    review.hold(source.id, reviewer=AUTO_REVIEWER, reason="PHASE11D fixture complete")
    if sources.get(source.id).status is not SourceStatus.HOLD:
        raise AssertionError("Preview fixture Source was not held")
    print("PASS permission withdrawal blocks new raw content; fixture Source held")
    print("PREVIEW_FIXTURE source_id=" + str(source.id) + " query_id=" + str(outcome.query.id)
          + " run_id=" + row["run_id"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service-key-file", type=Path, required=True)
    parser.add_argument("--anon-key-file", type=Path, required=True)
    parser.add_argument("--probe-only", action="store_true")
    args = parser.parse_args()
    service = create_client(PREVIEW_URL, read_key(args.service_key_file))
    anon = create_client(PREVIEW_URL, read_key(args.anon_key_file))
    probe(service, anon)
    if not args.probe_only:
        try:
            e2e(service, anon)
        finally:
            sources = SupabaseSourceRepository(service)
            review = SourceApprovalService(SupabaseSourceReviewRepository(service))
            for url in FIXTURE_URLS:
                source = sources.find_by_canonical_url(url)
                if source and source.status in {
                        SourceStatus.REVIEW, SourceStatus.APPROVED, SourceStatus.ACTIVE}:
                    review.hold(source.id, reviewer=AUTO_REVIEWER,
                                reason="PHASE11D fixture cleanup after test")


if __name__ == "__main__":
    main()
