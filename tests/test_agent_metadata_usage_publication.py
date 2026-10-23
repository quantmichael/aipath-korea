"""Publication applies the existing 019 action-rights contract before RPC I/O."""
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest

from agent_collect.metadata.evidence import MetadataUsageRights
from agent_collect.metadata.supabase import SupabaseMetadataEvidence
from tests.test_agent_collect_metadata_evidence import NOW, PERMISSION, URL, USAGE_SNAPSHOT, fixture
from tests.test_agent_metadata_automatic_publish import setup_runtime
from tests.test_agent_metadata_supabase import EventClient, row_from_policy


def test_action_rights_load_only_from_exact_immutable_approval_snapshot():
    policy=fixture()[1]
    row=row_from_policy(policy)
    event={'id':str(uuid4()),'action':'APPROVED','actor_uid':str(uuid4()),
           'acted_at':NOW.isoformat(),'policy_snapshot':{**row,'usage_rights':USAGE_SNAPSHOT}}
    loaded=SupabaseMetadataEvidence(EventClient([event]))._policy(row,cohort='operational')
    assert loaded.usage_rights.metadata_permitted()
    for key in ('official_url','permission_url','permission_digest','scope_origin','path_prefix'):
        wrong=deepcopy(event); wrong['policy_snapshot'][key]='different'
        loaded=SupabaseMetadataEvidence(EventClient([wrong]))._policy(row,cohort='operational')
        assert loaded.usage_rights is None


@pytest.mark.parametrize('fault',['missing','extra','bad_status','empty_reference','wrong_type'])
def test_malformed_action_rights_are_not_inferred(fault):
    snapshot=deepcopy(USAGE_SNAPSHOT)
    if fault=='missing': del snapshot['fact_store']
    elif fault=='extra': snapshot['other']={'status':'ALLOWED'}
    elif fault=='bad_status': snapshot['fact_store']['status']='yes'
    elif fault=='empty_reference': snapshot['fact_store']['evidence_reference']=' '
    else: snapshot['fact_store']=True
    assert MetadataUsageRights.from_snapshot(snapshot) is None


@pytest.mark.parametrize('action',['public_read','automated_access','fact_extract','fact_store','fact_display'])
@pytest.mark.parametrize('status',['DENIED','UNVERIFIED'])
def test_runtime_holds_without_http_when_a_required_action_is_not_allowed(monkeypatch,action,status):
    ctx=setup_runtime(monkeypatch)
    _,policy=ctx.metadata.publication_context(ctx.candidate.id)
    snapshot=deepcopy(USAGE_SNAPSHOT);snapshot[action]['status']=status
    policy=replace(policy,usage_rights=MetadataUsageRights.from_snapshot(snapshot))
    ctx.metadata._policy=lambda *args,**kwargs:policy
    summary=ctx.runtime.run_once()
    assert summary.status=='COMPLETED'
    assert summary.results['publish'].review_count==1
    assert summary.results['publish'].previews[0]['reasons']==['METADATA_USAGE_RIGHTS_UNCONFIRMED']
    assert not ctx.fetches and not ctx.tables.calls
    assert ctx.client.rows['agent_metadata_candidates'][0]['status']=='REVIEW_REQUIRED'


def test_permission_reference_must_replay_before_notice_fetch(monkeypatch):
    ctx=setup_runtime(monkeypatch)
    _,policy=ctx.metadata.publication_context(ctx.candidate.id)
    snapshot=deepcopy(USAGE_SNAPSHOT);snapshot['fact_display']['evidence_reference']='B999'
    policy=replace(policy,usage_rights=MetadataUsageRights.from_snapshot(snapshot))
    ctx.metadata._policy=lambda *args,**kwargs:policy
    result=ctx.runtime.run_once().results['publish']
    assert result.review_count==1 and not ctx.tables.calls
    assert ctx.fetches==[policy.permission_url]


def test_explicit_candidate_method_does_not_bypass_action_rights(monkeypatch):
    ctx=setup_runtime(monkeypatch)
    _,policy=ctx.metadata.publication_context(ctx.candidate.id)
    ctx.metadata._policy=lambda *args,**kwargs:replace(policy,usage_rights=None)
    worker=ctx.agent.publish_worker(worker_id='explicit-test')
    result=worker.run_metadata_candidate(ctx.candidate.id,page=fixture()[3],page_url=URL,
        fetched_at=NOW,permission_document=PERMISSION,permission_checked_at=NOW,dry_run=False)
    assert result.status=='HELD' and result.reasons==('METADATA_USAGE_RIGHTS_UNCONFIRMED',)
    assert not ctx.tables.calls
