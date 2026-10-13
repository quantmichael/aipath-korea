-- PREVIEW PROJECT ONLY. Synthetic, visibly labelled API fixtures; no
-- production Opportunity or candidate data is copied into Preview.
begin;

do $$
begin
  if to_regprocedure('public.preview_is_public_archive(uuid,text,timestamptz)')
      is null then
    raise exception 'Preview read-only schema is required';
  end if;
end;
$$;

insert into public.sources (id, name, homepage_url, collection_method)
values ('00000000-0000-4000-8000-000000000001',
        'PREVIEW TEST source', 'https://example.invalid', 'manual');

insert into public.categories (id, name, slug)
values ('00000000-0000-4000-8000-000000000002',
        'PREVIEW TEST', 'preview-test');

insert into public.opportunities (
  id, source_id, category_id, title, slug, summary, organizer, official_url,
  application_start_at, application_deadline_at, event_start_at, event_end_at,
  status, published_at, updated_at
) values
  ('00000000-0000-4000-8000-000000000101',
   '00000000-0000-4000-8000-000000000001',
   '00000000-0000-4000-8000-000000000002',
   'PREVIEW TEST — exact-time open', 'preview-exact-open',
   'Synthetic data for Preview API verification.', 'PREVIEW TEST',
   'https://example.invalid/preview/exact-open',
   '2026-01-01T00:00:00+09:00', '2030-01-20T14:00:00+09:00',
   '2030-02-01T00:00:00+09:00', '2030-02-05T00:00:00+09:00',
   'open', '2026-10-11T00:00:00+09:00', '2026-10-11T00:00:00+09:00'),
  ('00000000-0000-4000-8000-000000000102',
   '00000000-0000-4000-8000-000000000001',
   '00000000-0000-4000-8000-000000000002',
   'PREVIEW TEST — date-only open', 'preview-date-only-open',
   'Synthetic data for the KST next-day cutoff.', 'PREVIEW TEST',
   'https://example.invalid/preview/date-only-open',
   null, '2030-02-10T00:00:00+00:00', null, null,
   'open', '2026-10-11T00:00:00+09:00', '2026-10-11T00:00:00+09:00'),
  ('00000000-0000-4000-8000-000000000103',
   '00000000-0000-4000-8000-000000000001',
   '00000000-0000-4000-8000-000000000002',
   'PREVIEW TEST — legacy closed, hidden', 'preview-legacy-closed',
   'Must not appear in public list or detail.', 'PREVIEW TEST',
   'https://example.invalid/preview/legacy-closed',
   null, '2026-01-01T00:00:00+09:00', null, null,
   'closed', '2026-01-01T00:00:00+09:00', '2026-10-11T00:00:00+09:00'),
  ('00000000-0000-4000-8000-000000000104',
   '00000000-0000-4000-8000-000000000001',
   '00000000-0000-4000-8000-000000000002',
   'PREVIEW TEST — automated closed', 'preview-automated-closed',
   'Visible archive fixture with worker provenance.', 'PREVIEW TEST',
   'https://example.invalid/preview/automated-closed',
   null, '2026-01-01T00:00:00+09:00', null, null,
   'closed', '2026-01-01T00:00:00+09:00', '2026-10-11T00:00:00+09:00'),
  ('00000000-0000-4000-8000-000000000105',
   '00000000-0000-4000-8000-000000000001',
   '00000000-0000-4000-8000-000000000002',
   'PREVIEW TEST — automated ended', 'preview-automated-ended',
   'Visible ended fixture with worker provenance.', 'PREVIEW TEST',
   'https://example.invalid/preview/automated-ended',
   null, '2026-01-01T00:00:00+09:00',
   '2026-02-01T00:00:00+09:00', '2026-02-05T00:00:00+09:00',
   'ended', '2026-01-01T00:00:00+09:00', '2026-10-11T00:00:00+09:00'),
  ('00000000-0000-4000-8000-000000000106',
   '00000000-0000-4000-8000-000000000001',
   '00000000-0000-4000-8000-000000000002',
   'PREVIEW TEST — scheduled', 'preview-scheduled',
   'Synthetic scheduled status fixture.', 'PREVIEW TEST',
   'https://example.invalid/preview/scheduled',
   '2030-01-01T00:00:00+09:00', '2030-01-20T00:00:00+09:00',
   null, null,
   'scheduled', '2026-10-11T00:00:00+09:00', '2026-10-11T00:00:00+09:00'),
  ('00000000-0000-4000-8000-000000000107',
   '00000000-0000-4000-8000-000000000001',
   '00000000-0000-4000-8000-000000000002',
   'PREVIEW TEST — ongoing', 'preview-ongoing',
   'Synthetic ongoing status fixture.', 'PREVIEW TEST',
   'https://example.invalid/preview/ongoing',
   null, '2026-01-01T00:00:00+09:00',
   '2026-02-01T00:00:00+09:00', '2030-02-05T00:00:00+09:00',
   'ongoing', '2026-01-01T00:00:00+09:00', '2026-10-11T00:00:00+09:00');

insert into public.opportunity_status_automation_scope (
  opportunity_id, application_deadline_precision,
  application_deadline_source_date, application_start_snapshot,
  application_deadline_snapshot, event_start_snapshot, event_end_snapshot,
  event_end_precision, last_automated_status, last_automated_updated_at
) values
  ('00000000-0000-4000-8000-000000000101', 'EXACT_TIME', null,
   '2026-01-01T00:00:00+09:00', '2030-01-20T14:00:00+09:00',
   '2030-02-01T00:00:00+09:00', '2030-02-05T00:00:00+09:00',
   'EXACT_TIME', null, null),
  ('00000000-0000-4000-8000-000000000102', 'DATE_ONLY', '2030-02-10',
   null, '2030-02-10T00:00:00+00:00', null, null,
   'UNKNOWN', null, null),
  ('00000000-0000-4000-8000-000000000104', 'UNKNOWN', null,
   null, '2026-01-01T00:00:00+09:00', null, null,
   'UNKNOWN', 'closed', '2026-10-11T00:00:00+09:00'),
  ('00000000-0000-4000-8000-000000000105', 'UNKNOWN', null,
   null, '2026-01-01T00:00:00+09:00',
   '2026-02-01T00:00:00+09:00', '2026-02-05T00:00:00+09:00',
   'EXACT_TIME', 'ended', '2026-10-11T00:00:00+09:00');

commit;
