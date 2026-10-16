// Exact Plan Claim integration test. Runs only in disposable embedded PostgreSQL.
import {readFileSync, rmSync} from 'node:fs';
import {randomUUID} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import EmbeddedPostgres from 'embedded-postgres';

const root = process.env.AIPATH_PHASE12E1_ROOT || resolve(fileURLToPath(new URL('../..', import.meta.url)));
const scratch = join(tmpdir(), `aipath-phase12e1-${randomUUID()}`);
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

async function createPlan(day, queryCount=2) {
  const reservation = (await db.query(`insert into public.agent_provider_budget_reservations
    (kind,scope,idempotency_key,status,dispatched_at,completed_at)
    values ('openai','planner',$1,'SUCCEEDED',clock_timestamp(),clock_timestamp())
    returning id`, [`test-planner:${randomUUID()}`])).rows[0].id;
  const plan = (await db.query(`insert into public.agent_discovery_ai_plans
    (planning_date,reservation_id,status,model,context_digest,completed_at)
    values ($1,$2,'READY','test-model',$3,clock_timestamp()-interval '31 minutes')
    returning id`, [day,reservation,'a'.repeat(64)])).rows[0].id;
  const ids = [];
  for (let i=1; i<=queryCount; i++) {
    ids.push((await db.query(`insert into public.agent_discovery_ai_plan_queries
      (plan_id,ordinal,query_text,vertical_hint,language,priority,reason)
      values ($1,$2,$3,'competition','ko',$2,'Isolated exact claim test') returning id`,
      [plan,i,`2026 Korea AI test search ${day} ${i}`])).rows[0].id);
  }
  return {plan, ids};
}

async function exact(planId, claimId=randomUUID(), client=db) {
  return (await client.query(`select id,plan_id,claim_id
    from public.agent_claim_ai_plan_query_exact($1,$2)`,[planId,claimId])).rows;
}

try {
  await epg.initialise();
  await epg.start();
  db=epg.getPgClient(); second=epg.getPgClient();
  await db.connect(); await second.connect();
  await db.query('create role anon; create role authenticated; create role service_role bypassrls');
  await db.query(readFileSync(`${root}/supabase/schema.sql`,'utf8'));
  for (const migration of migrations) {
    await db.query(readFileSync(`${root}/agent_collect/migrations/${migration}.sql`,'utf8'));
  }

  // A query claimed by migration 011 can still be finished after 012.
  const legacy=await createPlan('2026-10-10',1);
  const legacyClaim=randomUUID();
  assert.equal((await db.query('select id from public.agent_claim_ai_plan_query($1)',
    [legacyClaim])).rows.length,1);
  await db.query(readFileSync(`${root}/agent_collect/migrations/012_exact_ai_plan_claim.sql`,'utf8'));
  assert.equal((await db.query('select public.agent_finish_ai_plan_query($1,$2,$3) as ok',
    [legacyClaim,'FAILED',null])).rows[0].ok,true);
  assert.equal((await db.query('select active_claim_id from public.agent_discovery_ai_plans where id=$1',
    [legacy.plan])).rows[0].active_claim_id,null);

  const perms=(await db.query(`select
    has_function_privilege('anon','public.agent_claim_ai_plan_query_exact(uuid,uuid)','EXECUTE') as anon_rpc,
    has_function_privilege('authenticated','public.agent_claim_ai_plan_query_exact(uuid,uuid)','EXECUTE') as authenticated_rpc,
    has_function_privilege('service_role','public.agent_claim_ai_plan_query_exact(uuid,uuid)','EXECUTE') as service_rpc,
    has_table_privilege('service_role','public.agent_discovery_ai_plans','UPDATE') as service_update,
    (select relrowsecurity from pg_class where oid='public.agent_discovery_ai_plans'::regclass) as plan_rls`)).rows[0];
  assert.deepEqual(perms,{anon_rpc:false,authenticated_rpc:false,service_rpc:true,
    service_update:false,plan_rls:true});
  await db.query('set role anon');
  await assert.rejects(() => exact(legacy.plan),/permission denied/);
  await db.query('reset role');
  await db.query('set role service_role');
  assert.equal((await exact(randomUUID())).length,0);
  await db.query('reset role');

  const target=await createPlan('2026-10-11');
  const other=await createPlan('2026-10-12',1);
  assert.equal((await exact(randomUUID())).length,0);
  assert.equal((await db.query(`select count(*)::integer as n from public.agent_discovery_ai_plan_queries
    where plan_id in ($1,$2) and status='READY'`,[target.plan,other.plan])).rows[0].n,3);

  // Claim and rollback are one transaction, including the Plan lock marker.
  await db.query('set role service_role');
  await db.query('begin');
  assert.equal((await exact(target.plan)).length,1);
  await db.query('rollback');
  await db.query('reset role');
  assert.equal((await db.query('select active_claim_id from public.agent_discovery_ai_plans where id=$1',
    [target.plan])).rows[0].active_claim_id,null);
  assert.equal((await db.query(`select count(*)::integer as n from public.agent_discovery_ai_plan_queries
    where plan_id=$1 and status='READY'`,[target.plan])).rows[0].n,2);

  // Two independent sessions cannot claim different queries of the same Plan.
  const attempts=await Promise.all([
    exact(target.plan,randomUUID(),db), exact(target.plan,randomUUID(),second),
  ]);
  assert.deepEqual(attempts.map(rows=>rows.length).sort(),[0,1]);
  const first=attempts.flat()[0];
  assert.equal(first.plan_id,target.plan);
  assert.equal((await db.query('select active_claim_id from public.agent_discovery_ai_plans where id=$1',
    [target.plan])).rows[0].active_claim_id,first.claim_id);
  assert.equal((await exact(target.plan)).length,0);
  assert.equal((await db.query(`select count(*)::integer as n from public.agent_discovery_ai_plan_queries
    where plan_id=$1 and status='CLAIMED'`,[target.plan])).rows[0].n,1);
  const unscoped=(await db.query('select plan_id from public.agent_claim_ai_plan_query($1)',
    [randomUUID()])).rows;
  assert.equal(unscoped.length,1);
  assert.equal(unscoped[0].plan_id,other.plan);
  assert.equal((await db.query(`select count(*)::integer as n from public.agent_discovery_ai_plan_queries
    where plan_id=$1 and status='CLAIMED'`,[target.plan])).rows[0].n,1);

  assert.equal((await db.query('select public.agent_finish_ai_plan_query($1,$2,$3) as ok',
    [first.claim_id,'FAILED',null])).rows[0].ok,true);
  const next=(await exact(target.plan))[0];
  assert(next);
  assert.notEqual(next.id,first.id);
  assert.equal((await exact(target.plan)).length,0);
  assert.equal((await db.query('select public.agent_finish_ai_plan_query($1,$2,$3) as ok',
    [next.claim_id,'FAILED',null])).rows[0].ok,true);
  assert.equal((await exact(target.plan)).length,0);

  // The old Cloud Run RPC did not claim a second query from the locked Plan.

  // The Tavily reservation policy remains delegated to migration 011.
  const key=`discovery:${randomUUID()}`;
  const reserved=(await db.query('select public.agent_reserve_provider_budget($1,$2,$3,$4) as id',
    ['tavily','discovery',key,'2026 Korea AI competition'])).rows[0].id;
  assert(reserved);
  assert.equal((await db.query('select public.agent_reserve_provider_budget($1,$2,$3,$4) as id',
    ['tavily','discovery',key,'2026 Korea AI competition'])).rows[0].id,null);
  const usage=(await db.query("select public.agent_provider_budget_usage('tavily') as usage")).rows[0].usage;
  assert.equal(usage.daily,1);
  assert.equal((await db.query('select count(*)::integer as n from public.agent_sources')).rows[0].n,0);
  assert.equal((await db.query('select count(*)::integer as n from public.opportunities')).rows[0].n,0);
  console.log('PASS 001-012 exact claim, concurrent isolation, rollback, legacy RPC, RLS, atomic budget');
} finally {
  if (second) await second.end();
  if (db) await db.end();
  await epg.stop();
  rmSync(scratch,{recursive:true,force:true});
}
