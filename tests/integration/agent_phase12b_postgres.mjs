// Isolated PHASE 12B SQL/RPC test. No production credentials or network services.
import {readFileSync, rmSync} from 'node:fs';
import {randomUUID} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import EmbeddedPostgres from 'embedded-postgres';

const root = process.env.AIPATH_PHASE12B_ROOT || resolve(fileURLToPath(new URL('../..', import.meta.url)));
const scratch = join(tmpdir(), `aipath-phase12b-${randomUUID()}`);
const epg = new EmbeddedPostgres({
  databaseDir: scratch, user: 'postgres', password: 'isolated-test-only',
  port: 57000 + Math.floor(Math.random() * 1000), persistent: false,
});
const migrations = [
  '001_agent_foundation', '002_collection_foundation',
  '003_discovery_foundation', '004_source_review_foundation',
  '005_collection_scheduling', '006_processing_foundation',
  '007_publish_automation', '008_discovery_scheduling',
  '009_discovery_coverage', '010_discovery_exhibition_program',
  '011_goal_driven_discovery',
];
let db, second;
async function reserve(kind, scope, key, query=null, client=db) {
  const result = await client.query(
    'select public.agent_reserve_provider_budget($1,$2,$3,$4) as id',
    [kind, scope, key, query]);
  return result.rows[0].id;
}
async function failed(action, pattern) {
  await assert.rejects(action, pattern);
}
try {
  await epg.initialise();
  await epg.start();
  db = epg.getPgClient();
  second = epg.getPgClient();
  await db.connect();
  await second.connect();
  await db.query('create role anon; create role authenticated; create role service_role bypassrls');
  await db.query(readFileSync(`${root}/supabase/schema.sql`, 'utf8'));
  for (const migration of migrations) {
    await db.query(readFileSync(`${root}/agent_collect/migrations/${migration}.sql`, 'utf8'));
  }
  const tables = (await db.query(`select relname,relrowsecurity from pg_class where relname in
    ('agent_provider_budget_reservations','agent_discovery_ai_plans',
     'agent_discovery_ai_plan_queries') order by relname`)).rows;
  assert.equal(tables.length, 3);
  assert(tables.every(row => row.relrowsecurity));
  const permission = (await db.query(`select
    has_table_privilege('anon','public.agent_provider_budget_reservations','SELECT') as anon_read,
    has_function_privilege('anon','public.agent_reserve_provider_budget(text,text,text,text)','EXECUTE') as anon_rpc,
    has_function_privilege('service_role','public.agent_reserve_provider_budget(text,text,text,text)','EXECUTE') as service_rpc`)).rows[0];
  assert.deepEqual(permission, {anon_read: false, anon_rpc: false, service_rpc: true});
  await db.query('set role anon');
  await failed(() => reserve('openai','planner',`planner:${randomUUID()}`), /permission denied/);
  await db.query('reset role');

  // Shared OpenAI cap: one planner plus four extraction reservations, then stop.
  const plannerKey = `planner:${randomUUID()}`;
  const plannerId = await reserve('openai', 'planner', plannerKey);
  assert.equal(await reserve('openai', 'planner', plannerKey), null);
  await failed(() => reserve('openai', 'planner', `planner:${randomUUID()}`), /planner budget exhausted/);
  assert.equal((await db.query('select public.agent_dispatch_provider_budget($1) as ok', [plannerId])).rows[0].ok, true);
  assert.equal((await db.query('select public.agent_dispatch_provider_budget($1) as ok', [plannerId])).rows[0].ok, false);
  const planId = (await db.query(`select public.agent_start_ai_plan(
    (clock_timestamp() at time zone 'Asia/Seoul')::date,$1,'gpt-5.4-nano',$2) as id`,
    [plannerId, 'a'.repeat(64)])).rows[0].id;
  assert.equal((await db.query('select public.agent_finish_provider_budget($1,$2,$3) as ok',
    [plannerId,'SUCCEEDED',null])).rows[0].ok, true);
  const queries = [{query:'Korea physical AI expo registration',category:'exhibition',
    language:'en',priority:1,reason:'Upcoming industrial AI exhibitions',new_category:null},
    {query:'Korean machine learning education registration',category:'education',
    language:'en',priority:2,reason:'Open education opportunities',new_category:null}];
  assert.equal((await db.query('select public.agent_finish_ai_plan($1,$2,$3,$4) as ok',
    [planId,JSON.stringify(queries),null,JSON.stringify(['CATEGORY_DIVERSITY'])])).rows[0].ok, true);
  assert.equal((await db.query('select count(*)::integer as n from public.agent_claim_ai_plan_query($1)',
    [randomUUID()])).rows[0].n, 0);
  await db.query(`update public.agent_discovery_ai_plans
    set completed_at=clock_timestamp()-interval '31 minutes' where id=$1`,[planId]);
  const claims = await Promise.all([
    db.query('select id from public.agent_claim_ai_plan_query($1)', [randomUUID()]),
    second.query('select id from public.agent_claim_ai_plan_query($1)', [randomUUID()]),
  ]);
  assert.equal(new Set(claims.flatMap(result => result.rows.map(row => row.id))).size, 2);
  const extractionIds = [];
  for (let i=0; i<4; i++) extractionIds.push(
    await reserve('openai','extraction',`extraction:${randomUUID()}`));
  await db.query(`update public.agent_provider_budget_reservations
    set reserved_at=clock_timestamp()-interval '61 seconds' where id=$1`,[extractionIds[0]]);
  assert.equal((await db.query('select public.agent_dispatch_provider_budget($1) as ok',
    [extractionIds[0]])).rows[0].ok, false);
  await failed(() => reserve('openai','extraction',`extraction:${randomUUID()}`),
    /openai shared budget exhausted/);
  const llmUsage = (await db.query('select public.agent_provider_budget_usage($1) as usage',['openai'])).rows[0].usage;
  assert.equal(llmUsage.daily, 5);

  // Rolled-back RPC leaves no reservation; same key can be used once later.
  const rollbackKey = `discovery:${randomUUID()}`;
  await db.query('begin');
  await reserve('tavily','discovery',rollbackKey,'Korea AI public symposium');
  await db.query('rollback');
  const tavilyId = await reserve('tavily','discovery',rollbackKey,'Korea AI public symposium');
  assert(tavilyId);
  await failed(() => reserve('tavily','discovery',`discovery:${randomUUID()}`,
    'Korea AI public symposium'), /query cooldown active/);
  assert.equal(await reserve('tavily','discovery',rollbackKey,'Korea AI public symposium'), null);

  // Legacy usage remains counted without a backfill, including prior failures.
  // A KST yesterday attempt counts for the month but not today's daily cap.
  await db.query(`insert into public.agent_discovery_queries
    (query_text,provider,status,started_at,created_at)
    values ('KST previous day','tavily','FAILED',
      (date_trunc('day',clock_timestamp() at time zone 'Asia/Seoul') at time zone 'Asia/Seoul')
        - interval '1 second',
      (date_trunc('day',clock_timestamp() at time zone 'Asia/Seoul') at time zone 'Asia/Seoul')
        - interval '1 second')`);
  assert.equal((await db.query('select public.agent_provider_budget_usage($1) as usage',['tavily'])).rows[0].usage.daily,1);
  for (let i=0; i<28; i++) await db.query(`insert into public.agent_discovery_queries
    (query_text,provider,status,started_at) values ($1,'tavily','FAILED',clock_timestamp())`,
    [`legacy search ${i}`]);
  const outcomes = await Promise.allSettled([
    reserve('tavily','discovery',`discovery:${randomUUID()}`,'Korea AI hackathon call',db),
    reserve('tavily','discovery',`discovery:${randomUUID()}`,'Korea AI research call',second),
  ]);
  assert.equal(outcomes.filter(row => row.status==='fulfilled').length, 1);
  assert.equal(outcomes.filter(row => row.status==='rejected').length, 1);
  assert.equal((await db.query('select public.agent_provider_budget_usage($1) as usage',['tavily'])).rows[0].usage.daily,30);
  await failed(() => reserve('tavily','discovery',`discovery:${randomUUID()}`,'another call'),
    /tavily budget exhausted/);
  assert.equal((await db.query('select public.agent_dispatch_provider_budget($1) as ok',
    [tavilyId])).rows[0].ok, true);
  assert.equal((await db.query('select public.agent_finish_provider_budget($1,$2,$3) as ok',
    [tavilyId,'UNKNOWN','TIMEOUT'])).rows[0].ok, true);
  assert.equal((await db.query('select public.agent_finish_provider_budget($1,$2,$3) as ok',
    [tavilyId,'UNKNOWN','TIMEOUT'])).rows[0].ok, true);
  assert.equal((await db.query('select public.agent_finish_provider_budget($1,$2,$3) as ok',
    [tavilyId,'SUCCEEDED',null])).rows[0].ok, false);

  // Month caps are independent of the KST day caps. All fixtures remain in
  // this isolated database; no production history is mutated.
  await db.query(`update public.agent_provider_budget_reservations
    set reserved_at=reserved_at-interval '1 day' where kind='openai'`);
  await db.query(`insert into public.agent_logs(level,event_type,created_at)
    select 'INFO','EXTRACTION_LLM_ATTEMPT',
      clock_timestamp()-interval '1 day' from generate_series(1,95)`);
  await failed(() => reserve('openai','extraction',`extraction:${randomUUID()}`),
    /openai shared budget exhausted/);
  await db.query(`update public.agent_provider_budget_reservations
    set reserved_at=reserved_at-interval '1 day' where kind='tavily'`);
  await db.query(`update public.agent_discovery_queries
    set started_at=started_at-interval '1 day',created_at=created_at-interval '1 day'
    where provider='tavily' and query_text like 'legacy search %'`);
  await db.query(`insert into public.agent_discovery_queries
    (query_text,provider,status,created_at,started_at)
    select 'monthly legacy '||g,'tavily','FAILED',
      clock_timestamp()-interval '1 day',clock_timestamp()-interval '1 day'
      from generate_series(1,969) g`);
  await failed(() => reserve('tavily','discovery',`discovery:${randomUUID()}`,
    'Korean AI training'), /tavily budget exhausted/);

  const all = (await db.query('select id,kind,scope from public.agent_provider_budget_reservations')).rows;
  assert.equal(all.filter(row => row.kind==='tavily').length,2);
  console.log('PASS 001-011 migration, RLS/RPC, shared budget, plan audit, concurrent claim/reservation, legacy usage');
} finally {
  if (second) await second.end();
  if (db) await db.end();
  await epg.stop();
  rmSync(scratch, {recursive: true, force: true});
}
