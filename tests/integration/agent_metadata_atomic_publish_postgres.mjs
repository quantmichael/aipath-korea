// Isolated PostgreSQL contract test. Never connects to Supabase.
import {readFileSync} from 'node:fs';
import {randomUUID} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import EmbeddedPostgres from 'embedded-postgres';

const root=resolve(fileURLToPath(new URL('../..',import.meta.url)));
const epg=new EmbeddedPostgres({databaseDir:join(tmpdir(),'aipath-metadata-atomic-'+randomUUID()),
  user:'postgres',password:'isolated-only',port:58000+Math.floor(Math.random()*500),
  persistent:false});
let db,db2;
try {
  await epg.initialise(); await epg.start();
  db=epg.getPgClient(); await db.connect();
  await db.query("create role anon; create role authenticated; create role service_role bypassrls;"+
    "create schema auth; create function auth.uid() returns uuid language sql stable as $$"+
    "select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid$$;");
  for(const file of ['supabase/schema.sql',
    'supabase/migrations/002_opportunity_status_automation.sql',
    'supabase/migrations/004_atomic_opportunity_registration.sql'])
    await db.query(readFileSync(root+'/'+file,'utf8'));
  for(const name of ['001_agent_foundation','002_collection_foundation',
    '003_discovery_foundation','004_source_review_foundation',
    '005_collection_scheduling','006_processing_foundation',
    '007_publish_automation','008_discovery_scheduling',
    '009_discovery_coverage','010_discovery_exhibition_program',
    '011_goal_driven_discovery','012_exact_ai_plan_claim',
    '013_exact_processing_seed','014_metadata_first_draft',
    '015_metadata_approval_runtime_draft','016_metadata_reviewer_source_scope',
    '017_metadata_service_category_gate','018_metadata_atomic_publish'])
    await db.query(readFileSync(root+'/agent_collect/migrations/'+name+'.sql','utf8'));

  const reviewer=randomUUID();
  const categories=['conference','competition','hackathon','education','meetup'];
  const categoryIds={};
  for(const slug of categories)
    categoryIds[slug]=(await db.query('insert into public.categories(name,slug) '+
      'values($1,$2) returning id',['Synthetic '+slug,slug])).rows[0].id;
  const fixture=async (category, suffix)=>{
    const id=randomUUID(),source=randomUUID();
    const url='https://fixture.example.org/notices/'+id;
    const reviewed=new Date(Date.now()-60_000).toISOString();
    const verified=new Date().toISOString();
    await db.query('insert into public.agent_sources'+
      '(id,name,source_url,canonical_url,source_type,collection_method,confidence) '+
      "values($1,'Synthetic Lab',$2,$2,'official','html',0.99)",
      [source,url]);
    await db.query("select public.agent_review_source($1,'CANDIDATE','REVIEW',"+
      "'isolated reviewer','synthetic review','REVIEW_STARTED',$2)",
      [source,randomUUID()]);
    await db.query("select public.agent_review_source($1,'REVIEW','APPROVED',"+
      "'isolated reviewer','synthetic approval','APPROVED',$2,null,true,true)",
      [source,randomUUID()]);
    await db.query("select public.agent_review_source($1,'APPROVED','ACTIVE',"+
      "'isolated reviewer','synthetic activation','ACTIVATED',$2)",
      [source,randomUUID()]);
    const domainFallback=category==='competition' && suffix==='competition';
    const serviceSource=(await db.query('insert into public.sources(name,homepage_url) '+
      'values($1,$2) returning id',[
        domainFallback?'Synthetic Lab':'Synthetic Lab '+suffix,
        domainFallback?'https://fixture.example.org/':url,
      ])).rows[0].id;
    await db.query('update public.agent_metadata_reviewers set active=false where user_id=$1',
      [reviewer]);
    await db.query('insert into public.agent_metadata_reviewers'+
      '(user_id,active,source_id,granted_by) values($1,true,$2,$3) '+
      'on conflict (user_id) do update set active=true,source_id=excluded.source_id',
      [reviewer,source,'isolated fixture']);
    const approval=(await db.query('insert into public.agent_metadata_source_approvals'+
      '(source_id,cohort,reviewer,reviewed_at,expires_at,official_url,permission_url,'+
      'permission_digest,organizer_relationship,approved_organizer,page_type,'+
      'scope_origin,path_prefix,officiality_confirmed,public_read,automated_access,'+
      'fact_use,recheck_allowed) values($1,\'operational\',$2,$3,$4,$5,$6,$7,'+
      "'organizer','Synthetic Lab','INDIVIDUAL_NOTICE','https://fixture.example.org',"+
      "'/notices/',true,true,true,true,true) returning id",
      [source,reviewer,reviewed,new Date(Date.now()+86400_000).toISOString(),url,
        'https://fixture.example.org/policy','a'.repeat(64)])).rows[0].id;
    await db.query("select set_config('request.jwt.claim.sub',$1,false)",[reviewer]);
    await db.query('insert into public.agent_metadata_approval_events'+
      '(decision_id,approval_id,source_id,action,actor_uid,reason) '+
      "values($1,$2,$3,'APPROVED',$4,'isolated synthetic review')",
      [randomUUID(),approval,source,reviewer]);
    const facts={title:'Synthetic '+category+' '+suffix,summary:'Synthetic opportunity',
      organizer:'Synthetic Lab',official_url:url,application_start:'2027-01-04',
      application_deadline:'2027-04-13',application_method:'Online',category};
    if(category!=='competition'){
      facts.event_start='2027-04-14'; facts.event_end='2027-04-16';
    }
    const fieldEvidence=Object.fromEntries(Object.keys(facts).map(name=>
      [name,{source_url:url,evidence_reference:'B-'+name}]));
    const verification={status:'VERIFIED',time_precision:'DATE_ONLY',
      evidence_refs:[url+'#B-title']};
    const duplicate={status:'UNIQUE'},gate={status:'PUBLISH_READY'};
    const candidate=(await db.query('insert into public.agent_metadata_candidates'+
      '(source_id,approval_id,cohort,official_url,facts,field_evidence,status,'+
      'verification,duplicate_result,gate_decision,discovered_at,verified_at) '+
      "values($1,$2,'operational',$3,$4,$5,'PUBLISH_READY',$6,$7,$8,$9,$9) returning id",
      [source,approval,url,facts,fieldEvidence,verification,duplicate,gate,verified])).rows[0].id;
    await db.query('insert into public.agent_metadata_candidate_events'+
      '(decision_id,candidate_id,from_status,to_status,actor_kind,reason,details) '+
      "values($1,$2,'REVIEW_REQUIRED','PUBLISH_READY','service_role','assessment',$3)",
      [randomUUID(),candidate,{verification,duplicate,gate,verified_at:verified}]);
    const record={source_id:serviceSource,category_id:categoryIds[category],
      external_id:'runtime_agent_metadata:'+candidate,
      slug:'synthetic-'+category+'-'+candidate.replaceAll('-',''),
      title:facts.title,summary:facts.summary,organizer:facts.organizer,
      official_url:url,is_featured:false,last_verified_at:verified,
      application_start_at:'2027-01-04T00:00:00Z',
      application_deadline_at:'2027-04-13T00:00:00Z',status:'scheduled'};
    const evidence={evidence_ref:'agent-metadata:'+candidate};
    for(const field of ['application_start','application_deadline',
                        'event_start','event_end']){
      if(facts[field]){
        record[field+'_at']=facts[field]+'T00:00:00Z';
        evidence[field]={precision:'DATE_ONLY',source_date:facts[field],
          source_ref:url+'#B-'+field};
      } else evidence[field]={precision:'UNKNOWN'};
    }
    return {candidate,approval,source,url,record,evidence};
  };
  await db.query('set role service_role');
  const call=(client,x)=>client.query(
    'select * from public.agent_publish_metadata_candidate($1,$2,$3)',
    [x.candidate,x.record,x.evidence]);
  const created=[];
  for(const category of categories){
    await db.query('reset role');
    const x=await fixture(category,category);
    await db.query('set role service_role');
    const row=(await call(db,x)).rows[0];
    assert.equal(row.disposition,'registered');
    assert.equal((await call(db,x)).rows[0].opportunity_id,row.opportunity_id);
    assert.equal((await call(db,x)).rows[0].disposition,'already_published');
    await db.query('reset role');
    assert.equal((await db.query('select count(*)::integer as n from '+
      'public.opportunity_status_automation_scope where opportunity_id=$1',
      [row.opportunity_id])).rows[0].n,1);
    await db.query('set role service_role');
    created.push(row.opportunity_id);
  }
  assert.equal((await db.query('select count(*)::integer as n from '+
    'public.agent_metadata_publish_completions')).rows[0].n,5);
  await db.query('reset role');
  const revoked=await fixture('conference','revoked');
  await db.query("select set_config('request.jwt.claim.sub',$1,false)",[reviewer]);
  await db.query('set role authenticated');
  await db.query('select public.agent_metadata_revoke($1,$2,$3)',
    [randomUUID(),revoked.approval,'synthetic revocation']);
  await db.query('reset role; set role service_role');
  await assert.rejects(call(db,revoked),/active audited metadata permission/);
  await db.query('reset role');
  const expired=await fixture('conference','expired');
  await db.query('update public.agent_metadata_candidates set verified_at=now()-interval '+
    "'20 minutes' where id=$1",[expired.candidate]);
  await db.query('set role service_role');
  await assert.rejects(call(db,expired),/fresh VERIFIED/);
  await db.query('reset role');
  const mismatch=await fixture('competition','mismatch');
  await db.query('set role service_role');
  await assert.rejects(call(db,{...mismatch,record:{...mismatch.record,title:'Forged'}}),
    /envelope differ/);
  await assert.rejects(call(db,{...mismatch,record:{...mismatch.record,
    slug:'invalid slug-'+mismatch.candidate.replaceAll('-','')}}),
  /opportunities_slug_format/);
  await db.query('reset role');
  const duplicate=await fixture('education','duplicate');
  await db.query('insert into public.opportunities'+
    '(source_id,category_id,title,slug,summary,organizer,official_url,status) '+
    "values($1,$2,'Existing',$3,'Existing','Synthetic Lab',$4,'open')",
    [duplicate.record.source_id,duplicate.record.category_id,
      'existing-'+randomUUID().replaceAll('-',''),duplicate.url]);
  await db.query('set role service_role');
  await assert.rejects(call(db,duplicate),/existing Service Opportunity/);
  assert.equal((await db.query('select count(*)::integer as n from '+
    'public.agent_metadata_publish_completions where candidate_id=$1',
    [duplicate.candidate])).rows[0].n,0);
  await db.query('reset role');
  await db.query('delete from public.opportunities where official_url=$1',
    [duplicate.url]);
  const rollback=await fixture('hackathon','rollback');
  await db.query("create function public.synthetic_completion_failure() returns trigger "+
    "language plpgsql as $$begin raise exception 'synthetic completion failure'; end$$");
  await db.query('create trigger synthetic_completion_failure before insert on '+
    'public.agent_metadata_publish_completions for each row '+
    'execute function public.synthetic_completion_failure()');
  await db.query('set role service_role');
  await assert.rejects(call(db,rollback),/synthetic completion failure/);
  await db.query('reset role');
  assert.equal((await db.query('select count(*)::integer as n from public.opportunities'))
    .rows[0].n,5);
  await db.query('drop trigger synthetic_completion_failure on '+
    'public.agent_metadata_publish_completions');
  await db.query('drop function public.synthetic_completion_failure()');
  const concurrent=await fixture('meetup','concurrent');
  db2=epg.getPgClient(); await db2.connect();
  await db.query('set role service_role');
  await db2.query('set role service_role');
  const both=await Promise.all([call(db,concurrent),call(db2,concurrent)]);
  assert.deepEqual(both.map(x=>x.rows[0].disposition).sort(),
    ['already_published','registered']);
  assert.equal(both[0].rows[0].opportunity_id,both[1].rows[0].opportunity_id);
  assert.equal((await db.query('select count(*)::integer as n from public.opportunities'))
    .rows[0].n,6);
  await db.query('reset role; set role anon');
  await assert.rejects(call(db,mismatch),/permission denied/);
  await db.query('reset role');
  console.log('PASS metadata atomic five categories, retry, evidence, revoked, stale, grants');
} finally {
  if(db2) await db2.end();
  if(db) await db.end();
  await epg.stop().catch(()=>{});
}
