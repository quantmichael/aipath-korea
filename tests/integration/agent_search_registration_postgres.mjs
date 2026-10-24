// Disposable Service 001+002+004 contract test for the actual Python preview envelope.
// No Supabase connection, provider call, or public Opportunity is involved.
import {execFileSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
import {randomUUID} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import EmbeddedPostgres from 'embedded-postgres';

const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const fixture = `
import json
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from agent_collect.metadata.search_registration import (
    SearchUseReview, preview_fetched_search_result, preview_search_registration,
)
from agent_collect.metadata.extraction import PublicNoticeFact
from agent_collect.discovery.quality import OfficialPageEvidence
from tests.test_agent_search_registration import _inputs

values = _inputs()
now = datetime.now(timezone.utc)
start = (now + timedelta(days=30)).date()
deadline = (now + timedelta(days=60)).date()
values['now'] = now
values['permission_valid_until'] = now + timedelta(days=1)
values['search_result'] = replace(values['search_result'], discovered_at=now)
values['official_page'] = replace(values['official_page'], checked_at=now,
    recruitment_start_on=start, recruitment_deadline_on=deadline)
values['facts']['application_start'] = PublicNoticeFact(start.isoformat(), 'B003', start.isoformat())
values['facts']['application_deadline'] = PublicNoticeFact(deadline.isoformat(), 'B004', deadline.isoformat())
preview = preview_search_registration(**values)
assert preview.eligible, preview.reasons
start_clock = datetime.combine(start, time(9), tzinfo=timezone(timedelta(hours=9)))
deadline_clock = datetime.combine(deadline, time(18), tzinfo=timezone(timedelta(hours=9)))
kst_url = values['search_result'].original_url.replace('ai-competition-2027', 'kst-competition')
kst_result = replace(values['search_result'], original_url=kst_url,
    canonical_url=kst_url, title='Synthetic KST Competition')
kst_page = (f'<html><body><h1>Synthetic KST Competition</h1>'
    f'<p>Organizer: Official Organizer</p>'
    f'<p>접수기간: {start_clock:%Y.%m.%d. %H:%M} KST ~ '
    f'{deadline_clock:%Y.%m.%d. %H:%M} KST</p>'
    f'<p>Application method: Apply on the official page</p>'
    f'<p>Category: competition</p></body></html>').encode()
kst_evidence = OfficialPageEvidence(kst_url, now,
    values['official_page'].authority, values['official_page'].page_type,
    recruitment_start_at=start_clock, recruitment_deadline_at=deadline_clock)
kst_preview = preview_fetched_search_result(
    search_result=kst_result, page=kst_page, page_url=kst_url,
    official_page=kst_evidence,
    service_sources=values['service_sources'],
    service_categories=[{'id': str(values['category_id']),
        'slug': 'competition', 'is_active': True}],
    existing_rows=[], snapshot_complete=True,
    use_review=SearchUseReview(True, 'reviewed', 'terms:B007', now + timedelta(days=1)),
    fetched_at=now, now=now)
assert kst_preview.eligible, kst_preview.reasons
assert kst_preview.evidence['application_deadline']['precision'] == 'EXACT_TIME'
print(json.dumps({'date_only': {'record': preview.record, 'evidence': preview.evidence},
    'exact_time': {'record': kst_preview.record, 'evidence': kst_preview.evidence}}))
`;
const envelopes = JSON.parse(execFileSync(process.env.PYTHON || 'python', ['-c', fixture],
  {cwd: root, encoding: 'utf8'}));
const envelope = envelopes.date_only;
const exactTime = envelopes.exact_time;
const epg = new EmbeddedPostgres({
  databaseDir: join(tmpdir(), 'aipath-search-registration-' + randomUUID()),
  user: 'postgres', password: 'isolated-only',
  port: 58000 + Math.floor(Math.random() * 500), persistent: false,
});
let db;
try {
  await epg.initialise();
  await epg.start();
  db = epg.getPgClient();
  await db.connect();
  await db.query('create role anon; create role authenticated; create role service_role bypassrls');
  for (const file of [
    'supabase/schema.sql',
    'supabase/migrations/001_collect_sources.sql',
    'supabase/migrations/002_opportunity_status_automation.sql',
    'supabase/migrations/004_atomic_opportunity_registration.sql',
  ]) await db.query(readFileSync(join(root, file), 'utf8'));
  await db.query("insert into public.sources(id,name,homepage_url,source_type,collection_method) " +
    "values($1,'AIPATH AI Discovery','https://fixture.example.org/','discovery','ai_search')",
  [envelope.record.source_id]);
  await db.query("insert into public.categories(id,name,slug) values($1,'Synthetic Competition','competition')",
    [envelope.record.category_id]);

  await db.query('set role service_role');
  const call = (record, evidence) => db.query(
    'select * from public.register_opportunity_with_evidence($1,$2,null,true)',
    [record, evidence]);
  const inserted = (await call(envelope.record, envelope.evidence)).rows[0];
  assert.equal(inserted.disposition, 'registered');
  assert.equal((await call(envelope.record, envelope.evidence)).rows[0].disposition,
    'protected_existing');
  const record = (await db.query(
    'select source_id,category_id,status,official_url from public.opportunities where id=$1',
    [inserted.opportunity_id])).rows[0];
  assert.equal(record.source_id, envelope.record.source_id);
  assert.equal(record.category_id, envelope.record.category_id);
  assert.equal(record.official_url, envelope.record.official_url);
  assert.equal(record.status, 'scheduled');

  await db.query('reset role');
  const scope = (await db.query(
    'select application_start_precision,application_deadline_precision ' +
    'from public.opportunity_status_automation_scope where opportunity_id=$1',
    [inserted.opportunity_id])).rows[0];
  assert.equal(scope.application_start_precision, 'DATE_ONLY');
  assert.equal(scope.application_deadline_precision, 'DATE_ONLY');
  await db.query('set role service_role');
  const exactInserted = (await call(exactTime.record, exactTime.evidence)).rows[0];
  assert.equal(exactInserted.disposition, 'registered');
  assert.equal((await call(exactTime.record, exactTime.evidence)).rows[0].disposition,
    'protected_existing');
  await db.query('reset role');
  const exactStored = (await db.query(
    'select o.application_deadline_at,s.application_deadline_precision ' +
    'from public.opportunities o join public.opportunity_status_automation_scope s ' +
    'on s.opportunity_id=o.id where o.id=$1',
    [exactInserted.opportunity_id])).rows[0];
  assert.equal(exactStored.application_deadline_precision, 'EXACT_TIME');
  assert.equal(new Date(exactStored.application_deadline_at).toISOString(),
    new Date(exactTime.record.application_deadline_at).toISOString());
  await db.query('set role service_role');
  const badRecord = {...envelope.record,
    official_url: 'https://fixture.example.org/notices/changed',
    external_id: 'search:changed', slug: 'ai-changed'};
  const badEvidence = {...envelope.evidence,
    application_deadline: {...envelope.evidence.application_deadline,
      source_date: new Date(Date.parse(envelope.evidence.application_deadline.source_date) -
        86400000).toISOString().slice(0, 10)}};
  await assert.rejects(call(badRecord, badEvidence), /source time evidence does not match/);
  assert.equal((await db.query('select count(*)::integer as n from public.opportunities'))
    .rows[0].n, 2);
  await db.query('reset role; set role authenticated');
  await assert.rejects(call(badRecord, envelope.evidence), /permission denied/);
  console.log('PASS DATE_ONLY and Korean KST EXACT_TIME envelopes, atomic registration, idempotency, rollback, grants');
} finally {
  if (db) await db.end();
  await epg.stop().catch(() => {});
}
