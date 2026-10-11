-- Staged foundation only: no existing Opportunity is changed and no Cron job
-- is created. Timestamp provenance must come from a trusted ingestion path.
begin;

create table public.opportunity_status_automation_scope (
  opportunity_id uuid primary key references public.opportunities(id) on delete cascade,
  application_start_precision text not null default 'UNKNOWN'
    check (application_start_precision in ('EXACT_TIME','DATE_ONLY','UNKNOWN')),
  application_deadline_precision text not null default 'UNKNOWN'
    check (application_deadline_precision in ('EXACT_TIME','DATE_ONLY','UNKNOWN')),
  event_start_precision text not null default 'UNKNOWN'
    check (event_start_precision in ('EXACT_TIME','DATE_ONLY','UNKNOWN')),
  event_end_precision text not null default 'UNKNOWN'
    check (event_end_precision in ('EXACT_TIME','DATE_ONLY','UNKNOWN')),
  application_start_snapshot timestamptz,
  application_deadline_snapshot timestamptz,
  event_start_snapshot timestamptz,
  event_end_snapshot timestamptz,
  application_start_source_date date,
  application_deadline_source_date date,
  application_start_basis text not null default 'UNKNOWN'
    check (application_start_basis in ('SOURCE_EXACT','POLICY_KST_DAY_START','UNKNOWN')),
  application_deadline_basis text not null default 'UNKNOWN'
    check (application_deadline_basis in ('SOURCE_EXACT','POLICY_KST_DAY_END','UNKNOWN')),
  evidence_ref text not null check (length(btrim(evidence_ref)) > 0),
  evidence_payload jsonb not null default '{}'::jsonb,
  hold_reason text,
  last_automated_status text
    check (last_automated_status in ('scheduled','open','closed','ongoing','ended')),
  last_automated_updated_at timestamptz,
  check ((last_automated_status is null) = (last_automated_updated_at is null)),
  check (application_start_precision <> 'EXACT_TIME' or application_start_snapshot is not null),
  check (application_deadline_precision <> 'EXACT_TIME' or application_deadline_snapshot is not null),
  check (event_start_precision <> 'EXACT_TIME' or event_start_snapshot is not null),
  check (event_end_precision <> 'EXACT_TIME' or event_end_snapshot is not null),
  check ((application_start_precision = 'DATE_ONLY') =
         (application_start_source_date is not null)),
  check ((application_deadline_precision = 'DATE_ONLY') =
         (application_deadline_source_date is not null)),
  check ((application_start_precision = 'EXACT_TIME' and application_start_basis = 'SOURCE_EXACT')
      or (application_start_precision = 'DATE_ONLY' and application_start_basis = 'POLICY_KST_DAY_START')
      or (application_start_precision = 'UNKNOWN' and application_start_basis = 'UNKNOWN')),
  check ((application_deadline_precision = 'EXACT_TIME' and application_deadline_basis = 'SOURCE_EXACT')
      or (application_deadline_precision = 'DATE_ONLY' and application_deadline_basis = 'POLICY_KST_DAY_END')
      or (application_deadline_precision = 'UNKNOWN' and application_deadline_basis = 'UNKNOWN'))
);

comment on table public.opportunity_status_automation_scope is
  'Source precision, KST date-only policy provenance, hold reason, and value snapshots. Empty at migration; only the worker writes last_automated_*.';

create table public.opportunity_status_automation_runs (
  id bigint generated always as identity primary key,
  observed_at timestamptz not null,
  examined_count bigint not null check (examined_count >= 0),
  updated_count bigint not null check (updated_count >= 0),
  outcome text not null check (outcome in ('completed','skipped_concurrent','failed')),
  error_sqlstate text,
  error_message text,
  recorded_at timestamptz not null default clock_timestamp()
);

create index opportunity_status_automation_runs_recorded_idx
  on public.opportunity_status_automation_runs (recorded_at desc);

alter table public.opportunity_status_automation_scope enable row level security;
alter table public.opportunity_status_automation_runs enable row level security;
revoke all on public.opportunity_status_automation_scope from public, anon, authenticated, service_role;
revoke all on public.opportunity_status_automation_runs from public, anon, authenticated, service_role;

-- The publisher supplies a source literal. An explicit offset is mandatory
-- for EXACT_TIME. DATE_ONLY must match the stored UTC calendar date generated
-- by the existing Collect path; this never treats its midnight as a cutoff.
create function public.opportunity_time_evidence_valid(
  p_stored timestamptz, p_detail jsonb
) returns boolean language plpgsql immutable set search_path = pg_catalog as $$
declare
  v_precision text := coalesce(p_detail->>'precision', 'UNKNOWN');
  v_literal text := p_detail->>'source_literal';
  v_date text := p_detail->>'source_date';
begin
  if v_precision = 'UNKNOWN' then
    return true;
  elsif v_precision = 'DATE_ONLY' then
    return coalesce(p_stored is not null
      and v_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
      and (p_stored at time zone 'UTC')::date = v_date::date, false);
  elsif v_precision = 'EXACT_TIME' then
    return coalesce(p_stored is not null
      and v_literal ~ 'T[0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]+)?)?(Z|[+-][0-9]{2}:[0-9]{2})$'
      and p_stored = v_literal::timestamptz, false);
  end if;
  return false;
exception when others then
  return false;
end;
$$;

revoke all on function public.opportunity_time_evidence_valid(timestamptz,jsonb)
  from public, anon, authenticated, service_role;

-- The service publisher calls this after its Opportunity insert/readback and
-- before acknowledging a promotion. It cannot alter worker provenance. A
-- failed second call leaves no scope row and therefore fails closed; retries
-- are idempotent. Only the trusted server service_role may invoke it.
create function public.record_opportunity_status_evidence(
  p_opportunity_id uuid, p_evidence jsonb
) returns text language plpgsql security definer set search_path = pg_catalog as $$
declare
  v_o public.opportunities%rowtype;
  v_ref text := p_evidence->>'evidence_ref';
  v_as jsonb := coalesce(p_evidence->'application_start', '{}'::jsonb);
  v_ad jsonb := coalesce(p_evidence->'application_deadline', '{}'::jsonb);
  v_es jsonb := coalesce(p_evidence->'event_start', '{}'::jsonb);
  v_ee jsonb := coalesce(p_evidence->'event_end', '{}'::jsonb);
  v_as_precision text;
  v_ad_precision text;
  v_es_precision text;
  v_ee_precision text;
begin
  if p_evidence is null or jsonb_typeof(p_evidence) <> 'object'
     or v_ref is null or length(btrim(v_ref)) = 0 or length(v_ref) > 500 then
    raise exception 'invalid opportunity time evidence envelope';
  end if;
  select * into v_o from public.opportunities where id = p_opportunity_id for update;
  if not found then raise exception 'opportunity not found'; end if;

  v_as_precision := coalesce(v_as->>'precision', 'UNKNOWN');
  v_ad_precision := coalesce(v_ad->>'precision', 'UNKNOWN');
  v_es_precision := coalesce(v_es->>'precision', 'UNKNOWN');
  v_ee_precision := coalesce(v_ee->>'precision', 'UNKNOWN');
  if not public.opportunity_time_evidence_valid(v_o.application_start_at, v_as)
     or not public.opportunity_time_evidence_valid(v_o.application_deadline_at, v_ad)
     or not public.opportunity_time_evidence_valid(v_o.event_start_at, v_es)
     or not public.opportunity_time_evidence_valid(v_o.event_end_at, v_ee) then
    raise exception 'source time evidence does not match stored opportunity';
  end if;
  if p_evidence ? 'last_automated_status'
     or p_evidence ? 'last_automated_updated_at' then
    raise exception 'worker provenance is not publisher writable';
  end if;

  insert into public.opportunity_status_automation_scope (
    opportunity_id, application_start_precision, application_deadline_precision,
    event_start_precision, event_end_precision,
    application_start_snapshot, application_deadline_snapshot,
    event_start_snapshot, event_end_snapshot,
    application_start_source_date, application_deadline_source_date,
    application_start_basis, application_deadline_basis,
    evidence_ref, evidence_payload, hold_reason
  ) values (
    p_opportunity_id, v_as_precision, v_ad_precision, v_es_precision, v_ee_precision,
    v_o.application_start_at, v_o.application_deadline_at,
    v_o.event_start_at, v_o.event_end_at,
    case when v_as_precision = 'DATE_ONLY' then (v_as->>'source_date')::date end,
    case when v_ad_precision = 'DATE_ONLY' then (v_ad->>'source_date')::date end,
    case when v_as_precision = 'EXACT_TIME' then 'SOURCE_EXACT'
         when v_as_precision = 'DATE_ONLY' then 'POLICY_KST_DAY_START' else 'UNKNOWN' end,
    case when v_ad_precision = 'EXACT_TIME' then 'SOURCE_EXACT'
         when v_ad_precision = 'DATE_ONLY' then 'POLICY_KST_DAY_END' else 'UNKNOWN' end,
    v_ref, p_evidence, nullif(p_evidence->>'hold_reason','')
  ) on conflict (opportunity_id) do update set
    application_start_precision = excluded.application_start_precision,
    application_deadline_precision = excluded.application_deadline_precision,
    event_start_precision = excluded.event_start_precision,
    event_end_precision = excluded.event_end_precision,
    application_start_snapshot = excluded.application_start_snapshot,
    application_deadline_snapshot = excluded.application_deadline_snapshot,
    event_start_snapshot = excluded.event_start_snapshot,
    event_end_snapshot = excluded.event_end_snapshot,
    application_start_source_date = excluded.application_start_source_date,
    application_deadline_source_date = excluded.application_deadline_source_date,
    application_start_basis = excluded.application_start_basis,
    application_deadline_basis = excluded.application_deadline_basis,
    evidence_ref = excluded.evidence_ref,
    evidence_payload = excluded.evidence_payload,
    hold_reason = excluded.hold_reason;
  return 'recorded';
end;
$$;

revoke all on function public.record_opportunity_status_evidence(uuid,jsonb)
  from public, anon, authenticated;
grant execute on function public.record_opportunity_status_evidence(uuid,jsonb)
  to service_role;

-- NULL means there is no supported registration boundary. Unknown event
-- boundaries never produce ongoing/ended, but a confirmed deadline may still
-- close registration. A confirmed past event end alone proves ended.
create function public.opportunity_status_target(
  p_at timestamptz,
  p_application_start_at timestamptz, p_application_start_precision text,
  p_application_start_source_date date,
  p_application_deadline_at timestamptz, p_application_deadline_precision text,
  p_application_deadline_source_date date,
  p_event_start_at timestamptz, p_event_start_precision text,
  p_event_end_at timestamptz, p_event_end_precision text
) returns text language sql immutable set search_path = pg_catalog as $$
  select case
    when p_at is null then null
    when p_event_end_at is not null and p_event_end_precision = 'EXACT_TIME'
      and p_at > p_event_end_at then 'ended'
    when p_event_start_at is not null and p_event_end_at is not null
      and p_event_start_precision = 'EXACT_TIME'
      and p_event_end_precision = 'EXACT_TIME'
      and p_at >= p_event_start_at then 'ongoing'
    when p_application_deadline_precision = 'EXACT_TIME'
      and p_application_deadline_at is not null
      and p_at > p_application_deadline_at then 'closed'
    when p_application_deadline_precision = 'DATE_ONLY'
      and p_application_deadline_source_date is not null
      and p_at >= ((p_application_deadline_source_date + 1)::timestamp
                    at time zone 'Asia/Seoul') then 'closed'
    when p_application_deadline_precision is distinct from 'EXACT_TIME'
      and p_application_deadline_precision is distinct from 'DATE_ONLY' then null
    when p_application_deadline_at is null
      or (p_application_deadline_precision = 'DATE_ONLY'
          and p_application_deadline_source_date is null) then null
    when p_application_start_precision = 'EXACT_TIME'
      and p_application_start_at is not null
      and p_at < p_application_start_at then 'scheduled'
    when p_application_start_precision = 'DATE_ONLY'
      and p_application_start_source_date is not null
      and p_at < (p_application_start_source_date::timestamp
                  at time zone 'Asia/Seoul') then 'scheduled'
    when p_application_start_at is not null
      and p_application_start_precision is distinct from 'EXACT_TIME'
      and p_application_start_precision is distinct from 'DATE_ONLY' then null
    when p_application_start_precision = 'DATE_ONLY'
      and p_application_start_at is not null
      and p_application_start_source_date is null then null
    else 'open'
  end;
$$;

revoke all on function public.opportunity_status_target(
  timestamptz,timestamptz,text,date,timestamptz,text,date,
  timestamptz,text,timestamptz,text
) from public, anon, authenticated, service_role;

-- Public archive membership is tied to a worker transition. Legacy closed
-- rows have no worker provenance and remain outside the established public
-- list. The API calls this using its trusted service role only.
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
grant execute on function public.opportunity_public_archive_ids(uuid[])
  to service_role;

-- The recommendation API uses the same source precision and KST policy as
-- the worker. Rows without current evidence return no window and are held.
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
  to service_role;

create function public.reconcile_opportunity_statuses()
returns table(observed_at timestamptz, examined_count bigint,
              updated_count bigint, outcome text)
language plpgsql security definer set search_path = pg_catalog as $$
declare
  v_at timestamptz := clock_timestamp();
  v_examined bigint := 0;
  v_updated bigint := 0;
  v_state text := 'completed';
  v_sqlstate text;
  v_error text;
begin
  if not pg_catalog.pg_try_advisory_xact_lock(20261011, 1) then
    insert into public.opportunity_status_automation_runs
      (observed_at, examined_count, updated_count, outcome)
    values (v_at, 0, 0, 'skipped_concurrent');
    return query select v_at, 0::bigint, 0::bigint, 'skipped_concurrent'::text;
    return;
  end if;

  -- An exception rolls back both opportunity and provenance updates in this
  -- subtransaction. The outer handler then records a failure without a partial
  -- status change. If even logging fails, pg_cron records the SQL error.
  begin
    with locked as materialized (
      select o.id, o.status as prior_status, o.updated_at as prior_updated_at,
        s.last_automated_status, s.last_automated_updated_at,
        case when o.application_start_at is not distinct from s.application_start_snapshot
           and o.application_deadline_at is not distinct from s.application_deadline_snapshot
           and o.event_start_at is not distinct from s.event_start_snapshot
           and o.event_end_at is not distinct from s.event_end_snapshot
          then public.opportunity_status_target(
            v_at, o.application_start_at, s.application_start_precision,
            s.application_start_source_date,
            o.application_deadline_at, s.application_deadline_precision,
            s.application_deadline_source_date,
            o.event_start_at, s.event_start_precision,
            o.event_end_at, s.event_end_precision)
          else null end as target_status
      from public.opportunities o
      join public.opportunity_status_automation_scope s on s.opportunity_id = o.id
      where o.status in ('scheduled','open','closed','ongoing')
      for update of o, s skip locked
    ), changed as (
      update public.opportunities o
      set status = l.target_status
      from locked l
      where o.id = l.id and l.target_status is not null
        and l.target_status is distinct from l.prior_status
        and (
          (l.prior_status = 'scheduled' and l.target_status in ('open','closed','ongoing','ended'))
          or (l.prior_status = 'open' and l.target_status in ('closed','ongoing','ended'))
          or (l.prior_status = 'closed' and l.target_status in ('ongoing','ended')
              and l.last_automated_status = 'closed'
              and l.last_automated_updated_at = l.prior_updated_at)
          or (l.prior_status = 'ongoing' and l.target_status = 'ended')
        )
      returning o.id, o.status, o.updated_at
    ), marked as (
      update public.opportunity_status_automation_scope s
      set last_automated_status = c.status,
          last_automated_updated_at = c.updated_at
      from changed c where s.opportunity_id = c.id
      returning s.opportunity_id
    )
    select (select count(*) from locked), (select count(*) from marked)
      into v_examined, v_updated;

    insert into public.opportunity_status_automation_runs
      (observed_at, examined_count, updated_count, outcome)
    values (v_at, v_examined, v_updated, v_state);
  exception when others then
    get stacked diagnostics v_sqlstate = returned_sqlstate, v_error = message_text;
    v_state := 'failed';
    v_examined := 0;
    v_updated := 0;
    insert into public.opportunity_status_automation_runs
      (observed_at, examined_count, updated_count, outcome, error_sqlstate, error_message)
    values (v_at, 0, 0, v_state, v_sqlstate, left(v_error, 500));
  end;

  return query select v_at, v_examined, v_updated, v_state;
end;
$$;

revoke all on function public.reconcile_opportunity_statuses()
  from public, anon, authenticated, service_role;

commit;
