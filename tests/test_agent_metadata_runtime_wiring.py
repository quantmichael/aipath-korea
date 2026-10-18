"""The real Automation entry point hands off only authenticated, approved leads."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from agent_collect.collectors.transport import HttpResponse
from agent_collect.config.settings import load_settings
from agent_collect.discovery.models import DiscoveryQuery, QueryStatus, SearchResult, SearchResultStatus
from agent_collect.discovery.followup import FollowupLead
from agent_collect.discovery.scheduling import DiscoveryWorkerSummary
from agent_collect.metadata.evidence import DuplicateSnapshot
from agent_collect.metadata.supabase import SupabaseMetadataEvidence
from agent_collect.runtime import AgentRuntime
from agent_collect.schemas.status import SourceStatus
from tests.test_agent_collect_metadata_evidence import PERMISSION, URL, fixture
from tests.test_agent_metadata_supabase import LABELLED_PAGE, row_from_policy


class Query:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *args, **kwargs):
        return self

    def eq(self, key, value):
        self.rows = [row for row in self.rows if row.get(key) == value]
        return self

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows, count=len(self.rows))


class Client:
    def __init__(self, approval, results):
        self.approval, self.results = approval, results

    def table(self, name):
        if name == "agent_metadata_source_approvals":
            return Query([self.approval] if self.approval else [])
        if name == "agent_search_results":
            return Query([{"id": str(row.id), "query_id": str(row.query_id)}
                          for row in self.results])
        raise AssertionError(f"unexpected table: {name}")


class Lease:
    def __init__(self, client):
        self.logs = []

    def acquire(self, **kwargs):
        return True

    def release(self, claim_id):
        return True

    def record_summary(self, details):
        self.logs.append(details)


class Discovery:
    def __init__(self, query_id):
        self.query_id = query_id

    def run_once(self, *, dry_run=False):
        now = datetime.now(timezone.utc)
        return DiscoveryWorkerSummary(
            "synthetic", now, now, dry_run, 1, 0 if dry_run else 1,
            0 if dry_run else 1, 0, 0, 0, (),
            query_ids=() if dry_run else (str(self.query_id),))


def test_approved_lead_reaches_metadata_from_agent_automation_runtime(monkeypatch):
    from agent_collect import runtime as runtime_module

    now = datetime.now(timezone.utc)
    source, policy, candidate, _, _ = fixture()
    source = replace(source, status=SourceStatus.APPROVED)
    policy = replace(policy, reviewed_at=now - timedelta(days=1),
                     expires_at=now + timedelta(days=1),
                     approved_principal_id=uuid4(), approval_event_id=uuid4())
    approval = {**row_from_policy(policy), "cohort": "operational"}
    query_id = uuid4()
    approved = SearchResult(uuid4(), query_id, "tavily", 1, "Approved official notice",
                            URL, "", now, canonical_url=URL,
                            status=SearchResultStatus.FILTERED,
                            filter_reasons=("existing_source",))
    unapproved_url = "https://fixture.example.org/notices/unapproved"
    unapproved = SearchResult(uuid4(), query_id, "tavily", 2, "Unapproved notice",
                              unapproved_url, "", now, canonical_url=unapproved_url,
                              status=SearchResultStatus.CANDIDATE)
    client = Client(approval, (approved, unapproved))
    metadata = SupabaseMetadataEvidence(client)
    metadata.sources.get = lambda source_id: source if source_id == source.id else None
    metadata._policy = lambda row, cohort: policy
    metadata._duplicates = lambda now: DuplicateSnapshot((), (), now, True, True)
    observed, writes = [], []
    real_assess = metadata.resolver.assess

    def assess(*args, **kwargs):
        outcome = real_assess(*args, **kwargs)
        observed.append(outcome)
        return outcome

    metadata.resolver.assess = assess
    metadata.create_candidate = lambda **kwargs: writes.append(kwargs) or candidate.id
    metadata.assess_and_record = lambda *args, **kwargs: observed[-1]

    class Repository:
        def __init__(self, client):
            pass

        def get_query(self, identity):
            return DiscoveryQuery(query_id, "synthetic search", "tavily", now,
                                  status=QueryStatus.SUCCEEDED)

        def get_result(self, identity):
            return {approved.id: approved, unapproved.id: unapproved}[identity]

    fetches = []

    class Explorer:
        def __init__(self, **kwargs):
            pass

        def _fetch(self, url, reviewed):
            fetches.append(url)
            return HttpResponse(200, {"content-type": "text/html"},
                                PERMISSION if url == policy.permission_url else LABELLED_PAGE, url)

        def explore(self, *args, **kwargs):
            raise AssertionError("an individual result needs no listing crawl")

    monkeypatch.setattr(runtime_module, "SupabaseDiscoveryRepository", Repository)
    monkeypatch.setattr(runtime_module, "OfficialFollowupExplorer", Explorer)
    monkeypatch.setattr(runtime_module, "SupabaseAutomationLease", Lease)
    monkeypatch.setattr(AgentRuntime, "metadata_evidence", lambda self: metadata)
    monkeypatch.setattr(AgentRuntime, "discovery_worker",
                        lambda self, worker_id: Discovery(query_id))
    settings = replace(load_settings(environ={}), automation_enabled=True,
                       discovery_enabled=True, discovery_worker_enabled=True)
    agent = AgentRuntime(SimpleNamespace(client=client), None, None, settings)
    runtime = agent.automation_runtime(Path("/unused"), worker_id="synthetic")
    summary = runtime.run_once()

    assert summary.status == "COMPLETED"
    assert summary.results["metadata"]["lead_count"] == 1
    assert summary.results["metadata"]["processed_count"] == 1
    assert len(observed) == len(writes) == 1
    assert observed[0].verification.status == "VERIFIED"
    assert observed[0].duplicate.status.value == "UNIQUE"
    assert observed[0].gate.status.value == "PUBLISH_READY"
    assert fetches == [policy.permission_url, URL]
    assert unapproved_url not in fetches
    assert writes[0]["facts"]["official_url"] == URL

    fetches.clear()
    dry = runtime.run_once(dry_run=True)
    assert "metadata" not in dry.results
    assert fetches == []

    source = replace(source, status=SourceStatus.REVIEW)
    held = runtime.run_once()
    assert held.status == "COMPLETED"
    assert held.results["metadata"]["processed_count"] == 0
    assert fetches == []


def test_zero_approvals_skips_metadata_without_interrupting_discovery(monkeypatch):
    from agent_collect import runtime as runtime_module

    query_id = uuid4()
    result = SearchResult(uuid4(), query_id, "tavily", 1, "Unapproved notice",
                          URL, "", datetime.now(timezone.utc), canonical_url=URL,
                          status=SearchResultStatus.CANDIDATE)
    client = Client(None, (result,))
    monkeypatch.setattr(runtime_module, "SupabaseAutomationLease", Lease)
    monkeypatch.setattr(AgentRuntime, "discovery_worker",
                        lambda self, worker_id: Discovery(query_id))
    settings = replace(load_settings(environ={}), automation_enabled=True,
                       discovery_enabled=True, discovery_worker_enabled=True)
    agent = AgentRuntime(SimpleNamespace(client=client), None, None, settings)
    summary = agent.automation_runtime(Path("/unused"), worker_id="synthetic").run_once()
    assert summary.status == "COMPLETED"
    assert summary.results["metadata"] == {
        "lead_count": 0, "processed_count": 0,
        "review_required_count": 0, "publish_ready_count": 0,
    }


def test_metadata_failure_does_not_stop_existing_collection_worker(monkeypatch):
    from agent_collect import runtime as runtime_module

    query_id = uuid4()
    client = Client(None, ())
    calls = []

    class Collection:
        def run_once(self, *, dry_run=False):
            calls.append("collection")
            return SimpleNamespace(success_count=1)

    def unavailable(self, query_ids):
        raise RuntimeError("metadata audit unavailable")

    monkeypatch.setattr(runtime_module, "SupabaseAutomationLease", Lease)
    monkeypatch.setattr(AgentRuntime, "discovery_worker",
                        lambda self, worker_id: Discovery(query_id))
    monkeypatch.setattr(AgentRuntime, "collection_worker",
                        lambda self, raw_dir, worker_id: Collection())
    monkeypatch.setattr(AgentRuntime, "process_discovery_metadata", unavailable)
    settings = replace(load_settings(environ={}), automation_enabled=True,
                       discovery_enabled=True, discovery_worker_enabled=True,
                       collection_worker_enabled=True)
    agent = AgentRuntime(SimpleNamespace(client=client), None, None, settings)
    summary = agent.automation_runtime(Path("/unused"), worker_id="synthetic").run_once()
    assert summary.status == "PARTIAL"
    assert summary.errors == {"metadata": "RuntimeError"}
    assert calls == ["collection"]


def test_reviewed_listing_followup_reaches_only_approved_individual(monkeypatch):
    from agent_collect import runtime as runtime_module

    now = datetime.now(timezone.utc)
    source, policy, _, _, _ = fixture()
    source = replace(source, status=SourceStatus.APPROVED)
    policy = replace(policy, reviewed_at=now - timedelta(days=1),
                     expires_at=now + timedelta(days=1),
                     approved_principal_id=uuid4(), approval_event_id=uuid4())
    approval = {**row_from_policy(policy), "cohort": "operational"}
    query_id = uuid4()
    listing = "https://fixture.example.org/notices"
    result = SearchResult(uuid4(), query_id, "tavily", 1, "Official listing",
                          listing, "", now, canonical_url=listing,
                          status=SearchResultStatus.FILTERED,
                          provider_metadata={"quality_followup_plan": {
                              "kind": "INDIVIDUAL_NOTICE_SEARCH",
                              "source_search_result_id": "pending"}})
    result = replace(result, provider_metadata={"quality_followup_plan": {
        "kind": "INDIVIDUAL_NOTICE_SEARCH",
        "source_search_result_id": str(result.id)}})
    client = Client(approval, (result,))
    calls = []
    metadata = SimpleNamespace(
        sources=SimpleNamespace(get=lambda identity: source),
        _policy=lambda row, cohort: policy,
        process_followup_lead=lambda **kwargs: (
            calls.append(("metadata", kwargs["lead"].url)) or
            SimpleNamespace(stored_candidate_id=uuid4(),
                            outcome=SimpleNamespace(gate=SimpleNamespace(
                                status=SimpleNamespace(value="PUBLISH_READY"))))),
    )

    class Repository:
        def __init__(self, client):
            pass

        def get_query(self, identity):
            return DiscoveryQuery(query_id, "synthetic listing search", "tavily", now,
                                  status=QueryStatus.SUCCEEDED)

        def get_result(self, identity):
            return result

    class Explorer:
        def __init__(self, **kwargs):
            pass

        def explore(self, item, *, reviewed, now):
            calls.append(("followup", item.original_url))
            return SimpleNamespace(leads=(FollowupLead(
                URL, listing, 1, "AI registration", "INDIVIDUAL_NOTICE",
                None, None, "UNKNOWN", "UNKNOWN", None, "approved_notice"),))

        def _fetch(self, url, reviewed):
            calls.append(("fetch", url))
            return HttpResponse(200, {"content-type": "text/html"},
                                PERMISSION if url == policy.permission_url else LABELLED_PAGE, url)

    monkeypatch.setattr(runtime_module, "SupabaseDiscoveryRepository", Repository)
    monkeypatch.setattr(runtime_module, "OfficialFollowupExplorer", Explorer)
    monkeypatch.setattr(runtime_module, "source_is_collectible", lambda source: True)
    monkeypatch.setattr(AgentRuntime, "metadata_evidence", lambda self: metadata)
    settings = load_settings(environ={})
    agent = AgentRuntime(SimpleNamespace(
        client=client, find_by_canonical_url=lambda url: SimpleNamespace()),
        None, None, settings)
    outcome = agent.process_discovery_metadata((str(query_id),))
    assert outcome["publish_ready_count"] == 1
    assert calls == [("followup", listing), ("fetch", policy.permission_url),
                     ("fetch", URL), ("metadata", URL)]
