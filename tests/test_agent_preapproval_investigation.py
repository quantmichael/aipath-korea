"""Pre-approval research uses reviewed licenses, never Source approval writes."""
import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from agent_collect.config.settings import load_settings
from agent_collect.discovery.auto_approval import ApprovalScope
from agent_collect.discovery.followup import OfficialFollowupExplorer
from agent_collect.discovery.models import DiscoveryQuery, QueryStatus
from agent_collect.runtime import AgentRuntime
from tests.test_agent_collect_discovery_followup import ORIGIN, FakeTransport, page, result
from tests.test_agent_metadata_runtime_wiring import Client, Discovery, Lease


def fixture():
    now = datetime.now(timezone.utc)
    terms, official = '<p>Explicit research license</p>', '<p>Official organizer</p>'
    scope = ApprovalScope('research', ORIGIN, '/notices/', 'reviewer', now-timedelta(days=1),
                          now+timedelta(days=1), ORIGIN+'/about',
                          hashlib.sha256(official.encode()).hexdigest(), ORIGIN+'/terms',
                          hashlib.sha256(terms.encode()).hexdigest(), 'explicit_license')
    root = ORIGIN+'/notices/'
    transport = FakeTransport({ORIGIN+'/terms': page(ORIGIN+'/terms', terms),
                               ORIGIN+'/about': page(ORIGIN+'/about', official),
                               root: page(root, '<a href="/notices/ai">AI 참가 신청</a>'),
                               root+'ai': page(root+'ai', '<h1>AI 참가 신청</h1>')})
    return now, scope, result(root), transport


def test_research_is_bounded_and_does_not_verify_or_approve():
    now, scope, hit, transport = fixture()
    outcome = OfficialFollowupExplorer(transport=transport).investigate(hit, scope=scope, now=now)
    assert len(outcome.leads) == 1
    assert outcome.audit_record()['followup_verified'] == 0
    assert outcome.leads[0].review_reason == 'requires_source_approval_and_official_evidence_pipeline'


def test_expired_scope_and_mismatched_identity_do_not_fetch():
    now, scope, hit, transport = fixture()
    explorer = OfficialFollowupExplorer(transport=transport)
    assert explorer.investigate(hit, scope=scope, now=scope.expires_at).blocked
    assert explorer.investigate(replace(hit, canonical_url=ORIGIN+'/other'), scope=scope, now=now).blocked
    assert transport.calls == []


def test_changed_terms_and_robots_stop_before_notice():
    now, scope, hit, transport = fixture()
    transport.pages[scope.terms_url] = page(scope.terms_url, 'changed')
    assert OfficialFollowupExplorer(transport=transport).investigate(hit, scope=scope, now=now).blocked
    assert hit.original_url not in transport.calls
    transport.calls.clear()
    transport.robots = b'User-agent: *\nDisallow: /\n'
    assert OfficialFollowupExplorer(transport=transport).investigate(hit, scope=scope, now=now).blocked
    assert transport.calls == [ORIGIN+'/robots.txt']


def test_real_automation_calls_research_with_zero_source_approvals(monkeypatch):
    from agent_collect import runtime as module
    now, scope, hit, transport = fixture()
    client = Client(None, (hit,))
    class Repository:
        def __init__(self, client): pass
        def get_query(self, identity):
            return DiscoveryQuery(hit.query_id, 'fixture', 'tavily', now, status=QueryStatus.SUCCEEDED)
        def get_result(self, identity): return hit
    monkeypatch.setattr(module, 'SupabaseDiscoveryRepository', Repository)
    monkeypatch.setattr(module, 'OfficialFollowupExplorer', lambda **kw: OfficialFollowupExplorer(transport=transport))
    monkeypatch.setattr(module, 'SupabaseAutomationLease', Lease)
    monkeypatch.setattr(AgentRuntime, 'discovery_worker', lambda self, worker_id: Discovery(hit.query_id))
    settings = replace(load_settings(environ={}), automation_enabled=True,
                       discovery_enabled=True, discovery_worker_enabled=True)
    agent = AgentRuntime(SimpleNamespace(client=client), None, None, settings, (scope,))
    runtime = agent.automation_runtime(Path('/unused'), worker_id='fixture')
    outcome = runtime.run_once()
    assert outcome.status == 'COMPLETED'
    assert outcome.results['metadata']['investigation_count'] == 1
    assert outcome.results['metadata']['investigation_lead_count'] == 1
    assert outcome.results['metadata']['processed_count'] == 0
    transport.calls.clear()
    assert 'metadata' not in runtime.run_once(dry_run=True).results
    assert transport.calls == []
    agent.investigation_scopes = ()
    assert runtime.run_once().results['metadata']['processed_count'] == 0
    assert transport.calls == []


def test_attachment_and_query_redirect_are_blocked_before_request():
    from agent_collect.collectors.transport import HttpResponse
    now, scope, hit, transport = fixture()
    root = hit.original_url
    transport.pages[root] = page(root, '<a href="/notices/application.pdf">AI 신청 공고</a>')
    outcome = OfficialFollowupExplorer(transport=transport).investigate(hit, scope=scope, now=now)
    assert not outcome.leads
    assert root + 'application.pdf' not in transport.calls
    transport.calls.clear()
    transport.pages[root] = HttpResponse(302, {'location': root+'?download=1'}, b'', root)
    outcome = OfficialFollowupExplorer(transport=transport).investigate(hit, scope=scope, now=now)
    assert outcome.blocked
    assert root+'?download=1' not in transport.calls


def test_policy_attachment_is_also_blocked_before_request():
    now, scope, hit, transport = fixture()
    scope = replace(scope, terms_url=ORIGIN+'/terms.pdf')
    outcome = OfficialFollowupExplorer(transport=transport).investigate(hit, scope=scope, now=now)
    assert outcome.blocked
    assert scope.terms_url not in transport.calls
