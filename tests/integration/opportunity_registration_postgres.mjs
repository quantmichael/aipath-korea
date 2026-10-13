// Local PostgreSQL only. Never reads SUPABASE_URL or production credentials.
import EmbeddedPostgres from 'embedded-postgres';
import {readFileSync, rmSync} from 'node:fs';
import {randomUUID} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';

const root=resolve(fileURLToPath(new URL('../..',import.meta.url)));
const dbDir=join(tmpdir(),`aipath-registration-${randomUUID()}`);
const pg=new EmbeddedPostgres({databaseDir:dbDir,user:'postgres',
  password:'isolated-test-only',port:57000+Math.floor(Math.random()*1000),persistent:false});
const clients=[];
let db;
const sql=(p)=>readFileSync(join(root,p),'utf8');
const q=(query,params=[],client=db)=>client.query(query,params);
async function owner(query,params=[]){
  await q('reset role');
  try{return await q(query,params)}finally{await q('set role service_role')}
}
async function rejects(fn,code){
  try{await fn();assert.fail('expected SQL rejection')}
  catch(error){if(error.name==='AssertionError')throw error;
    if(code)assert.equal(error.code,code);return error}
}
const evidence=(kind='EXACT_TIME',literal=null)=>({
  evidence_ref:`offline:${randomUUID()}`,
  application_deadline:kind==='DATE_ONLY'
    ?{precision:'DATE_ONLY',source_date:literal,source_ref:'fixture'}
    :kind==='EXACT_TIME'
      ?{precision:'EXACT_TIME',source_literal:literal,source_ref:'fixture'}
      :{precision:'UNKNOWN'}
});
function record(source,category,key,deadline,extra={}){
  return {source_id:source,category_id:category,external_id:key,
    slug:key,title:key,summary:'Synthetic fixture',organizer:'Fixture',
    official_url:`https://example.invalid/${key}`,price_type:'unknown',
    application_deadline_at:deadline,...extra};
}
async function register(row,proof,candidate=null,publish=true,client=db){
  return q('select * from public.register_opportunity_with_evidence($1::jsonb,$2::jsonb,$3::uuid,$4)',
    [JSON.stringify(row),JSON.stringify(proof),candidate,publish],client);
}
async function candidate(source,category,row){
  return (await q(`insert into public.opportunity_candidates
    (source_id,category_id,external_id,title,slug,official_url,candidate_status)
    values($1,$2,$3,$4,$5,$6,'verified') returning id`,
    [source,category,row.external_id,row.title,row.slug,row.official_url])).rows[0].id;
}
try{
  await pg.initialise();await pg.start();db=pg.getPgClient();await db.connect();
  await q('create role anon; create role authenticated; create role service_role bypassrls');
  for(const file of ['supabase/schema.sql',
    'supabase/migrations/002_opportunity_status_automation.sql',
    'supabase/migrations/003_manual_status_audit.sql',
    'supabase/migrations/004_atomic_opportunity_registration.sql']){
    await q(sql(file));console.log('PASS migration',file.split('/').at(-1));
  }
  const source=(await q(`insert into public.sources(name) values('Fixture Source') returning id`)).rows[0].id;
  const category=(await q(`insert into public.categories(name,slug) values('Fixture','fixture') returning id`)).rows[0].id;
  const futureDate=new Date(Date.now()+3*86400000).toISOString().slice(0,10);
  const deadline=`${futureDate}T00:00:00+00:00`;
  const legacy=record(source,category,'legacy-date-only',deadline);
  const legacyId=await candidate(source,category,legacy);
  await q('set role service_role');
  let result=await register(legacy,evidence('DATE_ONLY',futureDate),legacyId,true);
  assert.equal(result.rows[0].disposition,'registered');
  const id=result.rows[0].opportunity_id;
  assert.equal((await q('select status from public.opportunities where id=$1',[id])).rows[0].status,'open');
  assert.equal((await owner('select application_deadline_precision from public.opportunity_status_automation_scope where opportunity_id=$1',[id])).rows[0].application_deadline_precision,'DATE_ONLY');
  assert.equal((await q('select candidate_status from public.opportunity_candidates where id=$1',[legacyId])).rows[0].candidate_status,'promoted');
  result=await register(legacy,evidence('DATE_ONLY',futureDate),legacyId,true);
  assert.equal(result.rows[0].disposition,'protected_existing');
  assert.equal((await q('select count(*)::int as n from public.opportunities where slug=$1',[legacy.slug])).rows[0].n,1);
  console.log('PASS Legacy atomic registration, DATE_ONLY evidence, idempotency');

  assert.equal((await owner(`select public.opportunity_status_target(
    '2026-10-12 14:59:59+00',null,'UNKNOWN',null,
    $1::timestamptz,'DATE_ONLY','2026-10-12'::date,
    null,'UNKNOWN',null,'UNKNOWN') as status`,['2026-10-12T00:00:00+00:00'])).rows[0].status,'open');
  assert.equal((await owner(`select public.opportunity_status_target(
    '2026-10-12 15:00:00+00',null,'UNKNOWN',null,
    $1::timestamptz,'DATE_ONLY','2026-10-12'::date,
    null,'UNKNOWN',null,'UNKNOWN') as status`,['2026-10-12T00:00:00+00:00'])).rows[0].status,'closed');
  const past=record(source,category,'past-cron-fixture','2026-10-12T00:00:00+00:00');
  const pastCandidate=await candidate(source,category,past);
  const pastDraft=await register(past,evidence('DATE_ONLY','2026-10-12'),pastCandidate,false);
  const pastId=pastDraft.rows[0].opportunity_id;
  await owner(`update public.opportunities set status='open',published_at=now()
    where id=$1`,[pastId]);
  await owner('select * from public.reconcile_opportunity_statuses()');
  assert.equal((await q('select status from public.opportunities where id=$1',[pastId])).rows[0].status,'closed');
  assert.equal((await q('select count(*)::int as n from public.opportunity_public_archive_ids(array[$1::uuid])',[pastId])).rows[0].n,1);
  console.log('PASS KST midnight boundary, Cron close, worker archive');

  const expired=record(source,category,'expired-new','2026-10-12T00:00:00+00:00');
  const expiredCandidate=await candidate(source,category,expired);
  await rejects(()=>register(expired,evidence('DATE_ONLY','2026-10-12'),
    expiredCandidate,true),'P0001');
  assert.equal((await q('select count(*)::int as n from public.opportunities where slug=$1',
    [expired.slug])).rows[0].n,0);
  console.log('PASS expired new registration fails closed before publication');

  const start=new Date(Date.now()+86400000).toISOString();
  const scheduled=record(source,category,'scheduled-new',
    new Date(Date.now()+2*86400000).toISOString(),{
      status:'open',application_start_at:start});
  const scheduledProof=evidence('EXACT_TIME',scheduled.application_deadline_at);
  scheduledProof.application_start={
    precision:'EXACT_TIME',source_literal:start,source_ref:'fixture'};
  result=await register(scheduled,scheduledProof);
  assert.equal((await q('select status from public.opportunities where id=$1',
    [result.rows[0].opportunity_id])).rows[0].status,'scheduled');
  console.log('PASS server computes scheduled instead of trusting caller status');

  const bad=record(source,category,'invalid-exact',deadline);
  const badId=await candidate(source,category,bad);
  await rejects(()=>register(bad,evidence('EXACT_TIME','2026-10-12T10:00:00+09:00'),badId,true),'P0001');
  assert.equal((await q('select count(*)::int as n from public.opportunities where slug=$1',[bad.slug])).rows[0].n,0);
  assert.equal((await q('select candidate_status from public.opportunity_candidates where id=$1',[badId])).rows[0].candidate_status,'verified');
  const unknown=record(source,category,'unknown-public',deadline);
  const unknownId=await candidate(source,category,unknown);
  await rejects(()=>register(unknown,evidence('UNKNOWN'),unknownId,true),'P0001');
  assert.equal((await q('select count(*)::int as n from public.opportunities where slug=$1',[unknown.slug])).rows[0].n,0);
  result=await register(unknown,evidence('UNKNOWN'),unknownId,false);
  assert.equal(result.rows[0].disposition,'registered');
  assert.equal((await q('select status from public.opportunities where id=$1',[result.rows[0].opportunity_id])).rows[0].status,'draft');
  console.log('PASS invalid EXACT_TIME rollback; UNKNOWN held from public registration');

  const outage=record(source,category,'evidence-outage',
    new Date(Date.now()+86400000).toISOString(),{status:'open'});
  await owner(`create function public.offline_evidence_fault() returns trigger
    language plpgsql as $$ begin raise exception 'offline evidence fault'; end $$;
    create trigger offline_evidence_fault before insert on
    public.opportunity_status_automation_scope for each row
    execute function public.offline_evidence_fault();`);
  await rejects(()=>register(outage,evidence('EXACT_TIME',outage.application_deadline_at)));
  await owner(`drop trigger offline_evidence_fault on public.opportunity_status_automation_scope;
    drop function public.offline_evidence_fault();`);
  assert.equal((await q('select count(*)::int as n from public.opportunities where slug=$1',
    [outage.slug])).rows[0].n,0);
  console.log('PASS injected Evidence persistence failure rolls back public INSERT');

  const exactEnd=new Date(Date.now()+2000).toISOString();
  const exactStart=new Date(Date.now()-3600000).toISOString();
  const agent=record(source,category,'agent-ended',deadline,{
    status:'ongoing',event_start_at:exactStart,event_end_at:exactEnd});
  const agentProof=evidence('EXACT_TIME',deadline);
  agentProof.event_start={precision:'EXACT_TIME',source_literal:exactStart,source_ref:'fixture'};
  agentProof.event_end={precision:'EXACT_TIME',source_literal:exactEnd,source_ref:'fixture'};
  result=await register(agent,agentProof);
  const agentRecord=result.rows[0].opportunity_id;
  assert.equal(result.rows[0].disposition,'registered');
  assert.equal((await q('select status from public.opportunities where id=$1',[agentRecord])).rows[0].status,'ongoing');
  await q('select pg_sleep(2.2)');
  await owner('select * from public.reconcile_opportunity_statuses()');
  assert.equal((await q('select status from public.opportunities where id=$1',[agentRecord])).rows[0].status,'ended');
  console.log('PASS Agent exact-time registration and ongoing to ended transition');

  const protectedRow=record(source,category,'legacy-private',deadline,{status:'closed'});
  await q('reset role');
  const privateId=(await q(`insert into public.opportunities
    (source_id,category_id,external_id,slug,title,summary,organizer,official_url,status,published_at)
    values($1,$2,$3,$3,$3,'Fixture','Fixture',$4,'closed',now()) returning id`,
    [source,category,protectedRow.slug,protectedRow.official_url])).rows[0].id;
  await q('set role service_role');
  assert.equal((await q('select count(*)::int as n from public.opportunity_public_archive_ids(array[$1::uuid])',[privateId])).rows[0].n,0);
  console.log('PASS pre-existing closed remains private');

  const concurrent=record(source,category,'concurrent',new Date(Date.now()+86400000).toISOString(),{status:'open'});
  const proof=evidence('EXACT_TIME',concurrent.application_deadline_at);
  const a=pg.getPgClient(),b=pg.getPgClient();clients.push(a,b);
  await a.connect();await b.connect();await a.query('set role service_role');await b.query('set role service_role');
  const parallel=await Promise.all([
    register(concurrent,proof,null,true,a),
    register(concurrent,proof,null,true,b)]);
  assert.deepEqual(new Set(parallel.map(x=>x.rows[0].disposition)),
    new Set(['registered','protected_existing']));
  assert.equal((await q('select count(*)::int as n from public.opportunities where slug=$1',[concurrent.slug])).rows[0].n,1);
  console.log('PASS two-session concurrent duplicate registration');

  await q('set role anon',[],a);
  await rejects(()=>register(record(source,category,'unauthorized',deadline,{status:'open'}),
    evidence('EXACT_TIME',deadline),null,true,a),'42501');
  await q('reset role',[],a);
  assert.equal((await q(`select relrowsecurity from pg_class where oid=
    'public.opportunity_manual_status_audit'::regclass`)).rows[0].relrowsecurity,true);
  console.log('PASS anon RPC denial and manual audit RLS');
}finally{
  for(const client of clients){try{await client.end()}catch{}}
  if(db){try{await db.end()}catch{}}
  await pg.stop();rmSync(dbDir,{recursive:true,force:true});
}
