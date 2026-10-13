-- Schema record for the 2026-10-13 manually corrected ended Opportunities.
-- ALREADY APPLIED TO PRODUCTION fehlvyoskgphlodzawfn: NEVER RUN THERE.
-- This migration is for fresh/non-production databases after 002 only.
begin;

create table public.opportunity_manual_status_audit (
  opportunity_id uuid not null references public.opportunities(id) on delete restrict,
  prior_updated_at timestamptz not null,
  resulting_updated_at timestamptz not null,
  status_before text not null check (status_before = 'open'),
  status_after text not null check (status_after = 'ended'),
  evidence_ref text not null check (evidence_ref ~ '^https://'),
  evidence_basis text not null check
    (evidence_basis in ('OFFICIAL_DATE_RANGE_ELAPSED', 'OFFICIAL_POST_EVENT_RECAP')),
  evidence_summary text not null check (length(btrim(evidence_summary)) > 0),
  operator_id text not null check (length(btrim(operator_id)) > 0),
  authorization_ref text not null check (length(btrim(authorization_ref)) > 0),
  public_archive_approved boolean not null default false,
  recorded_at timestamptz not null default clock_timestamp(),
  primary key (opportunity_id, resulting_updated_at),
  check (resulting_updated_at > prior_updated_at)
);

comment on table public.opportunity_manual_status_audit is
  'Append-only, owner-written audit for authorized manual ended corrections; never Cron or exact-time evidence.';

alter table public.opportunity_manual_status_audit enable row level security;
revoke all on public.opportunity_manual_status_audit
  from public, anon, authenticated, service_role;

create function public.opportunity_manual_status_audit_immutable()
returns trigger language plpgsql set search_path = pg_catalog as $$
begin
  raise exception 'Manual Opportunity status audit is append-only';
end;
$$;
revoke all on function public.opportunity_manual_status_audit_immutable()
  from public, anon, authenticated, service_role;

create trigger opportunity_manual_status_audit_immutable
before update or delete on public.opportunity_manual_status_audit
for each row execute function public.opportunity_manual_status_audit_immutable();

create or replace function public.opportunity_public_archive_ids(p_ids uuid[])
returns table(opportunity_id uuid)
language sql stable security definer set search_path = pg_catalog as $$
  select o.id
  from public.opportunities o
  where o.id = any(p_ids)
    and o.status in ('closed', 'ended')
    and (
      exists (
        select 1 from public.opportunity_status_automation_scope s
        where s.opportunity_id = o.id
          and s.last_automated_status = o.status
          and s.last_automated_updated_at = o.updated_at
      )
      or (
        o.status = 'ended' and o.published_at is not null
        and exists (
          select 1 from public.opportunity_manual_status_audit m
          where m.opportunity_id = o.id and m.status_after = 'ended'
            and m.resulting_updated_at = o.updated_at
            and m.public_archive_approved
        )
      )
    );
$$;

revoke all on function public.opportunity_public_archive_ids(uuid[])
  from public, anon, authenticated;
grant execute on function public.opportunity_public_archive_ids(uuid[])
  to service_role;

commit;
