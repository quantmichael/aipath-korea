// Isolated PHASE 12A schema test. Never reads production configuration.
import {readFileSync, rmSync} from 'node:fs';
import {randomUUID} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import EmbeddedPostgres from 'embedded-postgres';

const root = process.env.AIPATH_PHASE12A_ROOT || resolve(fileURLToPath(new URL('../..', import.meta.url)));
const scratch = join(tmpdir(), `aipath-phase12a-${randomUUID()}`);
const epg = new EmbeddedPostgres({
  databaseDir: scratch, user: 'postgres', password: 'isolated-test-only',
  port: 56000 + Math.floor(Math.random() * 1000), persistent: false,
});
const migrations = [
  '001_agent_foundation', '002_collection_foundation',
  '003_discovery_foundation', '004_source_review_foundation',
  '005_collection_scheduling', '006_processing_foundation',
  '007_publish_automation', '008_discovery_scheduling',
  '009_discovery_coverage',
];
let db;
try {
  await epg.initialise();
  await epg.start();
  db = epg.getPgClient();
  await db.connect();
  await db.query('create role anon; create role authenticated; create role service_role bypassrls');
  await db.query(readFileSync(`${root}/supabase/schema.sql`, 'utf8'));
  for (const migration of migrations) {
    await db.query(readFileSync(`${root}/agent_collect/migrations/${migration}.sql`, 'utf8'));
  }
  const before = (await db.query(
    'select id,query_text,enabled from public.agent_discovery_schedules order by id')).rows;
  await db.query(readFileSync(
    `${root}/agent_collect/migrations/010_discovery_exhibition_program.sql`, 'utf8'));
  const after = (await db.query(
    'select id,query_text,enabled from public.agent_discovery_schedules order by id')).rows;
  assert.deepEqual(after, before);
  const inserted = await db.query(`
    insert into public.agent_discovery_schedules(query_text,vertical,language,country)
    values ('2026 AI exhibition Korea registration','exhibition','en','KR'),
           ('AI accelerator Korea official application','program','en','KR')
    returning vertical,enabled`);
  assert.deepEqual(inserted.rows.map(row => row.vertical), ['exhibition', 'program']);
  assert(inserted.rows.every(row => row.enabled === false));
  await db.query(`
    insert into public.agent_discovery_queries(query_text,provider,vertical_hint)
    values ('exhibition fixture','tavily','exhibition'),
           ('program fixture','tavily','program')`);
  const checks = (await db.query(`
    select conname from pg_constraint where conname in
    ('agent_discovery_schedules_vertical_check',
     'agent_discovery_queries_vertical_hint_check')`)).rows;
  assert.equal(checks.length, 2);
  const rls = (await db.query(`
    select relname,relrowsecurity from pg_class where relname in
    ('agent_discovery_schedules','agent_discovery_queries')`)).rows;
  assert(rls.every(row => row.relrowsecurity));
  const anon = (await db.query(`
    select has_table_privilege('anon','public.agent_discovery_schedules','SELECT') as schedules,
           has_table_privilege('anon','public.agent_discovery_queries','SELECT') as queries`)).rows[0];
  assert.equal(anon.schedules, false);
  assert.equal(anon.queries, false);
  console.log('PASS migrations 001-010, existing schedules unchanged, disabled new categories, constraints, RLS');
} finally {
  if (db) await db.end();
  await epg.stop();
  rmSync(scratch, {recursive: true, force: true});
}
