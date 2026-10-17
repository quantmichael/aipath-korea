// Isolated Service 002+004 registration compatibility for metadata dates.
import {readFileSync} from 'node:fs';
import {randomUUID} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import EmbeddedPostgres from 'embedded-postgres';

const root=resolve(fileURLToPath(new URL('../..',import.meta.url)));
const epg=new EmbeddedPostgres({databaseDir:join(tmpdir(),'aipath-metadata-service-'+randomUUID()),
  user:'postgres',password:'isolated-only',port:58000+Math.floor(Math.random()*500),
  persistent:false});
let db;
try {
  await epg.initialise(); await epg.start();
  db=epg.getPgClient(); await db.connect();
  await db.query('create role anon; create role authenticated; create role service_role bypassrls');
  await db.query(readFileSync(root+'/supabase/schema.sql','utf8'));
  await db.query(readFileSync(root+'/supabase/migrations/002_opportunity_status_automation.sql','utf8'));
  await db.query(readFileSync(root+'/supabase/migrations/004_atomic_opportunity_registration.sql','utf8'));
  const source=(await db.query("insert into public.sources(name,homepage_url) "+
    "values('Synthetic Metadata Lab','https://fixture.example.org/') returning id")).rows[0].id;
  const categories=['conference','competition','hackathon','education','meetup'];
  const categoryIds=new Map();
  for(const slug of categories) {
    const id=(await db.query('insert into public.categories(name,slug) values($1,$2) returning id',
      ['Synthetic '+slug,slug])).rows[0].id;
    categoryIds.set(slug,id);
  }
  await db.query('set role service_role');
  const call=(record,evidence)=>db.query(
    'select * from public.register_opportunity_with_evidence($1,$2,null,true)',
    [record,evidence]);
  const ids=[];
  for(const slug of categories) {
    const id=randomUUID();
    const record={source_id:source,category_id:categoryIds.get(slug),
      external_id:'synthetic-metadata:'+id,slug:'synthetic-'+slug+'-'+id,
      title:'Synthetic '+slug,summary:'Isolated category contract',
      organizer:'Synthetic Metadata Lab',official_url:'https://fixture.example.org/'+id,
      application_start_at:'2027-01-04T00:00:00+00:00',
      application_deadline_at:'2027-04-13T00:00:00+00:00',
      status:'scheduled'};
    const evidence={evidence_ref:'synthetic-metadata:'+id,
      application_start:{precision:'DATE_ONLY',source_date:'2027-01-04',source_ref:'B001'},
      application_deadline:{precision:'DATE_ONLY',source_date:'2027-04-13',source_ref:'B002'},
      event_start:{precision:'UNKNOWN'},event_end:{precision:'UNKNOWN'}};
    if(slug!=='competition') {
      record.event_start_at='2027-04-14T00:00:00+00:00';
      record.event_end_at='2027-04-16T00:00:00+00:00';
      evidence.event_start={precision:'DATE_ONLY',source_date:'2027-04-14',source_ref:'B003'};
      evidence.event_end={precision:'DATE_ONLY',source_date:'2027-04-16',source_ref:'B004'};
    }
    const inserted=(await call(record,evidence)).rows[0];
    assert.equal(inserted.disposition,'registered');
    ids.push(inserted.opportunity_id);
    assert.equal((await call(record,evidence)).rows[0].disposition,'protected_existing');
    const stored=(await db.query('select category_id,status,event_end_at from public.opportunities '+
      'where id=$1',[inserted.opportunity_id])).rows[0];
    assert.equal(stored.category_id,record.category_id);
    assert.equal(stored.status,'scheduled');
    assert.equal(stored.event_end_at===null,slug==='competition');
    await db.query('reset role');
    const scope=(await db.query('select application_deadline_precision,event_end_precision '+
      'from public.opportunity_status_automation_scope where opportunity_id=$1',
      [inserted.opportunity_id])).rows[0];
    await db.query('set role service_role');
    assert.equal(scope.application_deadline_precision,'DATE_ONLY');
    assert.equal(scope.event_end_precision,slug==='competition'?'UNKNOWN':'DATE_ONLY');
  }
  assert.equal((await db.query('select count(*)::integer as n from public.opportunities'))
    .rows[0].n,categories.length);
  const badId=randomUUID(),badRecord={source_id:source,category_id:categoryIds.get('competition'),
    external_id:'synthetic-metadata:'+badId,slug:'synthetic-bad-'+badId,
    title:'Synthetic bad evidence',summary:'Should roll back',
    organizer:'Synthetic Metadata Lab',official_url:'https://fixture.example.org/'+badId,
    application_start_at:'2027-01-04T00:00:00+00:00',
    application_deadline_at:'2027-04-13T00:00:00+00:00',status:'scheduled'};
  const badEvidence={evidence_ref:'synthetic-metadata:'+badId,
    application_start:{precision:'DATE_ONLY',source_date:'2027-01-04',source_ref:'B001'},
    application_deadline:{precision:'DATE_ONLY',source_date:'2027-04-12',source_ref:'B002'},
    event_start:{precision:'UNKNOWN'},event_end:{precision:'UNKNOWN'}};
  await assert.rejects(call(badRecord,badEvidence),/source time evidence does not match/);
  assert.equal((await db.query('select count(*)::integer as n from public.opportunities'))
    .rows[0].n,categories.length);
  await db.query('reset role; set role authenticated');
  await assert.rejects(call(badRecord,badEvidence),/permission denied/);
  await db.query('reset role');
  console.log('PASS metadata category DATE_ONLY, optional event, atomic rollback, duplicate, RPC grants');
} finally {
  if(db) await db.end();
  await epg.stop().catch(()=>{});
}
