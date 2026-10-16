// Isolated schema/RLS test. Never connects to Preview or Production.
import {readFileSync} from 'node:fs';
import {randomUUID} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import EmbeddedPostgres from 'embedded-postgres';

const root=resolve(fileURLToPath(new URL('../..',import.meta.url)));
const dbDir=join(tmpdir(),'aipath-metadata-'+randomUUID());
const epg=new EmbeddedPostgres({databaseDir:dbDir,user:'postgres',password:'isolated-only',
  port:57000+Math.floor(Math.random()*1000),persistent:false});
const migrations=[
  '001_agent_foundation','002_collection_foundation','003_discovery_foundation',
  '004_source_review_foundation','005_collection_scheduling','006_processing_foundation',
  '007_publish_automation','008_discovery_scheduling','009_discovery_coverage',
  '010_discovery_exhibition_program','011_goal_driven_discovery',
  '012_exact_ai_plan_claim','013_exact_processing_seed','014_metadata_first_draft',
];
let db;
try {
  await epg.initialise();
  await epg.start();
  db=epg.getPgClient();
  await db.connect();
  await db.query('create role anon; create role authenticated; create role service_role bypassrls');
  await db.query(readFileSync(root+'/supabase/schema.sql','utf8'));
  for(const name of migrations)
    await db.query(readFileSync(root+'/agent_collect/migrations/'+name+'.sql','utf8'));
  const source=randomUUID(),other=randomUUID(),approval=randomUUID(),candidate=randomUUID();
  const url='https://fixture.example.org/notices/synthetic';
  const names=['title','summary','organizer','official_url','application_start',
    'application_deadline','event_start','event_end','application_method','category'];
  const facts=Object.fromEntries(names.map(name=>[name,'Synthetic '+name]));
  const evidence=Object.fromEntries(names.map(name=>[name,{reference:'B001'}]));
  await db.query(
    "insert into public.agent_sources (id,name,source_url,canonical_url,source_type,status) "+
    "values ($1,'Synthetic metadata fixture',$2,$2,'official','CANDIDATE'),"+
    "($3,'Other synthetic fixture','https://fixture.example.org/other',"+
    "'https://fixture.example.org/other','official','CANDIDATE')",
    [source,url,other]);
  await db.query(
    "select public.agent_review_source($1,'CANDIDATE','REVIEW',"+
    "'synthetic-fixture-reviewer','isolated test','REVIEW_STARTED',$2)",
    [source,randomUUID()]);
  const baseline=(await db.query('select count(*)::integer as n from public.agent_sources')).rows[0].n;
  await db.query(
    "insert into public.agent_metadata_source_approvals "+
    "(id,source_id,cohort,reviewer,reviewed_at,expires_at,official_url,"+
    "permission_url,permission_digest,organizer_relationship,approved_organizer,page_type,"+
    "scope_origin,path_prefix,"+
    "officiality_confirmed,public_read,automated_access,fact_use,recheck_allowed) "+
    "values ($1,$2,'synthetic','synthetic-fixture-reviewer',"+
    "clock_timestamp()-interval '1 hour',clock_timestamp()+interval '1 day',"+
    "$3,'https://fixture.example.org/policy',$4,'fixture organizer','Fixture Lab','INDIVIDUAL_NOTICE',"+
    "'https://fixture.example.org','/',true,true,true,true,true)",
    [approval,source,url,'a'.repeat(64)]);
  assert.equal((await db.query(
    'select raw_storage_allowed from public.agent_metadata_source_approvals where id=$1',
    [approval])).rows[0].raw_storage_allowed,false);
  await db.query(
    "insert into public.agent_metadata_candidates "+
    "(id,source_id,approval_id,cohort,official_url,facts,field_evidence,"+
    "status,verification,duplicate_result,gate_decision,discovered_at,verified_at) "+
    "values ($1,$2,$3,'synthetic',$4,$5::jsonb,$6::jsonb,'PUBLISH_READY',"+
    "'{\"status\":\"VERIFIED\"}','{\"status\":\"UNIQUE\"}',"+
    "'{\"status\":\"PUBLISH_READY\"}',clock_timestamp(),clock_timestamp())",
    [candidate,source,approval,url,JSON.stringify(facts),JSON.stringify(evidence)]);
  assert.equal((await db.query('select count(*)::integer as n from public.agent_raw_documents'))
    .rows[0].n,0);
  assert.equal((await db.query('select count(*)::integer as n from public.agent_publish_queue'))
    .rows[0].n,0);
  assert.equal((await db.query('select count(*)::integer as n from public.agent_sources'))
    .rows[0].n,baseline);
  await assert.rejects(db.query(
    "insert into public.agent_metadata_candidates "+
    "(source_id,approval_id,cohort,official_url,facts,field_evidence,discovered_at) "+
    "values ($1,$2,'synthetic',$3,'{}','{}',clock_timestamp())",
    [other,approval,url]),/foreign key/);
  await db.query('set role anon');
  await assert.rejects(db.query('select * from public.agent_metadata_candidates'),
    /permission denied/);
  await db.query('reset role; set role authenticated');
  await assert.rejects(db.query('select * from public.agent_metadata_source_approvals'),
    /permission denied/);
  await db.query('reset role; set role service_role');
  assert.equal((await db.query('select count(*)::integer as n from public.agent_metadata_candidates'))
    .rows[0].n,1);
  await assert.rejects(db.query(
    "update public.agent_metadata_source_approvals set reviewer='altered' where id=$1",
    [approval]),/permission denied/);
  await assert.rejects(db.query('delete from public.agent_metadata_candidates where id=$1',
    [candidate]),/permission denied/);
  await db.query('reset role');
  console.log('PASS metadata-only tables, FK, no raw/queue writes, grants, RLS');
} finally {
  if(db) await db.end();
  await epg.stop().catch(()=>{});
}
