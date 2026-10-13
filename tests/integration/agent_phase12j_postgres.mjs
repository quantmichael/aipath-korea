// Disposable PostgreSQL verification of the existing PHASE 12J FK path.
import {readFileSync, rmSync} from 'node:fs';
import {randomUUID} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import EmbeddedPostgres from 'embedded-postgres';

const root = process.env.AIPATH_PHASE12J_ROOT || resolve(fileURLToPath(new URL('../..', import.meta.url)));
const scratch = join(tmpdir(), `aipath-phase12j-${randomUUID()}`);
const epg = new EmbeddedPostgres({databaseDir:scratch,user:'postgres',password:'isolated-only',
  port:57000+Math.floor(Math.random()*1000),persistent:false});
const migrations = [
  '001_agent_foundation','002_collection_foundation','003_discovery_foundation',
  '004_source_review_foundation','005_collection_scheduling','006_processing_foundation',
  '007_publish_automation','008_discovery_scheduling','009_discovery_coverage',
  '010_discovery_exhibition_program','011_goal_driven_discovery',
  '012_exact_ai_plan_claim','013_exact_processing_seed',
];
let db, peer;
try {
  await epg.initialise();
  await epg.start();
  db=epg.getPgClient();
  await db.connect();
  peer=epg.getPgClient();
  await peer.connect();
  await db.query('create role anon; create role authenticated; create role service_role bypassrls');
  await db.query(readFileSync(`${root}/supabase/schema.sql`,'utf8'));
  for (const name of migrations) {
    if (name === '013_exact_processing_seed') {
      assert.equal((await db.query(`select count(*)::integer as n from pg_proc
        where proname='agent_seed_processing_job_exact'`)).rows[0].n,0);
    }
    await db.query(readFileSync(`${root}/agent_collect/migrations/${name}.sql`,'utf8'));
  }
  const query=randomUUID(), result=randomUUID(), sourceCandidate=randomUUID();
  const source=randomUUID(), run=randomUUID(), raw=randomUUID(), candidate=randomUUID();
  const verification=randomUUID(), queue=randomUUID(), oldRaw=randomUUID();
  const url=`https://official.example.org/notices/${randomUUID()}`;
  const oldHash='b'.repeat(64), hash='a'.repeat(64);
  const oldRef=`supabase:agent-raw-preview:raw/sha256/bb/${oldHash}.raw`;
  const ref=`supabase:agent-raw-preview:raw/sha256/aa/${hash}.raw`;
  await db.query(`insert into public.agent_discovery_queries(id,query_text,provider)
    values ($1,'synthetic official opportunity','fixture')`, [query]);
  await db.query(`insert into public.agent_search_results
    (id,query_id,provider,rank,title,original_url,canonical_url)
    values ($1,$2,'fixture',1,'Synthetic AI call',$3,$3)`, [result,query,url]);
  await db.query(`insert into public.agent_sources
    (id,name,source_url,canonical_url,source_type,provenance)
    values ($1,'Synthetic organizer',$2,$2,'official',$3)`,
    [source,url,JSON.stringify({search_result_id:result,source_candidate_id:sourceCandidate})]);
  await db.query(`insert into public.agent_source_candidates
    (id,query_id,search_result_id,original_url,candidate_url,canonical_url,
     domain,title,status,evaluation_decision,promoted_source_id,promoted_at)
    values ($1,$2,$3,$4,$4,$4,'official.example.org','Synthetic AI call',
            'PROMOTED','ACCEPT',$5,clock_timestamp())`,
    [sourceCandidate,query,result,url,source]);
  await db.query(`insert into public.agent_collection_runs(id,source_id,status,finished_at)
    values ($1,$2,'SUCCEEDED',clock_timestamp())`, [run,source]);
  await db.query(`insert into public.agent_raw_documents
    (id,run_id,source_id,source_url,content_ref,content_sha256,media_type,http_status)
    values ($1,$2,$3,$4,$5,$6,'text/html',200)`,
    [oldRaw,run,source,url,oldRef,oldHash]);
  await db.query(`insert into public.agent_raw_documents
    (id,run_id,source_id,source_url,content_ref,content_sha256,media_type,http_status)
    values ($1,$2,$3,$4,$5,$6,'text/html',200)`,
    [raw,run,source,url,ref,hash]);
  const exactSql=`select public.agent_seed_processing_job_exact($1,$2,$3,$4,$5) as id`;
  const args=[raw,source,run,'isolated-test-v1',ref];
  assert.equal((await db.query(exactSql,[raw,randomUUID(),run,args[3],ref])).rows[0].id,null);
  assert.equal((await db.query(exactSql,[raw,source,randomUUID(),args[3],ref])).rows[0].id,null);
  assert.equal((await db.query(exactSql,[raw,source,run,args[3],oldRef])).rows[0].id,null);
  const [first,second]=await Promise.all([db.query(exactSql,args),peer.query(exactSql,args)]);
  const exact=first.rows[0].id;
  assert(exact);
  assert.equal(second.rows[0].id,exact);
  assert.equal((await db.query(exactSql,args)).rows[0].id,exact);
  assert.equal((await db.query(exactSql,[raw,randomUUID(),run,args[3],ref])).rows[0].id,null);
  assert.equal((await db.query(exactSql,[raw,source,run,args[3],oldRef])).rows[0].id,null);
  assert.equal((await db.query(`select count(*)::integer as n from public.agent_processing_jobs
    where raw_document_id=$1 and processing_version=$2`,[raw,args[3]])).rows[0].n,1);
  assert.equal((await db.query(`select count(*)::integer as n from public.agent_processing_jobs
    where raw_document_id=$1`,[oldRaw])).rows[0].n,0);
  const signature='public.agent_seed_processing_job_exact(uuid,uuid,uuid,text,text)';
  for (const role of ['anon','authenticated']) {
    assert.equal((await db.query('select has_function_privilege($1,$2,\'EXECUTE\') as allowed',
      [role,signature])).rows[0].allowed,false);
  }
  assert.equal((await db.query('select has_function_privilege($1,$2,\'EXECUTE\') as allowed',
    ['service_role',signature])).rows[0].allowed,true);
  await db.query('begin');
  await db.query('set local role service_role');
  assert.equal((await db.query(exactSql,args)).rows[0].id,exact);
  await assert.rejects(db.query(`insert into public.agent_processing_jobs
    (raw_document_id,processing_version) values ($1,$2)`,[oldRaw,args[3]]));
  await db.query('rollback');
  const claimSql=`select id,claim_id from public.agent_claim_processing(
    $1,$2,$3,300,array[15,60],4)`;
  const [a,b]=await Promise.all([
    db.query(claimSql,[exact,'isolated-a',randomUUID()]),
    peer.query(claimSql,[exact,'isolated-b',randomUUID()]),
  ]);
  assert.equal(a.rowCount+b.rowCount,1);
  const claimed=(a.rows[0]||b.rows[0]);
  assert.equal(claimed.id,exact);
  const job=exact;
  await db.query(`insert into public.agent_candidates
    (id,run_id,source_id,raw_document_id,status,provenance)
    values ($1,$2,$3,$4,'EXTRACTED',$5)`,
    [candidate,run,source,raw,JSON.stringify({extraction_status:'EXTRACTED'})]);
  await db.query(`insert into public.agent_verifications
    (id,candidate_id,result,confidence,checks,evidence_refs)
    values ($1,$2,'VERIFIED',0.95,$3,$4)`,
    [verification,candidate,JSON.stringify({gate_decision:{status:'PUBLISH_READY'}}),
     JSON.stringify([`${raw}:B001`])]);
  assert.equal((await db.query(`select public.agent_processing_checkpoint(
    $1,$2,$3,$4,'PUBLISH_READY') as done`,
    [job,claimed.claim_id,candidate,verification])).rows[0].done,true);
  assert.equal((await db.query(`select public.agent_finish_processing(
    $1,$2,'SUCCEEDED',$3,$4,'PUBLISH_READY',null,array[15,60],4) as outcome`,
    [job,claimed.claim_id,candidate,verification])).rows[0].outcome,'SUCCEEDED');
  await db.query(`insert into public.agent_publish_queue
    (id,candidate_id,verification_id,contract_version,publish_candidate)
    values ($1,$2,$3,1,$4)`,
    [queue,candidate,verification,JSON.stringify({agent_item_id:candidate})]);
  const linked=(await db.query(`select count(*)::integer as n
    from public.agent_search_results r
    join public.agent_source_candidates sc on sc.search_result_id=r.id and sc.query_id=r.query_id
    join public.agent_sources s on s.id=sc.promoted_source_id
    join public.agent_collection_runs cr on cr.source_id=s.id
    join public.agent_raw_documents d on d.run_id=cr.id and d.source_id=s.id
    join public.agent_processing_jobs j on j.raw_document_id=d.id
    join public.agent_candidates c on c.id=j.candidate_id and c.raw_document_id=d.id
    join public.agent_verifications v on v.id=j.verification_id and v.candidate_id=c.id
    join public.agent_publish_queue pq on pq.candidate_id=c.id and pq.verification_id=v.id
    where r.id=$1 and j.id=$2`,[result,job])).rows[0].n;
  assert.equal(linked,1);
  await assert.rejects(db.query(`insert into public.agent_publish_queue
    (candidate_id,verification_id,contract_version,publish_candidate)
    values ($1,$2,1,$3)`,[randomUUID(),verification,JSON.stringify({agent_item_id:randomUUID()})]));
  assert.equal((await db.query('select count(*)::integer as n from public.opportunities')).rows[0].n,0);
  await db.query('begin');
  await db.query('set local role anon');
  await assert.rejects(db.query('select id from public.agent_sources limit 1'));
  await db.query('rollback');
  await db.query('begin');
  await db.query('delete from public.agent_publish_queue where id=$1',[queue]);
  await db.query('delete from public.agent_processing_completions where job_id=$1',[job]);
  await db.query('delete from public.agent_processing_jobs where id=$1',[job]);
  await db.query('delete from public.agent_verifications where id=$1',[verification]);
  await db.query('delete from public.agent_candidates where id=$1',[candidate]);
  await db.query('delete from public.agent_raw_documents where id=$1',[raw]);
  assert.equal((await db.query('select count(*)::integer as n from public.agent_raw_documents where id=$1',
    [oldRaw])).rows[0].n,1);
  await db.query('delete from public.agent_raw_documents where id=$1',[oldRaw]);
  await db.query('delete from public.agent_collection_runs where id=$1',[run]);
  await db.query('delete from public.agent_source_candidates where id=$1',[sourceCandidate]);
  await db.query('delete from public.agent_sources where id=$1',[source]);
  await db.query('delete from public.agent_search_results where id=$1',[result]);
  await db.query('delete from public.agent_discovery_queries where id=$1',[query]);
  await db.query('commit');
  for (const table of ['agent_processing_jobs','agent_processing_completions',
    'agent_candidates','agent_verifications','agent_raw_documents','agent_sources',
    'agent_collection_runs','agent_source_candidates','agent_search_results',
    'agent_discovery_queries']) {
    assert.equal((await db.query(`select count(*)::integer as n from public.${table}`)).rows[0].n,0);
  }
  console.log('PASS isolated 001-013 exact seed concurrency/idempotence, claim, FK path, RPC/RLS, exact cleanup, Service zero rows');
} finally {
  if (peer) await peer.end();
  if (db) await db.end();
  await epg.stop();
  rmSync(scratch,{recursive:true,force:true});
}
