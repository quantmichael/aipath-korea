-- ONE-TIME VERIFIED EVIDENCE REGISTRATION. DO NOT REAPPLY.
-- Three individually checked Service rows are included. This file writes
-- scope metadata, not opportunity status. Migration 002 and Cron are already
-- active in Production: preflight and preview impact before this one-time run.
begin;

do $preflight$
begin
  if (select count(*) from public.opportunities) <> 17
     or (select count(*) from public.opportunities where status = 'open') <> 11
     or (select count(*) from public.opportunities where status = 'closed') <> 6
     or exists (select 1 from public.opportunity_status_automation_scope
                where opportunity_id in (
                  'c3a00d1d-1ebb-4deb-903b-fe7300a19ea6'::uuid,
                  '883d7e82-5870-474f-8d70-e39cedfeddff'::uuid,
                  'cc108cc2-ae35-4c39-90dc-a6cc11873ee6'::uuid))
  then raise exception 'Service inventory or scope changed since evidence audit'; end if;
  if not exists (
    select 1 from public.opportunities
    where id = 'c3a00d1d-1ebb-4deb-903b-fe7300a19ea6'::uuid
      and status = 'open'
      and application_start_at = '2026-09-08T00:00:00+00:00'::timestamptz
      and application_deadline_at = '2026-10-06T00:00:00+00:00'::timestamptz
      and event_start_at is null and event_end_at is null
  ) then raise exception 'Bizinfo 126283 preflight mismatch'; end if;
  if not exists (
    select 1 from public.opportunities
    where id = '883d7e82-5870-474f-8d70-e39cedfeddff'::uuid
      and status = 'open'
      and application_start_at = '2026-09-28T00:00:00+00:00'::timestamptz
      and application_deadline_at = '2026-10-12T00:00:00+00:00'::timestamptz
      and event_start_at is null and event_end_at is null
  ) then raise exception 'Bizinfo 126814 preflight mismatch'; end if;
  if not exists (
    select 1 from public.opportunities
    where id = 'cc108cc2-ae35-4c39-90dc-a6cc11873ee6'::uuid
      and status = 'open'
      and application_start_at is null
      and application_deadline_at = '2026-10-03T03:00:00+00:00'::timestamptz
      and event_start_at = '2026-10-03T03:00:00+00:00'::timestamptz
      and event_end_at = '2026-10-03T05:00:00+00:00'::timestamptz
  ) then raise exception 'Meetup event preflight mismatch'; end if;
end;
$preflight$;

-- The official Bizinfo page gives 2026-09-08 ~ 2026-10-06 without clock time.
-- The KST end-of-day threshold is a service policy, not an original cutoff.
select public.record_opportunity_status_evidence(
  'c3a00d1d-1ebb-4deb-903b-fe7300a19ea6'::uuid,
  jsonb_build_object(
    'evidence_ref','https://www.bizinfo.go.kr/sii/siia/selectSIIA200Detail.do?pblancId=PBLN_000000000126283',
    'application_start',jsonb_build_object('precision','DATE_ONLY',
      'source_date','2026-09-08','source_ref','official-page:application-period'),
    'application_deadline',jsonb_build_object('precision','DATE_ONLY',
      'source_date','2026-10-06','source_ref','official-page:application-period')
  )
);

-- This future official Bizinfo date-only period remains open now. The worker
-- will close it only from 2026-10-13 00:00 Asia/Seoul by service policy.
select public.record_opportunity_status_evidence(
  '883d7e82-5870-474f-8d70-e39cedfeddff'::uuid,
  jsonb_build_object(
    'evidence_ref','https://www.bizinfo.go.kr/sii/siia/selectSIIA200Detail.do?pblancId=PBLN_000000000126814',
    'application_start',jsonb_build_object('precision','DATE_ONLY',
      'source_date','2026-09-28','source_ref','official-page:application-period'),
    'application_deadline',jsonb_build_object('precision','DATE_ONLY',
      'source_date','2026-10-12','source_ref','official-page:application-period')
  )
);

-- The official Meetup page explicitly says 12:00–14:00 KST. It does not
-- establish a registration cutoff, so application_deadline remains UNKNOWN.
select public.record_opportunity_status_evidence(
  'cc108cc2-ae35-4c39-90dc-a6cc11873ee6'::uuid,
  jsonb_build_object(
    'evidence_ref','https://www.meetup.com/ko-kr/ai-seoul-public/events/kssfbvyjcnbfb/',
    'event_start',jsonb_build_object('precision','EXACT_TIME',
      'source_literal','2026-10-03T12:00:00+09:00','source_ref','official-event-time-KST'),
    'event_end',jsonb_build_object('precision','EXACT_TIME',
      'source_literal','2026-10-03T14:00:00+09:00','source_ref','official-event-time-KST')
  )
);

commit;
