-- PREVIEW PROJECT ONLY. Apply after supabase/schema.sql to an empty, isolated
-- Supabase project. Never apply to the production Service DB. This deliberately
-- omits Migration 002's writer RPC, lifecycle worker, run log, and Cron.
begin;

create table public.opportunity_status_automation_scope (
  opportunity_id uuid primary key references public.opportunities(id) on delete cascade,
  application_deadline_precision text not null
    check (application_deadline_precision in ('EXACT_TIME','DATE_ONLY','UNKNOWN')),
  application_deadline_source_date date,
  application_start_snapshot timestamptz,
  application_deadline_snapshot timestamptz,
  event_start_snapshot timestamptz,
  event_end_snapshot timestamptz,
  event_end_precision text not null default 'UNKNOWN'
    check (event_end_precision in ('EXACT_TIME','DATE_ONLY','UNKNOWN')),
  last_automated_status text,
  last_automated_updated_at timestamptz,
  check ((last_automated_status is null) = (last_automated_updated_at is null)),
  check (application_deadline_precision <> 'DATE_ONLY'
         or application_deadline_source_date is not null)
);
alter table public.opportunity_status_automation_scope enable row level security;
revoke all on public.opportunity_status_automation_scope
  from public, anon, authenticated;

-- The RLS predicate only reads a provenance row. It never changes data.
create function public.preview_is_public_archive(
  p_id uuid, p_status text, p_updated_at timestamptz
) returns boolean language sql stable security definer
set search_path = pg_catalog as $$
  select p_status in ('closed','ended') and exists (
    select 1 from public.opportunity_status_automation_scope s
    where s.opportunity_id = p_id
      and s.last_automated_status = p_status
      and s.last_automated_updated_at = p_updated_at
  );
$$;
revoke all on function public.preview_is_public_archive(uuid,text,timestamptz)
  from public, anon, authenticated;
grant execute on function public.preview_is_public_archive(uuid,text,timestamptz)
  to anon;

create policy preview_public_opportunities on public.opportunities
  for select to anon using (
    status in ('scheduled','open','ongoing')
    or public.preview_is_public_archive(id,status,updated_at)
  );
create policy preview_public_sources on public.sources
  for select to anon using (true);
create policy preview_public_categories on public.categories
  for select to anon using (true);
create policy preview_public_tags on public.tags
  for select to anon using (true);
create policy preview_public_opportunity_tags on public.opportunity_tags
  for select to anon using (true);

grant usage on schema public to anon;
grant select on public.opportunities, public.sources, public.categories,
  public.tags, public.opportunity_tags to anon;

-- Legacy provenance can expose only the columns the API requests and only
-- the two previously audited Collect candidate IDs. No raw payload access.
create policy preview_legacy_candidate_proof on public.opportunity_candidates
  for select to anon using (
    candidate_status = 'promoted' and id in (
      '54eb504d-76e1-4334-b2a0-7224e8a466fc'::uuid,
      '53c6e18c-8fc8-44e6-bcf0-b6a24bef681a'::uuid
    )
  );
grant select (id, source_id, external_id, collection_run_id, created_at)
  on public.opportunity_candidates to anon;

-- Read RPC contracts match Migration 002, but this Preview-only script does
-- not install its write functions or worker.
create function public.opportunity_public_archive_ids(p_ids uuid[])
returns table(opportunity_id uuid)
language sql stable security definer set search_path = pg_catalog as $$
  select o.id
  from public.opportunities o
  join public.opportunity_status_automation_scope s on s.opportunity_id = o.id
  where o.id = any(p_ids)
    and o.status in ('closed','ended')
    and s.last_automated_status = o.status
    and s.last_automated_updated_at = o.updated_at;
$$;
revoke all on function public.opportunity_public_archive_ids(uuid[])
  from public, anon, authenticated;
grant execute on function public.opportunity_public_archive_ids(uuid[]) to anon;

create function public.opportunity_recommendation_windows(p_ids uuid[])
returns table(opportunity_id uuid, application_cutoff_at timestamptz,
              application_precision text, event_end_cutoff_at timestamptz)
language sql stable security definer set search_path = pg_catalog as $$
  select o.id,
    case s.application_deadline_precision
      when 'EXACT_TIME' then o.application_deadline_at
      when 'DATE_ONLY' then
        ((s.application_deadline_source_date + 1)::timestamp at time zone 'Asia/Seoul')
    end,
    s.application_deadline_precision,
    case when s.event_end_precision = 'EXACT_TIME' then o.event_end_at end
  from public.opportunities o
  join public.opportunity_status_automation_scope s on s.opportunity_id = o.id
  where o.id = any(p_ids)
    and o.status in ('scheduled','open','ongoing')
    and o.application_start_at is not distinct from s.application_start_snapshot
    and o.application_deadline_at is not distinct from s.application_deadline_snapshot
    and o.event_start_at is not distinct from s.event_start_snapshot
    and o.event_end_at is not distinct from s.event_end_snapshot;
$$;
revoke all on function public.opportunity_recommendation_windows(uuid[])
  from public, anon, authenticated;
grant execute on function public.opportunity_recommendation_windows(uuid[])
  to anon;

commit;
