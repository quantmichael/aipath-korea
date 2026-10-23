"""Real Runtime -> disposable PostgreSQL persistence; no Supabase or real Auth.

Run explicitly with AIPATH_TEST_EMBEDDED_PG pointing to the installed test module.
Only SDK wire transport and webpage fetch are substituted. Source review, scoped
approval RPC, Candidate/assessment RPC, global lease, Evidence, dedup, mapping,
Atomic Publish and readback execute real repository code/SQL. Fixture identities
are local SQL role claims, not authenticated Supabase users. No real credentials.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from agent_collect.collectors.transport import HttpResponse
from agent_collect.config.settings import load_settings
from agent_collect.discovery.followup import FollowupLead
from agent_collect.metadata.supabase import SupabaseMetadataEvidence
from agent_collect.repositories.supabase import SupabaseSourceRepository
from agent_collect.runtime import AgentRuntime
from tests.test_agent_collect_metadata_evidence import USAGE_SNAPSHOT

PERMISSION = b'Isolated fixture: transient read, fact use and recheck permitted for tests only.'


class Query:
    def __init__(self, bridge, name=None, rpc=None, args=None):
        self.bridge = bridge
        self.query = ({'kind':'rpc','name':rpc,'args':args} if rpc else
                      {'kind':'table','name':name,'columns':'*','filters':[],
                       'order':[],'limit':None})
    def select(self, columns, **kwargs): self.query['columns']=columns; return self
    def eq(self, key, value): self.query['filters'].append(['eq',key,value]); return self
    def in_(self, key, values): self.query['filters'].append(['in',key,values]); return self
    def order(self, key): self.query['order'].append(key); return self
    def limit(self, n): self.query['limit']=n; return self
    def insert(self, row): self.query['insert']=row; return self
    def execute(self):
        return SimpleNamespace(count=None, **self.bridge.request(self.query)) if self.query['kind']=='rpc' else SimpleNamespace(**self.bridge.request(self.query))


class Bridge:
    def __init__(self):
        # No inherited Supabase/provider/GCP secrets reach the disposable server.
        env = {key:os.environ[key] for key in ('PATH','HOME','TMPDIR','AIPATH_TEST_EMBEDDED_PG','AIPATH_TEST_USAGE_GUARD_SQL') if key in os.environ}
        self.proc = subprocess.Popen([os.environ.get('AIPATH_TEST_NODE','node'),
            str(Path(__file__).with_name('metadata_runtime_pg_bridge.mjs'))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
        line = self.proc.stdout.readline()
        if not line:
            self.proc.wait(timeout=30)
            raise RuntimeError('Disposable PostgreSQL startup failed; see local stderr')
        self.ready = json.loads(line)
        assert self.ready.get('ready'), self.ready
    def request(self, request):
        self.proc.stdin.write(json.dumps(request)+'\n'); self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line: raise RuntimeError('isolated bridge exited')
        result=json.loads(line)
        if 'error' in result: raise RuntimeError(result['error'])
        return result
    def table(self, name): return Query(self,name=name)
    def rpc(self, name, args): return Query(self,rpc=name,args=args)
    def inspect(self): return self.request({'kind':'inspect'})['data']
    def close(self):
        if self.proc.poll() is None:
            self.proc.stdin.write('{"kind":"stop"}\n'); self.proc.stdin.flush()
        assert self.proc.wait(timeout=30)==0


def now(): return datetime.now(timezone.utc)


def main():
    bridge=Bridge()
    pages={}
    metadata=SupabaseMetadataEvidence(bridge)
    settings=replace(load_settings(environ={}),automation_enabled=True,
                     publish_enabled=True,publish_worker_enabled=True)
    agent=AgentRuntime(SupabaseSourceRepository(bridge),None,None,settings)
    runtime=agent.automation_runtime(Path('/unused'),worker_id='isolated-pg-runtime')
    fetches=[]
    def fetch(explorer,url,reviewed,*,request_policy=None):
        if request_policy is not None: assert request_policy(url)
        fetches.append(url)
        return HttpResponse(200,{'content-type':'text/html'},
                            PERMISSION if url.endswith('/policy') else pages[url],url)
    def make(category, *, ready=True, usage_rights=USAGE_SNAPSHOT):
        title=' '.join(uuid4().hex for _ in range(3))
        organizer=uuid4().hex
        row=bridge.request({'kind':'fixture','args':{'category':category,'organizer':organizer,
             'permission_digest':hashlib.sha256(PERMISSION).hexdigest(),'usage_rights':usage_rights}})['data']
        dates=[str(now().date()+timedelta(days=d)) for d in (2,12,15,16)]
        page=(f'<p>Title: {title}</p><p>Organizer: {organizer}</p>'
              '<p>Summary: This is a disposable local fixture.</p>'
              f'<p>Application opens: {dates[0]}</p><p>Application deadline: {dates[1]}</p>'
              f'<p>Event starts: {dates[2]}</p><p>Event ends: {dates[3]}</p>'
              '<p>Application method: Synthetic form only</p>'
              f'<p>Category: {category}</p>').encode()
        pages[row['url']]=page
        row['title']=title
        if not ready: return row
        lead=FollowupLead(row['url'],row['url'],1,title,'INDIVIDUAL_NOTICE',
                          None,None,'UNKNOWN','UNKNOWN',None,'isolated scoped approval')
        at=now()
        result=metadata.process_followup_lead(lead=lead,approval_id=UUID(row['approval']),
            facts=None,page=page,fetched_at=at,permission_document=PERMISSION,
            permission_checked_at=at,request_id=uuid4(),decision_id=uuid4(),now=at)
        assert result.outcome.verification.status=='VERIFIED', result.outcome
        assert result.outcome.duplicate.status.value=='UNIQUE', result.outcome
        assert result.outcome.gate.status.value=='PUBLISH_READY', result.outcome
        assert result.stored_candidate_id
        row['candidate']=str(result.stored_candidate_id)
        return row
    def no_network(*args,**kwargs): raise AssertionError('Python network access prohibited')
    try:
        with patch('socket.socket.connect',no_network),patch('socket.create_connection',no_network),patch(
                'agent_collect.discovery.followup.OfficialFollowupExplorer._fetch',fetch):
            categories=[]
            previous={}
            for category in ('conference','competition','hackathon','education','meetup'):
                row=make(category)
                before=bridge.inspect()
                dry=runtime.run_once(dry_run=True)
                assert dry.results['publish'].metadata_due_count==1 and not fetches
                assert before==bridge.inspect(), 'dry run changed DB'
                result=runtime.run_once()
                assert result.status=='COMPLETED', result
                assert result.results['publish'].published_count==1, result
                state=bridge.inspect()
                assert len(state['opportunities'])==len(categories)+1
                assert len(state['agent_metadata_publish_completions'])==len(categories)+1
                assert len(state['opportunity_status_automation_scope'])==len(categories)+1
                current={item['id']:item for item in state['opportunities']}
                assert all(current[key]==value for key,value in previous.items()), 'prior row mutated'
                assert all(not r['active'] for r in state['agent_metadata_reviewers'])
                assert all(r['claim_id'] is None for r in state['agent_runtime_leases'])
                fetches.clear()
                retry=runtime.run_once()
                assert retry.results['publish'].metadata_attempted_count==0 and not fetches
                assert bridge.inspect()['opportunities']==state['opportunities']
                categories.append(category); previous=current
            # Now include the actual Runtime handoff, with only the Discovery
            # transport represented by a stored-query summary. Candidate creation,
            # extraction, assessment and publish all run inside run_once.
            discovered=make('conference',ready=False)
            query_id=bridge.request({'kind':'discovery','source':discovered['source'],
                                     'title':discovered['title']})['data']
            class StoredDiscovery:
                def run_once(self, *, dry_run=False):
                    return SimpleNamespace(query_ids=() if dry_run else (query_id,))
            integrated_agent=AgentRuntime(agent.sources,None,None,replace(settings,
                discovery_enabled=True,discovery_worker_enabled=True))
            before_full=bridge.inspect()
            with patch.object(AgentRuntime,'discovery_worker',lambda *args,**kwargs:StoredDiscovery()):
                integrated=integrated_agent.automation_runtime(Path('/unused'),worker_id='isolated-full-runtime')
                dry=integrated.run_once(dry_run=True)
                assert dry.status=='COMPLETED' and not fetches and 'metadata' not in dry.results
                assert bridge.inspect()==before_full
                full=integrated.run_once()
            assert full.status=='COMPLETED',full
            assert full.results['metadata']['processed_count']==1,full
            assert full.results['metadata']['publish_ready_count']==1,full
            assert full.results['publish'].published_count==1,full
            full_state=bridge.inspect()
            assert len(full_state['opportunities'])==6
            current={item['id']:item for item in full_state['opportunities']}
            assert all(current[key]==value for key,value in previous.items())
            fetches.clear()
            assert runtime.run_once().results['publish'].metadata_attempted_count==0 and not fetches
            revoked=make('competition')
            bridge.request({'kind':'revoke','source':revoked['source']})
            revoked_run=runtime.run_once()
            assert revoked_run.results['publish'].metadata_attempted_count==0
            state=bridge.inspect()
            assert next(c for c in state['agent_metadata_candidates'] if c['id']==revoked['candidate'])['status']=='REVIEW_REQUIRED'
            assert len(state['opportunities'])==6 and not fetches
            insufficient=make('education',usage_rights=None)
            held=runtime.run_once()
            assert held.status=='COMPLETED' and held.results['publish'].review_count==1
            assert held.results['publish'].failed_count==0 and not fetches
            rights_state=bridge.inspect()
            assert len(rights_state['opportunities'])==6
            assert next(c for c in rights_state['agent_metadata_candidates'] if c['id']==insufficient['candidate'])['status']=='REVIEW_REQUIRED'
            rollback=make('hackathon')
            before=bridge.inspect()
            bridge.request({'kind':'completion_fault'})
            failed=runtime.run_once()
            assert failed.status=='PARTIAL' and failed.errors=={'publish':'PUBLISH_INCOMPLETE'},failed
            assert failed.results['publish'].failed_count==1 and failed.results['publish'].metadata_attempted_count==1
            after=bridge.inspect()
            for table in ('opportunities','opportunity_status_automation_scope','agent_metadata_publish_completions'):
                assert before[table]==after[table], 'atomic rollback failed '+table
            assert all(r['claim_id'] is None for r in after['agent_runtime_leases'])
            assert all(not r['active'] for r in after['agent_metadata_reviewers'])
            print(json.dumps({'result':'PASS','postgres':bridge.ready['version'],
                'categories':categories,'atomic_publications':6,'full_runtime_discovery_to_publish':'PASS','usage_guard_019':bridge.ready['usage_guard'],'insufficient_rights':'HELD_BEFORE_HTTP_OR_PUBLISH','retry_new_publications':0,
                'revocation':'HELD','completion_failure':'ROLLED_BACK',
                'lease_released':True,'reviewers_off':True,'prior_opportunities_unchanged':True,
                'transport':'test SQL bridge; not Supabase SDK/Auth','external_http_calls':0}))
    finally:
        bridge.close()


if __name__=='__main__': main()
