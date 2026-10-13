// Isolated PostgreSQL only. Does not read Supabase credentials or connect to a cloud DB.
import EmbeddedPostgres from 'embedded-postgres';
import {readFileSync} from 'node:fs';
import {randomUUID} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';

const root=resolve(fileURLToPath(new URL('../..',import.meta.url)));
const agentExtras=process.env.AIPATH_AGENT_EXTRA_MIGRATIONS;
if(!agentExtras) throw new Error('Set AIPATH_AGENT_EXTRA_MIGRATIONS to Agent migrations 010-013');
const pg=new EmbeddedPostgres({databaseDir:join(tmpdir(),`aipath-preview-compat-${randomUUID()}`),
  user:'postgres',password:'isolated-test-only',port:57000+Math.floor(Math.random()*1000),persistent:false});
const sql=(name)=>readFileSync(join(root,name),'utf8');
let db;
const clients=[];
async function query(statement,params=[]){return db.query(statement,params)}
async function digest(){
  return (await query(`select md5(coalesce(string_agg(to_jsonb(o)::text,'|' order by o.id),'')) as hash
    from public.opportunities o`)).rows[0].hash;
}
async function visible(role){
  await query(`set role ${role}`);
  try{return (await query(`select id::text from public.opportunities order by id`)).rows.map(r=>r.id)}
  finally{await query('reset role')}
}
async function denied(statement){
  try{await query(statement);assert.fail('expected permission denial')}
  catch(error){if(error.name==='AssertionError')throw error;assert.equal(error.code,'42501')}
}
try{
  await pg.initialise();await pg.start();db=pg.getPgClient();await db.connect();
  await query('create role anon; create role authenticated; create role service_role bypassrls');
  for(const path of ['supabase/schema.sql','supabase/preview/read_only_api.sql',
    'supabase/preview/seed_synthetic.sql']) await query(sql(path));
  for(const name of [
    '001_agent_foundation.sql','002_collection_foundation.sql',
    '003_discovery_foundation.sql','004_source_review_foundation.sql',
    '005_collection_scheduling.sql','006_processing_foundation.sql',
    '007_publish_automation.sql','008_discovery_scheduling.sql',
    '009_discovery_coverage.sql']) await query(sql(`agent_collect/migrations/${name}`));
  // Later Agent migrations live in the attached Phase 12J worktree for this
  // audit. Never report a full 001-013 integration run when they were omitted.
  for(const name of [
    '010_discovery_exhibition_program.sql','011_goal_driven_discovery.sql',
    '012_exact_ai_plan_claim.sql','013_exact_processing_seed.sql']){
    await query(readFileSync(join(agentExtras,name),'utf8'));
  }
  const agentBefore=(await query(`select md5(string_agg(table_name||':'||column_name||':'||data_type,
    '|' order by table_name,ordinal_position)) as hash,
    count(distinct table_name)::int as n from information_schema.columns
    where table_schema='public' and table_name like 'agent_%'`)).rows[0];
  assert.equal(agentBefore.n,22);
  const before=await digest();
  const scopeBefore=(await query(`select opportunity_id,application_deadline_precision,
    application_deadline_source_date,application_start_snapshot,
    application_deadline_snapshot,event_start_snapshot,event_end_snapshot,
    event_end_precision,last_automated_status,last_automated_updated_at
    from public.opportunity_status_automation_scope order by opportunity_id`)).rows;
  const anonBefore=await visible('anon');
  assert.equal(anonBefore.length,6);
  assert(!anonBefore.includes('00000000-0000-4000-8000-000000000103'));
  const archiveBefore=(await query(`select opportunity_id::text from
    public.opportunity_public_archive_ids(array[
      '00000000-0000-4000-8000-000000000103'::uuid,
      '00000000-0000-4000-8000-000000000104'::uuid,
      '00000000-0000-4000-8000-000000000105'::uuid]) order by 1`)).rows;
  assert.deepEqual(archiveBefore.map(r=>r.opportunity_id),
    ['00000000-0000-4000-8000-000000000104','00000000-0000-4000-8000-000000000105']);
  const recommendBefore=(await query(`select opportunity_id::text,application_precision,
    application_cutoff_at from public.opportunity_recommendation_windows(array[
      '00000000-0000-4000-8000-000000000101'::uuid,
      '00000000-0000-4000-8000-000000000102'::uuid]) order by 1`)).rows;
  await query(sql('supabase/preview/service_schema_compatibility_draft.sql'));
  assert.deepEqual((await query(`select opportunity_id,application_deadline_precision,
    application_deadline_source_date,application_start_snapshot,
    application_deadline_snapshot,event_start_snapshot,event_end_snapshot,
    event_end_precision,last_automated_status,last_automated_updated_at
    from public.opportunity_status_automation_scope order by opportunity_id`)).rows,
    scopeBefore,'existing Preview evidence and worker provenance must be preserved');
  try{
    await query(sql('supabase/preview/service_schema_compatibility_draft.sql'));
    assert.fail('compatibility migration replay must fail closed');
  }catch(error){
    if(error.name==='AssertionError')throw error;
    assert.equal(error.code,'P0001');
    await query('rollback');
  }
  await query(sql('supabase/migrations/004_atomic_opportunity_registration.sql'));
  assert.deepEqual((await query(`select md5(string_agg(table_name||':'||column_name||':'||data_type,
    '|' order by table_name,ordinal_position)) as hash,
    count(distinct table_name)::int as n from information_schema.columns
    where table_schema='public' and table_name like 'agent_%'`)).rows[0],agentBefore);
  assert.equal(await digest(),before,'existing seven Opportunities must not change');
  assert.deepEqual(await visible('anon'),anonBefore,'anon list/detail visibility must remain');
  const archiveAfter=(await query(`select opportunity_id::text from
    public.opportunity_public_archive_ids(array[
      '00000000-0000-4000-8000-000000000103'::uuid,
      '00000000-0000-4000-8000-000000000104'::uuid,
      '00000000-0000-4000-8000-000000000105'::uuid]) order by 1`)).rows;
  assert.deepEqual(archiveAfter,archiveBefore);
  const recommendAfter=(await query(`select opportunity_id::text,application_precision,
    application_cutoff_at from public.opportunity_recommendation_windows(array[
      '00000000-0000-4000-8000-000000000101'::uuid,
      '00000000-0000-4000-8000-000000000102'::uuid]) order by 1`)).rows;
  assert.deepEqual(recommendAfter,recommendBefore);
  assert.equal((await query(`select has_function_privilege('anon',
    'public.opportunity_public_archive_ids(uuid[])','EXECUTE') as allowed`)).rows[0].allowed,true);
  assert.equal((await query(`select has_function_privilege('anon',
    'public.opportunity_recommendation_windows(uuid[])','EXECUTE') as allowed`)).rows[0].allowed,true);
  assert.equal((await query(`select count(*)::int as n from public.opportunity_status_automation_scope
    where evidence_payload->>'source_verification'='NOT_PERFORMED'`)).rows[0].n,4);
  assert.equal((await query(`select count(*)::int as n from public.opportunity_manual_status_audit`)).rows[0].n,0);
  assert.equal((await query(`select count(*)::int as n from pg_extension where extname='pg_cron'`)).rows[0].n,0);
  await query('set role anon');
  assert.deepEqual((await query(`select id::text from public.opportunities
    where status='closed' order by id`)).rows.map(r=>r.id),
    ['00000000-0000-4000-8000-000000000104']);
  assert.deepEqual((await query(`select id::text from public.opportunities
    where status='ended' order by id`)).rows.map(r=>r.id),
    ['00000000-0000-4000-8000-000000000105']);
  assert.equal((await query(`select count(*)::int as n from public.opportunities
    where slug='preview-legacy-closed'`)).rows[0].n,0);
  assert.equal((await query(`select count(*)::int as n from
    public.opportunity_recommendation_windows(array[
      '00000000-0000-4000-8000-000000000102'::uuid])`)).rows[0].n,1);
  await denied(`select * from public.register_opportunity_with_evidence('{}','{}',null,false)`);
  await denied(`select * from public.opportunity_status_automation_scope`);
  await query('reset role');
  await query('set role authenticated');
  await denied(`select * from public.register_opportunity_with_evidence('{}','{}',null,false)`);
  await query('reset role');
  await query('set role service_role');
  const key=`preview-compat-${randomUUID()}`;
  const date=new Date(Date.now()+3*86400000).toISOString().slice(0,10);
  const record={source_id:'00000000-0000-4000-8000-000000000001',
    category_id:'00000000-0000-4000-8000-000000000002',external_id:key,slug:key,
    title:'PREVIEW TEST atomic',summary:'Synthetic registration',organizer:'PREVIEW TEST',
    official_url:`https://example.invalid/${key}`,status:'open',
    application_deadline_at:`${date}T00:00:00+00:00`};
  const evidence={evidence_ref:'preview:synthetic:atomic-e2e',
    application_deadline:{precision:'DATE_ONLY',source_date:date,source_ref:'synthetic fixture'}};
  const registered=(await query(`select * from public.register_opportunity_with_evidence(
    $1::jsonb,$2::jsonb,null,true)`,[JSON.stringify(record),JSON.stringify(evidence)])).rows[0];
  assert.equal(registered.disposition,'registered');
  assert.equal((await query('select status from public.opportunities where id=$1',
    [registered.opportunity_id])).rows[0].status,'open');
  await query('reset role');
  assert.equal((await query(`select count(*)::int as n from public.opportunity_status_automation_scope
    where opportunity_id=$1 and application_deadline_precision='DATE_ONLY'`,
    [registered.opportunity_id])).rows[0].n,1);
  await query('set role service_role');
  const duplicate=(await query(`select * from public.register_opportunity_with_evidence(
    $1::jsonb,$2::jsonb,null,true)`,[JSON.stringify(record),JSON.stringify(evidence)])).rows[0];
  assert.equal(duplicate.disposition,'protected_existing');
  const exactKey=`preview-exact-${randomUUID()}`;
  const exactDeadline=new Date(Date.now()+4*86400000).toISOString();
  const exactRecord={...record,external_id:exactKey,slug:exactKey,
    title:'PREVIEW TEST exact',official_url:`https://example.invalid/${exactKey}`,
    application_deadline_at:exactDeadline};
  const exactEvidence={evidence_ref:'preview:synthetic:exact',
    application_deadline:{precision:'EXACT_TIME',source_literal:exactDeadline,
      source_ref:'synthetic fixture'}};
  const exactResult=(await query(`select * from public.register_opportunity_with_evidence(
    $1::jsonb,$2::jsonb,null,true)`,[JSON.stringify(exactRecord),
      JSON.stringify(exactEvidence)])).rows[0];
  await query('reset role');
  assert.equal((await query(`select application_deadline_precision from
    public.opportunity_status_automation_scope where opportunity_id=$1`,
    [exactResult.opportunity_id])).rows[0].application_deadline_precision,'EXACT_TIME');
  await query('set role service_role');

  const concurrentKey=`preview-concurrent-${randomUUID()}`;
  const concurrentRecord={...exactRecord,external_id:concurrentKey,slug:concurrentKey,
    official_url:`https://example.invalid/${concurrentKey}`};
  const a=pg.getPgClient(),b=pg.getPgClient();clients.push(a,b);
  await a.connect();await b.connect();
  await a.query('set role service_role');await b.query('set role service_role');
  const concurrent=await Promise.all([a,b].map(client=>client.query(
    `select * from public.register_opportunity_with_evidence($1::jsonb,$2::jsonb,null,true)`,
    [JSON.stringify(concurrentRecord),JSON.stringify(exactEvidence)])));
  assert.deepEqual(new Set(concurrent.map(r=>r.rows[0].disposition)),
    new Set(['registered','protected_existing']));
  await query('reset role');
  assert.equal((await query('select count(*)::int as n from public.opportunities where slug=$1',
    [concurrentKey])).rows[0].n,1);
  await query('set role service_role');
  const invalid={...record,external_id:`bad-${key}`,slug:`bad-${key}`,
    official_url:`https://example.invalid/bad-${key}`};
  const wrongEvidence={evidence_ref:'preview:synthetic:invalid',application_deadline:{
    precision:'EXACT_TIME',source_literal:'2026-01-01T12:00:00+09:00'}};
  try{
    await query(`select * from public.register_opportunity_with_evidence(
      $1::jsonb,$2::jsonb,null,true)`,[JSON.stringify(invalid),JSON.stringify(wrongEvidence)]);
    assert.fail('invalid evidence must reject the entire registration');
  }catch(error){
    if(error.name==='AssertionError')throw error;
    assert.equal(error.code,'P0001');
  }
  await query('reset role');
  assert.equal((await query('select count(*)::int as n from public.opportunities where slug=$1',
    [invalid.slug])).rows[0].n,0);
  await query('set role service_role');
  const unknownKey=`preview-unknown-${randomUUID()}`;
  const unknownRecord={...record,external_id:unknownKey,slug:unknownKey,
    official_url:`https://example.invalid/${unknownKey}`};
  try{
    await query(`select * from public.register_opportunity_with_evidence(
      $1::jsonb,$2::jsonb,null,true)`,[JSON.stringify(unknownRecord),
        JSON.stringify({evidence_ref:'preview:synthetic:unknown',
          application_deadline:{precision:'UNKNOWN'}})]);
    assert.fail('UNKNOWN evidence must not register a public Opportunity');
  }catch(error){
    if(error.name==='AssertionError')throw error;
    assert.equal(error.code,'P0001');
  }
  await query('reset role');
  assert.equal((await query('select count(*)::int as n from public.opportunities where slug=$1',
    [unknownKey])).rows[0].n,0);
  const manualId=randomUUID();
  await query(`insert into public.opportunities
    (id,source_id,category_id,title,slug,summary,organizer,official_url,status,published_at)
    values($1,'00000000-0000-4000-8000-000000000001',
      '00000000-0000-4000-8000-000000000002','PREVIEW TEST manual ended',
      $2,'Synthetic manual audit fixture','PREVIEW TEST',$3,'ended',clock_timestamp())
    returning updated_at`,[manualId,`manual-${manualId}`,`https://example.invalid/manual/${manualId}`]);
  await query(`insert into public.opportunity_manual_status_audit
    (opportunity_id,prior_updated_at,resulting_updated_at,status_before,status_after,
      evidence_ref,evidence_basis,evidence_summary,operator_id,authorization_ref,
      public_archive_approved)
    select o.id,o.updated_at - interval '1 minute',o.updated_at,'open','ended',
      'https://example.invalid/fixture','OFFICIAL_POST_EVENT_RECAP',
      'Synthetic audit test','fixture','fixture',true
    from public.opportunities o where o.id=$1`,[manualId]);
  await query('set role anon');
  assert.equal((await query('select count(*)::int as n from public.opportunities where id=$1',
    [manualId])).rows[0].n,1);
  await query('reset role');
  const reconciliation=(await query('select * from public.reconcile_opportunity_statuses()')).rows[0];
  assert.equal(reconciliation.outcome,'completed');
  assert.equal(Number(reconciliation.updated_count),0);
  assert.equal((await query(`select count(*)::int as n from public.opportunities where
    id in ('00000000-0000-4000-8000-000000000101','00000000-0000-4000-8000-000000000102',
    '00000000-0000-4000-8000-000000000103','00000000-0000-4000-8000-000000000104',
    '00000000-0000-4000-8000-000000000105','00000000-0000-4000-8000-000000000106',
    '00000000-0000-4000-8000-000000000107')`)).rows[0].n,7);
  console.log('PASS preview schema bridge + 004; seven rows and public API unchanged');
  console.log('PASS fixture provenance, anon/auth denial, DATE_ONLY/EXACT_TIME/UNKNOWN');
  console.log('PASS compatibility replay denial, concurrent duplicate, rollback');
}finally{
  for(const client of clients){try{await client.end()}catch{}}
  if(db) await db.end();
  await pg.stop().catch(()=>{});
}
