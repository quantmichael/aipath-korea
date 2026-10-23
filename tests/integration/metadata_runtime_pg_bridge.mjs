// Test-only SQL transport. Creates a disposable local DB; never accepts a DSN.
import {readFileSync, rmSync} from 'node:fs';
import {randomUUID,createHash} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {pathToFileURL, fileURLToPath} from 'node:url';
import {createInterface} from 'node:readline';
const modulePath = process.env.AIPATH_TEST_EMBEDDED_PG;
if (!modulePath) throw new Error('AIPATH_TEST_EMBEDDED_PG required');
const {default: EmbeddedPostgres} = await import(pathToFileURL(resolve(modulePath)).href);
const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const emit = value => process.stdout.write(JSON.stringify(value)+'\n');
console.log = (...items) => console.error(...items);
const databaseDir=join(tmpdir(),'aipath-runtime-'+randomUUID());
const epg = new EmbeddedPostgres({databaseDir,
  user:'postgres',password:'isolated-only',port:58500+Math.floor(Math.random()*400),persistent:false,postgresFlags:['-h','127.0.0.1'],onLog:message=>{if(message.includes('FATAL:'))console.error(message);},onError:message=>console.error(message)});
let db;
const fixtures = new Map();
const ident = value => {
  if (!/^[a-z_][a-z_0-9]*$/.test(value)) throw new Error('invalid test identifier');
  return '"'+value+'"';
};
const scalar = new Set(['agent_acquire_automation_lease','agent_release_automation_lease',
  'agent_metadata_create_candidate','agent_metadata_record_assessment']);
const rpcAllowed = new Set([...scalar,'agent_list_due_publish','agent_publish_metadata_candidate']);
const fixture = async args => {
  const source=randomUUID(), reviewer=randomUUID(), url='https://fixture.example.org/notices/'+source;
  await db.query('reset role');
  await db.query('insert into public.agent_sources(id,name,source_url,canonical_url,source_type,collection_method,confidence,provenance) '+
    "values($1,$2,$3,$3,'official','html',0.99,$4)", [source,args.organizer,url,{isolated_runtime_test:true}]);
  for (const [from,to,event] of [['CANDIDATE','REVIEW','REVIEW_STARTED'],['REVIEW','APPROVED','APPROVED'],['APPROVED','ACTIVE','ACTIVATED']])
    await db.query('select public.agent_review_source($1,$2,$3,$4,$5,$6,$7,null,$8,$9)',
      [source,from,to,'isolated fixture reviewer','local test only',event,randomUUID(),
       to==='APPROVED'?true:null,to==='APPROVED'?true:null]);
  await db.query('insert into public.sources(name,homepage_url) values($1,$2)', [args.organizer,url]);
  await db.query('insert into public.categories(name,slug) values($1,$2) on conflict(slug) do nothing', [args.category,args.category]);
  await db.query('insert into public.agent_metadata_reviewers(user_id,active,source_id,granted_by) values($1,true,$2,$3)',
    [reviewer,source,'isolated fixture only']);
  await db.query("select set_config('request.jwt.claim.sub',$1,false)",[reviewer]);
  const policy = {official_url:url, permission_url:'https://fixture.example.org/policy',
    permission_digest:args.permission_digest,expires_at:new Date(Date.now()+3600000).toISOString(),
    organizer_relationship:'isolated test organizer',approved_organizer:args.organizer,
    page_type:'INDIVIDUAL_NOTICE',scope_origin:'https://fixture.example.org',path_prefix:'/notices/',
    officiality_confirmed:true,public_read:true,automated_access:true,fact_use:true,recheck_allowed:true,
    raw_storage_allowed:false,redistribution_allowed:false,
    ...(args.usage_rights?{usage_rights:args.usage_rights}:{})};
  await db.query('set role authenticated');
  const approval = (await db.query('select public.agent_metadata_approve($1,$2,$3,$4) as id',
    [randomUUID(),source,policy,'isolated synthetic permission'])).rows[0].id;
  await db.query('reset role');
  await db.query('update public.agent_metadata_reviewers set active=false where user_id=$1',[reviewer]);
  fixtures.set(source,{reviewer,approval,url});
  return {source,approval,url,policy};
};
const execute = async q => {
  if (q.kind==='fixture') return {data:await fixture(q.args)};
  if(q.kind==='discovery') {
    const f=fixtures.get(q.source); if(!f) throw new Error('unknown fixture');
    await db.query('set role service_role');
    const query=randomUUID();
    await db.query("insert into public.agent_discovery_queries(id,query_text,provider,status,result_count,started_at,completed_at) values($1,'isolated runtime','synthetic','SUCCEEDED',1,now(),now())",[query]);
    await db.query("insert into public.agent_search_results(query_id,provider,rank,title,original_url,canonical_url) values($1,'synthetic',1,$2,$3,$3)",[query,q.title,f.url]);
    return {data:query};
  }
  if (q.kind==='revoke') {
    const f=fixtures.get(q.source); if (!f) throw new Error('unknown fixture');
    await db.query('reset role');
    await db.query('update public.agent_metadata_reviewers set active=true where user_id=$1',[f.reviewer]);
    try {
      await db.query("select set_config('request.jwt.claim.sub',$1,false)",[f.reviewer]);
      await db.query('set role authenticated');
      await db.query('select public.agent_metadata_revoke($1,$2,$3)',[randomUUID(),f.approval,'isolated revoke']);
    } finally {
      await db.query('reset role');
      await db.query('update public.agent_metadata_reviewers set active=false where user_id=$1',[f.reviewer]);
    }
    return {data:true};
  }
  if (q.kind==='completion_fault') {
    await db.query('reset role');
    await db.query("create function public.isolated_completion_failure() returns trigger language plpgsql as $$begin raise exception 'isolated completion failure'; end$$; create trigger isolated_completion_failure before insert on public.agent_metadata_publish_completions for each row execute function public.isolated_completion_failure()");
    return {data:true};
  }
  if (q.kind==='inspect') {
    await db.query('reset role');
    const result={};
    for (const table of ['opportunities','opportunity_status_automation_scope',
      'agent_metadata_publish_completions','agent_metadata_candidates','agent_metadata_approval_events',
      'agent_metadata_candidate_events','agent_metadata_reviewers','agent_runtime_leases','agent_logs'])
      result[table]=(await db.query('select * from public.'+ident(table))).rows;
    return {data:result};
  }
  await db.query('set role service_role');
  if (q.kind==='rpc') {
    if(!rpcAllowed.has(q.name)) throw new Error('RPC outside integration scope');
    const entries=Object.entries(q.args);
    const call='public.'+ident(q.name)+'('+entries.map(([k],i)=>ident(k)+' => $'+(i+1)).join(',')+')';
    const args=entries.map(([,v])=>v);
    if(scalar.has(q.name)) return {data:(await db.query('select '+call+' as value',args)).rows[0].value};
    return {data:(await db.query('select * from '+call,args)).rows};
  }
  if(q.kind!=='table') throw new Error('unsupported test operation');
  const table='public.'+ident(q.name);
  if(q.insert) {
    if(q.name!=='agent_logs') throw new Error('direct write outside log scope');
    const entries=Object.entries(q.insert);
    return {data:(await db.query('insert into '+table+'('+entries.map(([k])=>ident(k)).join(',')+') values ('+
      entries.map((_,i)=>'$'+(i+1)).join(',')+') returning *', entries.map(([,v])=>v))).rows};
  }
  const params=[];
  const where=q.filters.length?' where '+q.filters.map(([op,key,value])=>{
    if(op==='eq'){params.push(value); return ident(key)+'=$'+params.length;}
    if(op==='in'){if(!value.length)return 'false'; return ident(key)+' in ('+value.map(v=>{params.push(v);return '$'+params.length;}).join(',')+')';}
    throw new Error('unsupported test filter');
  }).join(' and '):'';
  const count=Number((await db.query('select count(*) as n from '+table+where,params)).rows[0].n);
  const columns=q.columns==='*'?'*':q.columns.split(',').map(ident).join(',');
  const order=q.order.length?' order by '+q.order.map(ident).join(','):'';
  const limit=q.limit===null?'':' limit '+Number(q.limit);
  return {data:(await db.query('select '+columns+' from '+table+where+order+limit,params)).rows,count};
};
try {
  await epg.initialise(); await epg.start(); db=epg.getPgClient('postgres','127.0.0.1'); await db.connect();
  await db.query("create role anon; create role authenticated; create role service_role bypassrls; create schema auth; create function auth.uid() returns uuid language sql stable as $$select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid$$;");
  for(const file of ['supabase/schema.sql','supabase/migrations/002_opportunity_status_automation.sql',
                    'supabase/migrations/004_atomic_opportunity_registration.sql'])
    await db.query(readFileSync(join(root,file),'utf8'));
  for(const name of ['001_agent_foundation','002_collection_foundation','003_discovery_foundation',
    '004_source_review_foundation','005_collection_scheduling','006_processing_foundation',
    '007_publish_automation','008_discovery_scheduling','009_discovery_coverage',
    '010_discovery_exhibition_program','011_goal_driven_discovery','012_exact_ai_plan_claim',
    '013_exact_processing_seed','014_metadata_first_draft','015_metadata_approval_runtime_draft',
    '016_metadata_reviewer_source_scope','017_metadata_service_category_gate','018_metadata_atomic_publish'])
    await db.query(readFileSync(join(root,'agent_collect/migrations',name+'.sql'),'utf8'));
  const guardPath=process.env.AIPATH_TEST_USAGE_GUARD_SQL;
  if(guardPath) {
    const guard=readFileSync(guardPath);
    if(createHash('sha256').update(guard).digest('hex')!=='30e0db6360d8fc9434932c049c3aeceb089f8ad53262547500319d41834c418f')
      throw new Error('019 fixture SQL hash mismatch');
    await db.query(guard.toString('utf8'));
  }
  emit({ready:true,usage_guard:!!guardPath,version:(await db.query('show server_version')).rows[0].server_version});
  for await (const line of createInterface({input:process.stdin})) {
    const q=JSON.parse(line); if(q.kind==='stop') break;
    try {emit(await execute(q));} catch(error) {emit({error:error.message});}
  }
} catch(error) {
  console.error('ISOLATED_TEST_ERROR',error);
  process.exitCode=1;
} finally {
  process.stdin.destroy();
  if(db) await db.end();
  if(epg.process && epg.process.exitCode===null && epg.process.signalCode===null) await epg.stop();
  else rmSync(databaseDir,{recursive:true,force:true});
}
