// Isolated 014+015 contract test; never connects to Supabase.
import {readFileSync} from 'node:fs';
import {randomUUID} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import EmbeddedPostgres from 'embedded-postgres';

const root=resolve(fileURLToPath(new URL('../..',import.meta.url)));
const epg=new EmbeddedPostgres({databaseDir:join(tmpdir(),'aipath-metadata-approval-'+randomUUID()),
  user:'postgres',password:'isolated-only',port:58000+Math.floor(Math.random()*500),
  persistent:false});
let db,db2;
try {
  await epg.initialise(); await epg.start();
  db=epg.getPgClient(); await db.connect();
  await db.query("create role anon; create role authenticated; create role service_role bypassrls;"+
    "create schema auth; create function auth.uid() returns uuid language sql stable as $$"+
    "select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid$$;");
  await db.query(readFileSync(root+'/supabase/schema.sql','utf8'));
  const names=['001_agent_foundation','002_collection_foundation','003_discovery_foundation',
    '004_source_review_foundation','005_collection_scheduling','006_processing_foundation',
    '007_publish_automation','008_discovery_scheduling','009_discovery_coverage',
    '010_discovery_exhibition_program','011_goal_driven_discovery',
    '012_exact_ai_plan_claim','013_exact_processing_seed',
    '014_metadata_first_draft','015_metadata_approval_runtime_draft'];
  for(const name of names)
    await db.query(readFileSync(root+'/agent_collect/migrations/'+name+'.sql','utf8'));
  const unscoped=randomUUID();
  await db.query("insert into public.agent_metadata_reviewers(user_id,granted_by)"+
    " values($1,'unscoped fixture')",[unscoped]);
  const scopeSql=readFileSync(root+'/agent_collect/migrations/'+
    '016_metadata_reviewer_source_scope.sql','utf8');
  await assert.rejects(db.query(scopeSql),/agent_metadata_active_reviewer_scoped/);
  await db.query('rollback');
  await db.query('delete from public.agent_metadata_reviewers where user_id=$1',[unscoped]);
  await db.query(scopeSql);
  await db.query(readFileSync(root+'/agent_collect/migrations/'+
    '017_metadata_service_category_gate.sql','utf8'));
  const source=randomUUID(),reviewer=randomUUID(),outsider=randomUUID();
  const url='https://fixture.example.org/notices/approval-test';
  await db.query("insert into public.agent_sources(id,name,source_url,canonical_url,source_type)"+
    "values($1,'Synthetic Source',$2,$2,'official')",[source,url]);
  await db.query("select public.agent_review_source($1,'CANDIDATE','REVIEW',"+
    "'synthetic-fixture-reviewer','isolated review','REVIEW_STARTED',$2)",
    [source,randomUUID()]);
  await db.query("insert into public.agent_metadata_reviewers(user_id,source_id,granted_by)"+
    "values($1,$2,'isolated-postgres-fixture')",[reviewer,source]);
  const policy={official_url:url,expires_at:'2027-12-31T00:00:00Z',
    permission_url:'https://fixture.example.org/policy',permission_digest:'a'.repeat(64),
    organizer_relationship:'fixture organizer',approved_organizer:'Fixture Lab',
    page_type:'INDIVIDUAL_NOTICE',scope_origin:'https://fixture.example.org',
    path_prefix:'/',officiality_confirmed:true,public_read:true,
    automated_access:true,fact_use:true,recheck_allowed:true,
    raw_storage_allowed:false,redistribution_allowed:false};
  const decision=randomUUID();
  await db.query('set role authenticated');
  await db.query("select set_config('request.jwt.claim.sub',$1,false)",[outsider]);
  await assert.rejects(db.query('select public.agent_metadata_approve($1,$2,$3,$4)',
    [randomUUID(),source,policy,'synthetic fixture approval']),/reviewer required/);
  await db.query("select set_config('request.jwt.claim.sub',$1,false)",[reviewer]);
  const approve=()=>db.query('select public.agent_metadata_approve($1,$2,$3,$4) as id',
    [decision,source,policy,'synthetic fixture approval']);
  const approval=(await approve()).rows[0].id;
  assert.equal((await approve()).rows[0].id,approval);
  await assert.rejects(db.query('select public.agent_metadata_approve($1,$2,$3,$4)',
    [randomUUID(),source,policy,'synthetic fixture approval']),/must be revoked first/);
  await db.query('reset role');
  const names10=['title','summary','organizer','official_url','application_start',
    'application_deadline','event_start','event_end','application_method','category'];
  const facts=Object.fromEntries(names10.map(x=>[x,x==='official_url'?url:'Synthetic '+x]));
  facts.application_start='2027-01-01'; facts.application_deadline='2027-04-01';
  facts.event_start='2027-04-02'; facts.event_end='2027-04-03';
  facts.category='conference';
  const evidence=Object.fromEntries(names10.map(x=>[x,{reference:'B001'}]));
  const testTime=new Date().toISOString();
  await db.query('set role service_role');
  await assert.rejects(db.query("insert into public.agent_metadata_candidates"+
    "(source_id,approval_id,cohort,official_url,facts,field_evidence,discovered_at)"+
    "values($1,$2,'operational',$3,$4,$5,$6)",
    [source,approval,url,facts,evidence,testTime]),/permission denied/);
  await assert.rejects(db.query("insert into public.agent_metadata_source_approvals"+
    "(source_id,cohort,reviewer,reviewed_at,expires_at,official_url,permission_url,"+
    "permission_digest,organizer_relationship,approved_organizer,page_type,scope_origin,"+
    "path_prefix,officiality_confirmed,public_read,automated_access,fact_use,recheck_allowed)"+
    "values($1,'operational','forged',now(),now()+interval '1 day',$2,$3,$4,"+
    "'x','Fixture Lab','INDIVIDUAL_NOTICE',$5,'/',true,true,true,true,true)",
    [source,url,policy.permission_url,policy.permission_digest,policy.scope_origin]),
    /permission denied/);
  const request=randomUUID();
  const create=()=>db.query('select public.agent_metadata_create_candidate($1,$2,$3,$4,$5) as id',
    [request,approval,facts,evidence,testTime]);
  const candidate=(await create()).rows[0].id;
  assert.equal((await create()).rows[0].id,candidate);
  const samePeriod=(await db.query('select public.agent_metadata_create_candidate($1,$2,$3,$4,$5) as id',
    [randomUUID(),approval,facts,evidence,testTime])).rows[0].id;
  assert.equal(samePeriod,candidate);
  // Migration 015 returns the same period's existing ID even for different
  // evidence. The SDK must read it back and fail rather than relabel old facts.
  const changedEvidence={...evidence,title:{reference:'B999'}};
  const reused=(await db.query('select public.agent_metadata_create_candidate($1,$2,$3,$4,$5) as id',
    [randomUUID(),approval,{...facts,title:'Different title'},changedEvidence,testTime])).rows[0].id;
  assert.equal(reused,candidate);
  const preserved=(await db.query('select facts,field_evidence,status from public.agent_metadata_candidates where id=$1',
    [candidate])).rows[0];
  assert.deepEqual(preserved.facts,facts);
  assert.deepEqual(preserved.field_evidence,evidence);
  assert.equal(preserved.status,'REVIEW_REQUIRED');
  const verification={status:'VERIFIED',time_precision:'DATE_ONLY',evidence_refs:[url+'#B001']};
  const duplicate={status:'UNIQUE'},gate={status:'PUBLISH_READY'},assessId=randomUUID();
  await assert.rejects(db.query(
    'select public.agent_metadata_record_assessment($1,$2,$3,$4,$5,$6,$7,$8)',
    [randomUUID(),candidate,'REVIEW_REQUIRED','PUBLISH_READY',
      {status:'VERIFIED',time_precision:'DATE_ONLY',evidence_refs:[]},
      duplicate,gate,testTime]),/prerequisites failed/);
  const record=()=>db.query('select public.agent_metadata_record_assessment($1,$2,$3,$4,$5,$6,$7,$8) as id',
    [assessId,candidate,'REVIEW_REQUIRED','PUBLISH_READY',verification,duplicate,gate,
      testTime]);
  assert.equal((await record()).rows[0].id,candidate);
  assert.equal((await record()).rows[0].id,candidate);
  assert.equal((await db.query('select status from public.agent_metadata_candidates where id=$1',
    [candidate])).rows[0].status,'PUBLISH_READY');
  await db.query('reset role');
  const readyInsert=(readyFacts,readyEvidence)=>db.query(
    'insert into public.agent_metadata_candidates '+
    '(source_id,approval_id,cohort,official_url,facts,field_evidence,status,'+
    'verification,duplicate_result,gate_decision,discovered_at,verified_at) '+
    "values($1,$2,'operational',$3,$4,$5,'PUBLISH_READY',$6,$7,$8,$9,$9)",
    [source,approval,url,readyFacts,readyEvidence,
      {status:'VERIFIED'}, {status:'UNIQUE'}, {status:'PUBLISH_READY'}, testTime]);
  const optionalFacts={...facts,category:'competition',
    application_start:'2027-02-01',application_deadline:'2027-05-01'};
  delete optionalFacts.event_start; delete optionalFacts.event_end;
  const optionalEvidence={...evidence};
  delete optionalEvidence.event_start; delete optionalEvidence.event_end;
  await readyInsert(optionalFacts,optionalEvidence);
  await assert.rejects(readyInsert({...optionalFacts,category:'grant',
    application_start:'2027-02-02'},optionalEvidence),/agent_metadata_candidate_gate/);
  await assert.rejects(readyInsert({...optionalFacts,category:'conference',
    application_start:'2027-02-03'},optionalEvidence),/agent_metadata_candidate_gate/);
  await db.query('reset role; set role authenticated');
  await db.query("select set_config('request.jwt.claim.sub',$1,false)",[reviewer]);
  const revokeId=randomUUID();
  const revoke=()=>db.query('select public.agent_metadata_revoke($1,$2,$3) as id',
    [revokeId,approval,'synthetic permission revoked']);
  const revoked=(await revoke()).rows[0].id;
  assert.equal((await revoke()).rows[0].id,revoked);
  await db.query('reset role; set role service_role');
  assert.equal((await db.query('select status from public.agent_metadata_candidates where id=$1',
    [candidate])).rows[0].status,'REVIEW_REQUIRED');
  await assert.rejects(db.query('select public.agent_metadata_create_candidate($1,$2,$3,$4,$5)',
    [randomUUID(),approval,facts,evidence,testTime]),/active reviewed/);
  await db.query('reset role; set role anon');
  await assert.rejects(db.query('select public.agent_metadata_approve($1,$2,$3,$4)',
    [randomUUID(),source,policy,'synthetic fixture approval']),/permission denied/);
  await db.query('reset role');
  assert.equal((await db.query('select count(*)::integer as n from public.agent_raw_documents'))
    .rows[0].n,0);
  assert.equal((await db.query('select count(*)::integer as n from public.agent_publish_queue'))
    .rows[0].n,0);
  const source2=randomUUID(),url2='https://fixture.example.org/notices/concurrent';
  await db.query('reset role');
  await db.query("insert into public.agent_sources(id,name,source_url,canonical_url,source_type)"+
    "values($1,'Concurrent Synthetic Source',$2,$2,'official')",[source2,url2]);
  await db.query("select public.agent_review_source($1,'CANDIDATE','REVIEW',"+
    "'synthetic-fixture-reviewer','isolated concurrency','REVIEW_STARTED',$2)",
    [source2,randomUUID()]);
  await db.query('set role authenticated');
  await db.query("select set_config('request.jwt.claim.sub',$1,false)",[reviewer]);
  await assert.rejects(db.query('select public.agent_metadata_approve($1,$2,$3,$4)',
    [randomUUID(),source2,{...policy,official_url:url2},'wrong Source scope']),
    /source-scoped metadata reviewer required/);
  await db.query('reset role');
  assert.equal((await db.query('select count(*)::integer as n from public.agent_metadata_approval_events'+
    ' where source_id=$1',[source2])).rows[0].n,0);
  assert.equal((await db.query('select count(*)::integer as n from public.agent_metadata_source_approvals'+
    ' where source_id=$1',[source2])).rows[0].n,0);
  await assert.rejects(db.query('update public.agent_metadata_reviewers set source_id=$1'+
    ' where user_id=$2',[source2,reviewer]),/deactivate reviewer before changing Source scope/);
  await db.query('update public.agent_metadata_reviewers set active=false where user_id=$1',
    [reviewer]);
  await db.query('update public.agent_metadata_reviewers set source_id=$1,active=true'+
    ' where user_id=$2',[source2,reviewer]);
  db2=epg.getPgClient(); await db2.connect();
  for(const client of [db,db2]) {
    await client.query('set role authenticated');
    await client.query("select set_config('request.jwt.claim.sub',$1,false)",[reviewer]);
  }
  const concurrentDecision=randomUUID(),policy2={...policy,official_url:url2};
  const [left,right]=await Promise.all([db,db2].map(client=>client.query(
    'select public.agent_metadata_approve($1,$2,$3,$4) as id',
    [concurrentDecision,source2,policy2,'synthetic concurrent approval'])));
  assert.equal(left.rows[0].id,right.rows[0].id);
  await db.query('reset role'); await db2.query('reset role');
  assert.equal((await db.query('select count(*)::integer as n from public.agent_metadata_approval_events'+
    ' where source_id=$1 and action=\'APPROVED\'',[source2])).rows[0].n,1);
  await db.query('update public.agent_metadata_reviewers set active=false where user_id=$1',
    [reviewer]);
  await db.query('update public.agent_metadata_reviewers set source_id=$1,active=true'+
    ' where user_id=$2',[source,reviewer]);
  await db.query('set role authenticated');
  await db.query("select set_config('request.jwt.claim.sub',$1,false)",[reviewer]);
  await assert.rejects(db.query('select public.agent_metadata_revoke($1,$2,$3)',
    [randomUUID(),left.rows[0].id,'wrong Source scope revocation']),
    /source-scoped metadata reviewer required/);
  await db.query('reset role');
  assert.equal((await db.query('select count(*)::integer as n from public.agent_metadata_approval_events'+
    ' where source_id=$1 and action=\'REVOKED\'',[source2])).rows[0].n,0);
  console.log('PASS metadata reviewer auth, audit, idempotency, RPC-only writes, revocation, RLS');
} finally {
  if(db2) await db2.end();
  if(db) await db.end();
  await epg.stop().catch(()=>{});
}
