// Isolated Agent DB integration test. Spins up and deletes a local PostgreSQL
// cluster; never reads a production connection string or calls external APIs.
import {readFileSync, mkdirSync, rmSync, writeFileSync} from 'node:fs';
import {createHash, randomUUID} from 'node:crypto';
import assert from 'node:assert/strict';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import EmbeddedPostgres from 'embedded-postgres';
const root=resolve(fileURLToPath(new URL('../..',import.meta.url)));
const scratch=join(tmpdir(),`aipath-phase11c-${randomUUID()}`);
const epg=new EmbeddedPostgres({databaseDir:join(scratch,'data'),user:'postgres',password:'isolated-test-only',port:56000+Math.floor(Math.random()*1000),persistent:false});
const migrations=['001_agent_foundation','002_collection_foundation','003_discovery_foundation','004_source_review_foundation','005_collection_scheduling','006_processing_foundation','007_publish_automation','008_discovery_scheduling','009_discovery_coverage'];
let db;const extras=[];
const scope='phase11c-offline-fixture', reviewer='AIPATH_AUTO_APPROVAL_V1';
const termsHash='a'.repeat(64), officialHash='b'.repeat(64);
async function q(sql,params=[],client=db){return client.query(sql,params)}
async function role(client,name){await client.query('set role '+name)}
async function failure(fn,code){try{await fn();assert.fail('expected failure')}catch(e){if(e.name==='AssertionError')throw e;if(code)assert.equal(e.code,code);return e}}
async function source(name){const url=`https://official.example.org/calls/${name}`;const provenance={auto_approval:{eligible:true,scope_id:scope,candidate_url:url,terms_sha256:termsHash,officiality_sha256:officialHash,reviewed_by:'OFFLINE_HUMAN_REVIEWER'},discovery_officiality_signals:['official_source_signal']};const r=await q(`insert into public.agent_sources(name,source_url,canonical_url,source_type,collection_method,confidence,provenance) values($1,$2,$2,'official','http_html',0.95,$3::jsonb) returning id,status`,[name,url,JSON.stringify(provenance)]);return {id:r.rows[0].id,url}}
async function review(s,expected,target,decision,id=randomUUID(),allowed=null,official=null,client=db){return q(`select (public.agent_review_source($1::uuid,$2,$3,$4,$5,$6,$7::uuid,null,$8,$9)).*`,[s.id,expected,target,reviewer,`auto policy scope=${scope}; officiality sha256=${officialHash}; terms sha256=${termsHash}; private collection only`,decision,id,official,allowed],client)}
async function eventCount(s){return Number((await q('select count(*)::int as n from public.agent_source_review_events where source_id=$1',[s.id])).rows[0].n)}
try{
 await epg.initialise();await epg.start();db=epg.getPgClient();await db.connect();
 await q('create role anon; create role authenticated; create role service_role bypassrls;');
 await q(readFileSync(root+'/supabase/schema.sql','utf8'));
 const serviceColumns=async()=> (await q(`select table_name,column_name,data_type from information_schema.columns where table_schema='public' and table_name not like 'agent_%' order by table_name,ordinal_position`)).rows;
 const before=await serviceColumns();
 for(const m of migrations){await q(readFileSync(root+'/agent_collect/migrations/'+m+'.sql','utf8'));console.log('PASS migration',m.slice(0,3))}
 assert.deepEqual(await serviceColumns(),before);console.log('PASS service schema',before.length,'columns unchanged');
 const catalog=await q(`select c.relname,c.relrowsecurity from pg_class c where c.relnamespace='public'::regnamespace and c.relkind='r' and c.relname like 'agent_%'`);
 assert.equal(catalog.rows.filter(r=>!r.relrowsecurity).length,0);
 const requiredIndexes=['agent_sources_canonical_url_key','agent_source_candidates_canonical_url_key','agent_discovery_schedule_identity_unique','agent_runs_scheduled_window_unique','agent_discovery_query_window_unique'];
 const indexes=(await q(`select indexname from pg_indexes where schemaname='public'`)).rows.map(r=>r.indexname);
 for(const name of requiredIndexes)assert(indexes.includes(name),name);
 const requiredTriggers=['agent_source_review_guard_before_write','agent_schedule_after_activation','agent_discovery_query_guard_trigger','agent_scheduled_run_claim_before_insert'];
 const triggers=(await q(`select tgname from pg_trigger where not tgisinternal`)).rows.map(r=>r.tgname);
 for(const name of requiredTriggers)assert(triggers.includes(name),name);
 const requiredRpc=['agent_review_source','agent_claim_due_source','agent_claim_discovery','agent_register_discovery_schedule_vertical'];
 const rpc=(await q(`select proname,prosecdef from pg_proc where pronamespace='public'::regnamespace`)).rows;
 for(const name of requiredRpc)assert(rpc.some(r=>r.proname===name&&r.prosecdef),name);
 console.log('PASS all Agent tables RLS, critical indexes/triggers/security-definer RPCs');
 await q(readFileSync(root+'/tests/sql/agent_discovery_coverage_integration.sql','utf8'));
 console.log('PASS Discovery schedule RPC, trigger, RLS rollback test');
 await role(db,'service_role');
 const a=await source('ai-conference-a');
 await failure(()=>source('ai-conference-a'),'23505');
 console.log('PASS Source canonical URL unique constraint');
 const qr=await q(`insert into public.agent_discovery_queries(query_text,provider,vertical_hint,status,started_at,completed_at,result_count,candidate_count,promoted_count) values('AI conference official','offline_fixture','conference','SUCCEEDED',now(),now(),1,1,1) returning id`);const queryId=qr.rows[0].id;
 const sr=await q(`insert into public.agent_search_results(query_id,provider,rank,title,original_url,canonical_url,domain,snippet,status) values($1,'offline_fixture',1,'AI Conference',$2,$2,'official.example.org','AI conference call','CANDIDATE') returning id`,[queryId,a.url]);
 const ca=await q(`insert into public.agent_source_candidates(query_id,search_result_id,original_url,candidate_url,canonical_url,domain,title,snippet,status,evaluation_decision,confidence,reasons,evidence,collection_method,promoted_source_id,promoted_at,provenance) values($1,$2,$3,$3,$3,'official.example.org','AI Conference','AI conference call','PROMOTED','ACCEPT',0.95,'["ai_related","vertical_related","auto_scope_approved"]','["offline_fixture"]','http_html',$4,now(),$5::jsonb) returning id`,[queryId,sr.rows[0].id,a.url,a.id,JSON.stringify({discovery_query_id:queryId,auto_approval:{eligible:true,scope_id:scope}})]);
 await q(`update public.agent_sources set provenance=provenance || jsonb_build_object('discovery_query_id',$2::text,'source_candidate_id',$3::text) where id=$1`,[a.id,queryId,ca.rows[0].id]);
 assert.equal(ca.rowCount,1);console.log('PASS Discovery Query → Result → Candidate → Source FK');
 const beginId=randomUUID();await review(a,'CANDIDATE','REVIEW','REVIEW_STARTED',beginId);
 await review(a,'REVIEW','APPROVED','APPROVED',randomUUID(),true,true);
 const activationId=randomUUID();await review(a,'APPROVED','ACTIVE','ACTIVATED',activationId);
 let rows=await q(`select s.status,s.is_active,s.provenance #>> '{source_review,approval,reviewer}' as approved_by, count(sc.source_id)::int as schedules from public.agent_sources s left join public.agent_source_schedules sc on sc.source_id=s.id where s.id=$1 group by s.id`,[a.id]);assert.deepEqual({status:rows.rows[0].status,active:rows.rows[0].is_active,reviewer:rows.rows[0].approved_by,schedules:rows.rows[0].schedules},{status:'ACTIVE',active:true,reviewer,schedules:1});assert.equal(await eventCount(a),3);
 const events=(await q('select from_status,review_status,review_decision,reviewer from public.agent_source_review_events where source_id=$1 order by reviewed_at,id',[a.id])).rows;assert.equal(new Set(events.map(e=>e.reviewer)).size,1);assert.deepEqual(new Set(events.map(e=>e.review_decision)),new Set(['REVIEW_STARTED','APPROVED','ACTIVATED']));console.log('PASS audited machine approval, ACTIVE, one schedule');
 await q(`insert into public.agent_logs(level,event_type,message,details) values('INFO','DISCOVERY_WORKER_RUN','offline fixture',jsonb_build_object('query_ids',jsonb_build_array($1::text),'promoted_source_ids',jsonb_build_array($2::text),'auto_approved_count',1,'collection_scheduled_count',1))`,[queryId,a.id]);
 const trace=await q(`select count(*)::int as n from public.agent_logs l join public.agent_discovery_queries q on (l.details->'query_ids') ? q.id::text join public.agent_sources s on s.provenance->>'discovery_query_id'=q.id::text join public.agent_source_review_events e on e.source_id=s.id where l.event_type='DISCOVERY_WORKER_RUN' and s.id=$1`,[a.id]);
 assert.equal(trace.rows[0].n,3);console.log('PASS Discovery log Query ID links Source and three approval audit events');
 await review(a,'APPROVED','ACTIVE','ACTIVATED',activationId);assert.equal(await eventCount(a),3);
 await failure(()=>review(a,'CANDIDATE','REVIEW','REVIEW_STARTED',randomUUID()),'P0001');assert.equal(await eventCount(a),3);console.log('PASS idempotent decision ID and duplicate approval rejection');
 const b=await source('ai-conference-b');
 const c1=epg.getPgClient(),c2=epg.getPgClient();extras.push(c1,c2);await c1.connect();await c2.connect();await role(c1,'service_role');await role(c2,'service_role');
 const concurrent=await Promise.allSettled([review(b,'CANDIDATE','REVIEW','REVIEW_STARTED',randomUUID(),null,null,c1),review(b,'CANDIDATE','REVIEW','REVIEW_STARTED',randomUUID(),null,null,c2)]);assert.equal(concurrent.filter(x=>x.status==='fulfilled').length,1);assert.equal(concurrent.filter(x=>x.status==='rejected').length,1);assert.equal(await eventCount(b),1);rows=await q('select status from public.agent_sources where id=$1',[b.id]);assert.equal(rows.rows[0].status,'REVIEW');console.log('PASS two-session concurrent approval serializes to one event');
 const c=await source('ai-conference-c');await review(c,'CANDIDATE','REVIEW','REVIEW_STARTED');await failure(()=>review(c,'REVIEW','APPROVED','APPROVED',randomUUID(),false,true),'P0001');assert.equal(await eventCount(c),1);rows=await q('select status from public.agent_sources where id=$1',[c.id]);assert.equal(rows.rows[0].status,'REVIEW');await review(c,'REVIEW','HOLD','HELD');rows=await q('select status from public.agent_sources where id=$1',[c.id]);assert.equal(rows.rows[0].status,'HOLD');assert.equal((await q('select count(*)::int as n from public.agent_source_schedules where source_id=$1',[c.id])).rows[0].n,0);console.log('PASS failed approval RPC leaves REVIEW and explicit HOLD recovery');
 const d=await source('ai-conference-d');await q('begin');await review(d,'CANDIDATE','REVIEW','REVIEW_STARTED');await review(d,'REVIEW','APPROVED','APPROVED',randomUUID(),true,true);await review(d,'APPROVED','ACTIVE','ACTIVATED');await q('rollback');rows=await q('select status from public.agent_sources where id=$1',[d.id]);assert.equal(rows.rows[0].status,'CANDIDATE');assert.equal(await eventCount(d),0);assert.equal((await q('select count(*)::int as n from public.agent_source_schedules where source_id=$1',[d.id])).rows[0].n,0);console.log('PASS transaction rollback restores Source, audit, schedule');
 const e=await source('ai-conference-e');await review(e,'CANDIDATE','REVIEW','REVIEW_STARTED');await review(e,'REVIEW','APPROVED','APPROVED',randomUUID(),true,true);
 await q('reset role');await q(`create function public.phase11c_fail_activation() returns trigger language plpgsql as $$ begin raise exception 'isolated activation fault'; end $$; create trigger phase11c_fail_activation after update of status on public.agent_sources for each row when (new.status='ACTIVE') execute function public.phase11c_fail_activation();`);await role(db,'service_role');
 await failure(()=>review(e,'APPROVED','ACTIVE','ACTIVATED'),'P0001');rows=await q('select status from public.agent_sources where id=$1',[e.id]);assert.equal(rows.rows[0].status,'APPROVED');assert.equal(await eventCount(e),2);assert.equal((await q('select count(*)::int as n from public.agent_source_schedules where source_id=$1',[e.id])).rows[0].n,0);
 await q('reset role');await q('drop trigger phase11c_fail_activation on public.agent_sources; drop function public.phase11c_fail_activation()');await role(db,'service_role');await review(e,'APPROVED','ACTIVE','ACTIVATED');assert.equal((await q('select count(*)::int as n from public.agent_source_schedules where source_id=$1',[e.id])).rows[0].n,1);console.log('PASS activation trigger fault rolls back audit/schedule and retry succeeds');
 await failure(()=>q('update public.agent_sources set status=\'ACTIVE\',is_active=true where id=$1',[d.id]),'P0001');console.log('PASS service_role direct lifecycle mutation rejected');
 const due=await q('select * from public.agent_list_due_sources(10)');assert(due.rows.some(r=>r.source_id===a.id));assert(!due.rows.some(r=>r.source_id===c.id));
 const claimId=randomUUID();let claim=await q('select * from public.agent_claim_due_source($1::uuid,$2,$3::uuid,120,1,array[30,120,360,1440],4)',[a.id,'PHASE11C_TEST',claimId]);assert.equal(claim.rowCount,1);const window=claim.rows[0].window_id;let duplicate=await q('select * from public.agent_claim_due_source($1::uuid,$2,$3::uuid,120,1,array[30,120,360,1440],4)',[a.id,'PHASE11C_TEST',randomUUID()]);assert.equal(duplicate.rowCount,0);
 const snapshot=JSON.stringify({trigger:'scheduled',worker_id:'PHASE11C_TEST',claim_id:claimId,schedule_window_id:window});const run=await q(`insert into public.agent_collection_runs(source_id,status,collection_method,source_config_snapshot) values($1,'RUNNING','http_html',$2::jsonb) returning id`,[a.id,snapshot]);const runId=run.rows[0].id;
 await failure(()=>q(`insert into public.agent_collection_runs(source_id,status,collection_method,source_config_snapshot) values($1,'RUNNING','http_html',$2::jsonb)`,[a.id,snapshot]),'23505');
 const raw=Buffer.from('<html><title>AI Conference</title><body>Official call</body></html>');const hash=createHash('sha256').update(raw).digest('hex');const rawDir=join(scratch,'raw');mkdirSync(rawDir,{recursive:true});const rawPath=join(rawDir,hash+'.raw');writeFileSync(rawPath,raw);
 const doc=await q(`insert into public.agent_raw_documents(run_id,source_id,source_url,canonical_url,media_type,content_ref,content_sha256,http_status,provenance) values($1,$2,$3,$3,'text/html',$4,$5,200,$6::jsonb) returning id`,[runId,a.id,a.url,rawPath,hash,JSON.stringify({fixture:'mock_http',discovery_query_id:queryId})]);assert.equal(doc.rowCount,1);
 await q(`update public.agent_collection_runs set status='SUCCEEDED',found_count=1,stored_count=1,finished_at=clock_timestamp() where id=$1`,[runId]);const finish=await q(`select public.agent_finish_collection($1::uuid,$2::uuid,$3::uuid,array[30,120,360,1440],4) as outcome`,[a.id,claimId,runId]);assert.equal(finish.rows[0].outcome,'SUCCEEDED');const again=await q(`select public.agent_finish_collection($1::uuid,$2::uuid,$3::uuid,array[30,120,360,1440],4) as outcome`,[a.id,claimId,runId]);assert.equal(again.rows[0].outcome,'SUCCEEDED');assert.equal((await q('select count(*)::int as n from public.agent_raw_documents where run_id=$1',[runId])).rows[0].n,1);console.log('PASS scheduled collection claim, mock bytes and RawDocument, duplicate window denial, idempotent finish');
 await review(a,'ACTIVE','HOLD','HELD');rows=await q('select * from public.agent_list_due_sources(10)');assert(!rows.rows.some(r=>r.source_id===a.id));claim=await q('select * from public.agent_claim_due_source($1::uuid,$2,$3::uuid,120,1,array[30,120,360,1440],4)',[a.id,'PHASE11C_TEST',randomUUID()]);assert.equal(claim.rowCount,0);console.log('PASS HOLD blocks later scheduled collection');
 await q('reset role');for(const name of ['anon','authenticated']){await role(db,name);await failure(()=>q('select * from public.agent_source_review_events'),'42501');await failure(()=>q('select * from public.agent_sources'),'42501');await failure(()=>q(`select public.agent_review_source($1::uuid,'CANDIDATE','REVIEW','x','x','REVIEW_STARTED',$2::uuid,null,null,null)`,[d.id,randomUUID()]),'42501');await q('reset role')}console.log('PASS anon/authenticated table and Source RPC denial');
 await role(db,'service_role');await failure(()=>q(`insert into public.agent_source_review_events(id,source_id,from_status,review_status,review_decision,reviewer,review_reason,reviewed_at,approval_decision,activation_decision,canonical_url,normalized_domain,source_type_before,source_type_after) values($1,$2,'CANDIDATE','REVIEW','REVIEW_STARTED','forged','forged',now(),'UNCHANGED','NOT_ACTIVATED',$3,'official.example.org','official','official')`,[randomUUID(),d.id,d.url]),'42501');console.log('PASS service_role cannot forge Source audit events');
}finally{for(const client of extras){try{await client.end()}catch{}}if(db){try{await db.end()}catch{}}await epg.stop();rmSync(scratch,{recursive:true,force:true})}
