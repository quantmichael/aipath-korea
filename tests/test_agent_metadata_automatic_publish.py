"""Offline real Runtime -> worker -> Evidence -> adapter integration.

DB/HTTP transports are synthetic. Atomic SQL itself is covered by the existing
isolated PostgreSQL tests; these tests do not claim a real Service write.
"""
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from agent_collect.adapters.service_db.adapter import SupabaseServiceDBAdapter
from agent_collect.collectors.transport import HttpResponse
from agent_collect.config.settings import load_settings
from agent_collect.metadata.supabase import SupabaseMetadataEvidence
from agent_collect.runtime import AgentRuntime
from agent_collect.schemas.status import SourceStatus
from tests.test_agent_collect_metadata_evidence import NOW, URL, PERMISSION, fixture
from tests.test_agent_metadata_service_contract import active_source
from tests.test_agent_metadata_supabase import LABELLED_PAGE, row_from_policy
from tests.test_agent_metadata_runtime_wiring import Discovery, Lease


class Query:
    def __init__(self, client, name):
        self.client, self.name = client, name
        self.filters, self.cap = [], None
    def select(self, *args, **kwargs): return self
    def eq(self, key, value):
        self.filters.append(lambda row: row.get(key) == value)
        return self
    def in_(self, key, values):
        self.filters.append(lambda row: row.get(key) in values)
        return self
    def order(self, *args, **kwargs): return self
    def limit(self, n):
        self.cap = n
        return self
    def execute(self):
        rows = [r for r in self.client.rows[self.name]
                if all(predicate(r) for predicate in self.filters)]
        count = len(rows)
        if self.name in self.client.bad_counts:
            count = self.client.bad_counts[self.name]
        return SimpleNamespace(data=rows[:self.cap], count=count)


class Client:
    def __init__(self, rows):
        self.rows, self.bad_counts, self.assessments = rows, {}, []
    def table(self, name): return Query(self, name)
    def rpc(self, name, args):
        if name == 'agent_metadata_create_candidate':
            def create():
                approval = self.rows['agent_metadata_source_approvals'][0]
                identity = str(uuid4())
                self.rows['agent_metadata_candidates'].append({
                    'id':identity, 'source_id':approval['source_id'],
                    'approval_id':approval['id'],'official_url':args['p_facts']['official_url'],
                    'cohort':'operational','status':'REVIEW_REQUIRED',
                    'facts':args['p_facts'],'field_evidence':args['p_field_evidence'],
                    'discovered_at':args['p_discovered_at']})
                return SimpleNamespace(data=identity)
            return SimpleNamespace(execute=create)
        assert name == 'agent_metadata_record_assessment'
        def execute():
            row = next(r for r in self.rows['agent_metadata_candidates']
                       if r['id'] == args['p_candidate_id'])
            self.assessments.append(args)
            row['status'] = args['p_status']
            return SimpleNamespace(data=row['id'])
        return SimpleNamespace(execute=execute)


class Tables:
    def __init__(self, client, source):
        self.client = client
        self.sources = [{'id':str(uuid4()),'name':source.name,
                         'homepage_url':source.url,'is_active':True}]
        self.categories = [{'id':str(uuid4()),'slug':'conference','is_active':True}]
        self.records, self.calls = {}, []
        self.failure = None
    def list_sources(self): return self.sources
    def list_categories(self): return self.categories
    def get_metadata_completion(self, candidate_id):
        return next((r for r in self.client.rows['agent_metadata_publish_completions']
                     if r['candidate_id'] == str(candidate_id)), None)
    def get_opportunity(self, identity): return self.records.get(identity)
    def publish_metadata_atomic(self, candidate_id, record, evidence):
        self.calls.append((candidate_id, record, evidence))
        if self.failure == 'before_commit': raise TimeoutError('private transport detail')
        identity = uuid4()
        self.records[identity] = {'id':str(identity), **record}
        self.client.rows['agent_metadata_publish_completions'].append({
            'candidate_id':str(candidate_id),'opportunity_id':str(identity),'disposition':'PUBLISHED'})
        if self.failure == 'after_commit': raise TimeoutError('private transport detail')
        return identity, 'registered'


class Jobs:
    def __init__(self): self.summaries = []
    def preview(self, n): return []
    def list_due(self, n): return []
    def record_summary(self, summary): self.summaries.append(summary)


def setup_runtime(monkeypatch, fault=None, page_override=None):
    from agent_collect import runtime as runtime_module
    from agent_collect.publishing import worker as worker_module
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return NOW
    monkeypatch.setattr(runtime_module, 'datetime', Clock)
    monkeypatch.setattr(worker_module, 'datetime', Clock)
    source, policy, candidate, page, duplicates = fixture()
    if page_override is not None: page = page_override
    source = active_source(source)
    policy = replace(policy, approved_principal_id=uuid4(), approval_event_id=uuid4())
    if fault == 'source': source = replace(source, status=SourceStatus.REVIEW, is_active=False)
    if fault == 'synthetic_only':
        source = replace(source, provenance={**source.provenance, 'synthetic_only':True})
    if fault == 'revoked': policy = replace(policy, revoked_at=NOW)
    if fault == 'expired': policy = replace(policy, expires_at=NOW)
    fields = {name:{'source_url':fact.source_url,'evidence_type':fact.evidence_type.value,
                   'evidence_reference':fact.evidence_reference,'quote':fact.quote,
                   'observed_at':fact.observed_at.isoformat(),'confidence':fact.confidence,
                   'access_policy_digest':fact.access_policy_digest}
              for name,fact in candidate.facts.items()}
    approval = {**row_from_policy(policy), 'id':str(uuid4()), 'cohort':'operational'}
    row = {'id':str(candidate.id),'source_id':str(source.id),'approval_id':approval['id'],
           'official_url':URL,'cohort':'operational','status':'PUBLISH_READY',
           'facts':{n:f.value for n,f in candidate.facts.items()},'field_evidence':fields,
           'discovered_at':NOW.isoformat()}
    client = Client({'agent_metadata_candidates':[row],
                     'agent_metadata_source_approvals':[approval],
                     'agent_metadata_publish_completions':[]})
    metadata = SupabaseMetadataEvidence(client)
    metadata.sources.get = lambda identity: source if identity == source.id else None
    metadata._policy = lambda row, cohort: policy
    metadata._duplicates = lambda now: duplicates
    tables, jobs = Tables(client, source), Jobs()
    adapter = SupabaseServiceDBAdapter(tables=tables,ledger=SimpleNamespace(),publish_enabled=True)
    fetches = []
    class Explorer:
        def __init__(self, **kwargs): pass
        def _fetch(self, url, reviewed, *, request_policy):
            assert request_policy(url)
            assert not request_policy(url + '/unexpected')
            fetches.append(url)
            is_permission = url == policy.permission_url
            data = PERMISSION if is_permission else page
            if fault == 'permission' and is_permission: data = b'changed permission'
            final = url + '/redirect' if fault == 'redirect' else url
            if fault == 'page' and not is_permission: data = b'<p>unrelated page</p>'
            return HttpResponse(200,{'content-type':'text/html'},data,final)
    monkeypatch.setattr(runtime_module, 'OfficialFollowupExplorer', Explorer)
    monkeypatch.setattr(runtime_module, 'SupabaseAutomationLease', Lease)
    monkeypatch.setattr(runtime_module, 'SupabasePublishJobs', lambda client: jobs)
    monkeypatch.setattr(runtime_module, 'SupabasePublishLedger', lambda client: SimpleNamespace())
    monkeypatch.setattr(AgentRuntime, 'metadata_evidence', lambda self: metadata)
    monkeypatch.setattr(AgentRuntime, 'service_db_adapter', lambda self: adapter)
    settings = replace(load_settings(environ={}), automation_enabled=True,
                       publish_enabled=True, publish_worker_enabled=True)
    agent = AgentRuntime(SimpleNamespace(client=client),None,None,settings)
    runtime = agent.automation_runtime(Path('/unused'),worker_id='synthetic-local')
    return SimpleNamespace(agent=agent,runtime=runtime,metadata=metadata,tables=tables,
                           client=client,jobs=jobs,fetches=fetches,candidate=candidate)


def test_real_runtime_rechecks_evidence_then_dispatches_atomic_publication_once(monkeypatch):
    ctx = setup_runtime(monkeypatch)
    summary = ctx.runtime.run_once()
    publish = summary.results['publish']
    assert (publish.metadata_due_count,publish.metadata_attempted_count,publish.published_count) == (1,1,1)
    assert ctx.client.assessments[0]['p_verification']['status'] == 'VERIFIED'
    assert ctx.client.assessments[0]['p_duplicate']['status'] == 'UNIQUE'
    assert ctx.client.assessments[0]['p_gate']['status'] == 'PUBLISH_READY'
    identity,record,evidence = ctx.tables.calls[0]
    assert identity == ctx.candidate.id
    assert record['status'] == 'scheduled'
    assert evidence['application_deadline']['precision'] == 'DATE_ONLY'
    assert ctx.runtime.lease.logs[0]['workers']['publish']['metadata_attempted_count'] == 1
    ctx.fetches.clear()
    second = ctx.runtime.run_once()
    assert second.results['publish'].metadata_attempted_count == 0
    assert len(ctx.tables.calls) == 1 and ctx.fetches == []


@pytest.mark.parametrize('fault', ['source','synthetic_only','revoked','expired','permission','redirect','page'])
def test_real_runtime_holds_without_atomic_call_on_invalid_source_or_evidence(monkeypatch,fault):
    ctx = setup_runtime(monkeypatch,fault)
    summary = ctx.runtime.run_once().results['publish']
    assert summary.review_count == 1 and summary.published_count == 0
    assert ctx.tables.calls == []
    assert ctx.client.rows['agent_metadata_candidates'][0]['status'] == 'REVIEW_REQUIRED'
    if fault in {'source','synthetic_only','revoked','expired'}: assert ctx.fetches == []


def test_dry_run_only_lists_candidate_no_http_assessment_or_publication(monkeypatch):
    ctx = setup_runtime(monkeypatch)
    summary = ctx.runtime.run_once(dry_run=True).results['publish']
    assert summary.metadata_due_count == 1 and summary.metadata_attempted_count == 0
    assert summary.previews[0]['eligible'] is False
    assert ctx.fetches == ctx.client.assessments == ctx.tables.calls == ctx.jobs.summaries == []


@pytest.mark.parametrize('flag', ['publish_enabled','publish_worker_enabled'])
def test_either_off_flag_prevents_automatic_metadata_processing(monkeypatch,flag):
    ctx = setup_runtime(monkeypatch)
    ctx.agent.settings = replace(ctx.agent.settings, **{flag:False})
    summary = ctx.agent.automation_runtime(Path('/unused'),worker_id='disabled').run_once()
    assert 'publish' not in summary.results
    assert ctx.fetches == ctx.client.assessments == ctx.tables.calls == []


def test_global_lease_denial_prevents_metadata_access(monkeypatch):
    ctx = setup_runtime(monkeypatch)
    ctx.runtime.lease.acquire = lambda **kwargs: False
    assert ctx.runtime.run_once().status == 'LOCKED'
    assert ctx.fetches == ctx.client.assessments == ctx.tables.calls == []


@pytest.mark.parametrize('failure,expected', [('before_commit',0),('after_commit',1)])
def test_rpc_timeout_is_not_retried_within_runtime(monkeypatch,failure,expected):
    ctx = setup_runtime(monkeypatch)
    ctx.tables.failure = failure
    summary = ctx.runtime.run_once()
    result = summary.results['publish']
    assert len(ctx.tables.calls) == 1
    assert len(ctx.tables.records) == expected
    assert result.published_count == 0
    assert result.failed_count == (1 if failure == 'before_commit' else 0)
    assert result.duplicate_count == expected
    assert 'private transport detail' not in repr(result)
    assert summary.status == ('PARTIAL' if failure == 'before_commit' else 'COMPLETED')
    assert summary.errors == ({'publish':'PUBLISH_INCOMPLETE'} if failure == 'before_commit' else {})


@pytest.mark.parametrize('table', ['agent_metadata_candidates','agent_metadata_publish_completions'])
@pytest.mark.parametrize('count',[None,2])
def test_incomplete_selection_stops_before_fetch(monkeypatch,table,count):
    ctx = setup_runtime(monkeypatch)
    ctx.client.bad_counts[table] = count
    result = ctx.runtime.run_once().results['publish']
    assert result.failed_count == 1
    assert ctx.fetches == ctx.client.assessments == ctx.tables.calls == []


def test_full_raw_due_reserves_the_shared_limit_even_if_another_worker_claims_it(monkeypatch):
    ctx = setup_runtime(monkeypatch)
    ctx.jobs.list_due = lambda n: [SimpleNamespace(id=uuid4(),candidate_id=uuid4())]
    ctx.jobs.claim = lambda *args, **kwargs: None
    result = ctx.runtime.run_once().results['publish']
    assert result.due_count == 1 and result.metadata_attempted_count == 0
    assert ctx.fetches == ctx.client.assessments == ctx.tables.calls == []


def test_unknown_stops_other_metadata_candidates_and_does_not_exceed_limit(monkeypatch):
    ctx = setup_runtime(monkeypatch)
    row = ctx.client.rows['agent_metadata_candidates'][0]
    ctx.client.rows['agent_metadata_candidates'].append({**row,'id':str(uuid4())})
    ctx.agent.settings = replace(ctx.agent.settings,publish_max_jobs_per_run=2)
    ctx.tables.failure = 'before_commit'
    runtime = ctx.agent.automation_runtime(Path('/unused'),worker_id='bounded')
    result = runtime.run_once().results['publish']
    assert result.metadata_due_count == 2
    assert result.metadata_attempted_count == 1 and len(ctx.tables.calls) == 1
    assert result.failed_count == 1


def test_manual_worker_does_not_implicitly_enable_metadata_scanning(monkeypatch):
    ctx = setup_runtime(monkeypatch)
    result = ctx.agent.publish_worker(worker_id='manual').run_once()
    assert result.metadata_due_count == 0
    assert ctx.fetches == ctx.client.assessments == ctx.tables.calls == []


def test_selector_excludes_synthetic_review_rows_and_completed_candidates(monkeypatch):
    ctx = setup_runtime(monkeypatch)
    rows = ctx.client.rows['agent_metadata_candidates']
    original = rows[0]
    rows.extend([{**original,'id':str(uuid4()),'cohort':'synthetic'},
                 {**original,'id':str(uuid4()),'status':'REVIEW_REQUIRED'},
                 {**original,'id':str(uuid4())}])
    ctx.client.rows['agent_metadata_publish_completions'].append({
        'candidate_id':original['id'],'disposition':'PUBLISHED'})
    assert ctx.metadata.pending_publication_ids(1) == (UUID(rows[-1]['id']),)


def test_selector_fails_closed_on_overflow(monkeypatch):
    ctx = setup_runtime(monkeypatch)
    ctx.metadata.max_records = 1
    row = ctx.client.rows['agent_metadata_candidates'][0]
    ctx.client.rows['agent_metadata_candidates'].append({**row,'id':str(uuid4())})
    with pytest.raises(RuntimeError, match='scan incomplete'):
        ctx.metadata.pending_publication_ids(1)
    assert ctx.fetches == ctx.client.assessments == ctx.tables.calls == []


@pytest.mark.parametrize('unresolved_clock', [False,True])
def test_discovery_extraction_to_automatic_publish_through_real_runtime(monkeypatch,unresolved_clock):
    from agent_collect import runtime as runtime_module
    from agent_collect.discovery.models import DiscoveryQuery, QueryStatus, SearchResult, SearchResultStatus
    page = LABELLED_PAGE
    if unresolved_clock:
        page = page.replace(b'Application deadline: 2027-04-13',
                            b'Application deadline: 2027-04-13 18:00')
    ctx = setup_runtime(monkeypatch, page_override=page)
    ctx.client.rows['agent_metadata_candidates'] = []
    query_id = uuid4()
    result = SearchResult(uuid4(),query_id,'synthetic',1,'Synthetic AI notice',URL,'',NOW,
                          canonical_url=URL,status=SearchResultStatus.FILTERED)
    ctx.client.rows['agent_search_results'] = [{'id':str(result.id),'query_id':str(query_id)}]
    class Repository:
        def __init__(self,client): pass
        def get_query(self,identity):
            assert identity == query_id
            return DiscoveryQuery(query_id,'synthetic','synthetic',NOW,status=QueryStatus.SUCCEEDED)
        def get_result(self,identity):
            assert identity == result.id
            return result
    # Discovery transport alone is stubbed; runtime handoff, extraction, SDK
    # assessment, publish worker, Service mapping and envelope are real code.
    monkeypatch.setattr(runtime_module,'SupabaseDiscoveryRepository',Repository)
    monkeypatch.setattr(AgentRuntime,'discovery_worker',lambda self,worker_id: Discovery(query_id))
    # Discovery's approved path already constrains the request and doesn't pass
    # the new publication-only exact redirect callback.
    explorer_class = runtime_module.OfficialFollowupExplorer
    original_fetch = explorer_class._fetch
    def fetch(self,url,reviewed,*,request_policy=None):
        return original_fetch(self,url,reviewed,request_policy=request_policy or (lambda target:target==url))
    monkeypatch.setattr(explorer_class,'_fetch',fetch)
    ctx.agent.settings = replace(ctx.agent.settings,discovery_enabled=True,discovery_worker_enabled=True)
    runtime = ctx.agent.automation_runtime(Path('/unused'),worker_id='end-to-end-offline')
    summary = runtime.run_once()
    assert summary.errors == {}
    if unresolved_clock:
        assert summary.results['metadata']['processed_count'] == 0
        assert ctx.client.rows['agent_metadata_candidates'] == []
        assert ctx.tables.calls == []
    else:
        assert summary.results['metadata']['publish_ready_count'] == 1
        assert summary.results['publish'].published_count == 1
        row = ctx.client.rows['agent_metadata_candidates'][0]
        assert ctx.tables.calls[0][0] == UUID(row['id'])
        assert row['facts']['official_url'] == URL
        assert row['facts']['summary'] == 'A synthetic opportunity for isolated tests.'
        assert ctx.tables.calls[0][2]['application_deadline']['precision'] == 'DATE_ONLY'
        assert all(event['p_verification']['status']=='VERIFIED' for event in ctx.client.assessments)


def test_duplicate_service_opportunity_blocks_automatic_publication(monkeypatch):
    from agent_collect.metadata.evidence import DuplicateSnapshot
    from agent_collect.quality_gate.models import DuplicateRecord
    ctx = setup_runtime(monkeypatch)
    existing = DuplicateRecord(str(uuid4()),'service','existing','Fixture Lab',URL,None,URL,None)
    ctx.metadata._duplicates = lambda now: DuplicateSnapshot((),(existing,),now,True,True)
    result = ctx.runtime.run_once().results['publish']
    assert result.review_count == 1
    assert ctx.tables.calls == []
    assert ctx.client.assessments[0]['p_duplicate']['status'] == 'DUPLICATE'


def test_revocation_between_fetch_and_assessment_blocks_atomic_registration(monkeypatch):
    ctx = setup_runtime(monkeypatch)
    original_inputs = ctx.agent.metadata_publication_inputs
    original_policy = ctx.metadata._policy
    def inputs(identity):
        result = original_inputs(identity)
        ctx.metadata._policy = lambda row,cohort: replace(original_policy(row,cohort),revoked_at=NOW)
        return result
    monkeypatch.setattr(AgentRuntime,'metadata_publication_inputs',lambda self,identity:inputs(identity))
    result = ctx.agent.automation_runtime(Path('/unused'),worker_id='revoked').run_once().results['publish']
    assert result.review_count == 1 and ctx.tables.calls == []


def test_metadata_scan_enforces_unique_complete_ledger_identity(monkeypatch):
    ctx = setup_runtime(monkeypatch)
    ctx.client.rows['agent_metadata_publish_completions'].append({
        'candidate_id':str(ctx.candidate.id),'disposition':'UNKNOWN'})
    with pytest.raises(RuntimeError,match='identity mismatch'):
        ctx.metadata.pending_publication_ids(1)


def test_summary_storage_contains_counts_and_no_transient_page_bytes(monkeypatch):
    from unittest.mock import MagicMock
    from agent_collect.publishing.supabase import SupabasePublishJobs
    ctx = setup_runtime(monkeypatch)
    result = ctx.runtime.run_once().results['publish']
    client = MagicMock()
    SupabasePublishJobs(client).record_summary(result)
    payload = client.table.return_value.insert.call_args.args[0]
    assert payload['details']['metadata_attempted_count'] == 1
    assert payload['details']['metadata'][0]['status'] == 'PUBLISHED'
    assert 'permission_document' not in repr(payload)
    assert 'synthetic opportunity' not in repr(payload)
