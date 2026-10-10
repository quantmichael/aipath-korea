-- PHASE 10: Agent-only Conference Discovery scheduling. CREATE ONLY; do not auto-apply.
-- Requires 001-007. No Service objects, Source lifecycle, or historical rows are changed.
begin;

create table public.agent_discovery_schedules (
  id uuid primary key default gen_random_uuid(),
  query_text text not null check (length(btrim(query_text)) > 0),
  query_key text generated always as (lower(regexp_replace(btrim(query_text), '[[:space:]]+', ' ', 'g'))) stored,
  vertical text not null default 'conference' check (vertical = 'conference'),
  language text not null check (language ~ '^[a-z]{2}$'),
  country text not null check (country ~ '^[A-Z]{2}$'),
  interval_minutes integer not null default 1440 check (interval_minutes >= 1440),
  failure_backoff_minutes integer[] not null default array[60,360,1440]
    check (coalesce(array_length(failure_backoff_minutes,1) between 1 and 8,false)
      and array_position(failure_backoff_minutes,null) is null
      and 0 < all(failure_backoff_minutes)),
  budget_delay_minutes integer not null default 1440 check (budget_delay_minutes >= 1),
  enabled boolean not null default false,
  reviewed_by text,
  review_reason text,
  reviewed_at timestamptz,
  next_run_at timestamptz not null default clock_timestamp(),
  last_run_at timestamptz,
  last_success_at timestamptz,
  last_failure_at timestamptz,
  consecutive_failures integer not null default 0 check (consecutive_failures >= 0),
  last_outcome text check (last_outcome in ('SUCCEEDED','FAILED','BUDGET_BLOCKED')),
  claim_id uuid,
  worker_id text,
  claimed_at timestamptz,
  claim_expires_at timestamptz,
  current_window_id uuid,
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  constraint agent_discovery_schedule_identity_unique unique (query_key,vertical,language,country),
  constraint agent_discovery_schedule_approval check (
    not enabled or (nullif(btrim(reviewed_by),'') is not null and
                    nullif(btrim(review_reason),'') is not null and reviewed_at is not null)),
  constraint agent_discovery_schedule_claim_check check (
    (claim_id is null and worker_id is null and claimed_at is null and
     claim_expires_at is null and current_window_id is null) or
    (claim_id is not null and nullif(btrim(worker_id),'') is not null and
     claimed_at is not null and claim_expires_at > claimed_at and current_window_id is not null))
);
create index agent_discovery_schedule_due_idx
  on public.agent_discovery_schedules(next_run_at,id) where enabled;
alter table public.agent_discovery_schedules enable row level security;
revoke all on public.agent_discovery_schedules from public,anon,authenticated,service_role;
grant select on public.agent_discovery_schedules to service_role;

-- Two conservative, disabled examples. Enabling is a separate reviewed action.
insert into public.agent_discovery_schedules(query_text,language,country)
values ('AI conference Korea','en','KR'),('인공지능 컨퍼런스','ko','KR');

alter table public.agent_discovery_queries
  add column schedule_id uuid references public.agent_discovery_schedules(id),
  add column schedule_window_id uuid,
  add column schedule_claim_id uuid,
  add column schedule_worker_id text,
  add column scheduled_db_completed_at timestamptz;
alter table public.agent_discovery_queries add constraint agent_discovery_query_schedule_complete check (
  (schedule_id is null and schedule_window_id is null and schedule_claim_id is null and schedule_worker_id is null)
  or (schedule_id is not null and schedule_window_id is not null and schedule_claim_id is not null and
      nullif(btrim(schedule_worker_id),'') is not null));
create unique index agent_discovery_query_window_unique
  on public.agent_discovery_queries(schedule_id,schedule_window_id)
  where schedule_id is not null;

create table public.agent_discovery_windows (
  schedule_id uuid not null references public.agent_discovery_schedules(id),
  window_id uuid not null,
  claim_id uuid not null,
  status text not null check (status in ('CLAIMED','SUCCEEDED','FAILED','BUDGET_BLOCKED')),
  query_id uuid references public.agent_discovery_queries(id),
  claimed_at timestamptz not null,
  completed_at timestamptz,
  primary key (schedule_id,window_id)
);
create unique index agent_discovery_window_claim_unique on public.agent_discovery_windows(claim_id);
alter table public.agent_discovery_windows enable row level security;
revoke all on public.agent_discovery_windows from public,anon,authenticated,service_role;
grant select on public.agent_discovery_windows to service_role;

create function public.agent_discovery_query_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog as $$
declare v_schedule public.agent_discovery_schedules%rowtype; v_now timestamptz;
begin
  if new.schedule_id is null then
    if tg_op='UPDATE' and old.schedule_id is not null then
      raise exception 'scheduled query identity cannot be removed';
    end if;
    return new;
  end if;
  if tg_op='UPDATE' and (new.schedule_id,new.schedule_window_id,new.schedule_claim_id,new.schedule_worker_id)
      is distinct from (old.schedule_id,old.schedule_window_id,old.schedule_claim_id,old.schedule_worker_id) then
    raise exception 'scheduled query identity is immutable';
  end if;
  select * into v_schedule from public.agent_discovery_schedules where id=new.schedule_id for share;
  v_now:=clock_timestamp();
  if not found or not v_schedule.enabled or v_schedule.claim_id is distinct from new.schedule_claim_id or
     v_schedule.current_window_id is distinct from new.schedule_window_id or
     v_schedule.worker_id is distinct from new.schedule_worker_id or
     v_schedule.claim_expires_at<=v_now or new.provider<>'tavily' or
     new.vertical_hint<>'conference' or new.query_text<>v_schedule.query_text then
    raise exception 'scheduled Discovery query lacks a live matching claim';
  end if;
  if tg_op='UPDATE' and old.status='SUCCEEDED' and new.status<>'SUCCEEDED' then
    raise exception 'successful Discovery query is terminal';
  end if;
  if new.status in ('SUCCEEDED','FAILED') then
    new.scheduled_db_completed_at:=case when tg_op='UPDATE'
      then coalesce(old.scheduled_db_completed_at,v_now) else v_now end;
  else
    new.scheduled_db_completed_at:=null;
  end if;
  return new;
end;
$$;
alter function public.agent_discovery_query_guard() owner to postgres;
revoke all on function public.agent_discovery_query_guard() from public,anon,authenticated,service_role;
create trigger agent_discovery_query_guard_trigger before insert or update on public.agent_discovery_queries
  for each row execute function public.agent_discovery_query_guard();

create function public.agent_register_discovery_schedule(
  p_query_text text,p_language text,p_country text,p_interval_minutes integer,
  p_reviewer text,p_reason text)
returns public.agent_discovery_schedules
language plpgsql security definer set search_path=pg_catalog as $$
declare v_schedule public.agent_discovery_schedules%rowtype;
begin
  if nullif(btrim(p_query_text),'') is null or p_language !~ '^[a-z]{2}$' or
     p_country !~ '^[A-Z]{2}$' or p_interval_minutes is null or
     p_interval_minutes<1440 or nullif(btrim(p_reviewer),'') is null or
     nullif(btrim(p_reason),'') is null then
    raise exception 'reviewed Conference query registration required';
  end if;
  insert into public.agent_discovery_schedules(
    query_text,vertical,language,country,interval_minutes,enabled,
    reviewed_by,review_reason,reviewed_at)
  values (p_query_text,'conference',p_language,p_country,p_interval_minutes,false,
          p_reviewer,p_reason,clock_timestamp()) returning * into v_schedule;
  return v_schedule;
end;
$$;
alter function public.agent_register_discovery_schedule(text,text,text,integer,text,text) owner to postgres;
revoke all on function public.agent_register_discovery_schedule(text,text,text,integer,text,text)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_register_discovery_schedule(text,text,text,integer,text,text)
  to service_role;

create function public.agent_set_discovery_schedule_enabled(
  p_schedule_id uuid,p_enabled boolean,p_reviewer text,p_reason text)
returns boolean language plpgsql security definer set search_path=pg_catalog as $$
begin
  if p_schedule_id is null or p_enabled is null or nullif(btrim(p_reviewer),'') is null or
     nullif(btrim(p_reason),'') is null then raise exception 'review decision required'; end if;
  update public.agent_discovery_schedules set enabled=p_enabled,reviewed_by=p_reviewer,
    review_reason=p_reason,reviewed_at=clock_timestamp(),updated_at=clock_timestamp()
    where id=p_schedule_id and claim_id is null;
  return found;
end;
$$;
alter function public.agent_set_discovery_schedule_enabled(uuid,boolean,text,text) owner to postgres;
revoke all on function public.agent_set_discovery_schedule_enabled(uuid,boolean,text,text)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_set_discovery_schedule_enabled(uuid,boolean,text,text) to service_role;

create function public.agent_list_due_discovery(p_limit integer)
returns setof public.agent_discovery_schedules
language plpgsql security definer set search_path=pg_catalog as $$
declare v_now timestamptz:=clock_timestamp();
begin
  if p_limit is null or p_limit not between 1 and 100 then raise exception 'invalid Discovery limit'; end if;
  return query select s.* from public.agent_discovery_schedules s
    where s.enabled and s.vertical='conference' and s.next_run_at<=v_now
      and (s.claim_expires_at is null or s.claim_expires_at<=v_now)
    order by s.next_run_at,s.id limit p_limit;
end;
$$;
alter function public.agent_list_due_discovery(integer) owner to postgres;
revoke all on function public.agent_list_due_discovery(integer) from public,anon,authenticated,service_role;
grant execute on function public.agent_list_due_discovery(integer) to service_role;

create function public.agent_claim_discovery(
  p_schedule_id uuid,p_worker_id text,p_claim_id uuid,p_lease_seconds integer)
returns setof public.agent_discovery_schedules
language plpgsql security definer set search_path=pg_catalog as $$
declare v_schedule public.agent_discovery_schedules%rowtype;
        v_query public.agent_discovery_queries%rowtype;
        v_now timestamptz; v_window uuid; v_delay integer;
begin
  if p_schedule_id is null or p_claim_id is null or nullif(btrim(p_worker_id),'') is null or
     p_lease_seconds is null or p_lease_seconds not between 1 and 86400 then
    raise exception 'invalid Discovery claim'; end if;
  -- Serialize all scheduled searches, not only matching queries: the existing
  -- Budget Guard is not an atomic reservation across distinct queries.
  perform pg_advisory_xact_lock(83161401102);
  v_now:=clock_timestamp();
  if exists (select 1 from public.agent_discovery_schedules
             where claim_expires_at>v_now) then return; end if;
  select * into v_schedule from public.agent_discovery_schedules where id=p_schedule_id for update;
  v_now:=clock_timestamp();
  if not found or not v_schedule.enabled or v_schedule.vertical<>'conference' or
     v_schedule.next_run_at>v_now or v_schedule.claim_expires_at>v_now then return; end if;
  if v_schedule.claim_id is not null then
    select * into v_query from public.agent_discovery_queries
      where schedule_id=v_schedule.id and schedule_window_id=v_schedule.current_window_id;
    if found then
      if v_query.status='SUCCEEDED' and v_query.scheduled_db_completed_at is not null then
        update public.agent_discovery_windows set status='SUCCEEDED',query_id=v_query.id,
          completed_at=v_query.scheduled_db_completed_at
          where schedule_id=v_schedule.id and window_id=v_schedule.current_window_id;
        update public.agent_discovery_schedules set last_run_at=v_query.scheduled_db_completed_at,
          last_success_at=v_query.scheduled_db_completed_at,consecutive_failures=0,
          last_outcome='SUCCEEDED',next_run_at=v_query.scheduled_db_completed_at+
            make_interval(mins=>v_schedule.interval_minutes),claim_id=null,worker_id=null,
          claimed_at=null,claim_expires_at=null,current_window_id=null,updated_at=v_now
          where id=v_schedule.id;
      elsif v_query.started_at is null and
            v_query.provenance->>'budget_blocked'='true' then
        update public.agent_discovery_windows set status='BUDGET_BLOCKED',query_id=v_query.id,
          completed_at=v_now where schedule_id=v_schedule.id and window_id=v_schedule.current_window_id;
        update public.agent_discovery_schedules set last_run_at=v_now,
          last_outcome='BUDGET_BLOCKED',next_run_at=v_now+
            make_interval(mins=>v_schedule.budget_delay_minutes),claim_id=null,worker_id=null,
          claimed_at=null,claim_expires_at=null,current_window_id=null,updated_at=v_now
          where id=v_schedule.id;
      else
        v_delay:=v_schedule.failure_backoff_minutes[least(v_schedule.consecutive_failures+1,
          array_length(v_schedule.failure_backoff_minutes,1))];
        update public.agent_discovery_windows set status='FAILED',query_id=v_query.id,completed_at=v_now
          where schedule_id=v_schedule.id and window_id=v_schedule.current_window_id;
        update public.agent_discovery_schedules set last_run_at=v_now,last_failure_at=v_now,
          consecutive_failures=consecutive_failures+1,last_outcome='FAILED',
          next_run_at=v_now+make_interval(mins=>v_delay),claim_id=null,worker_id=null,
          claimed_at=null,claim_expires_at=null,current_window_id=null,updated_at=v_now
          where id=v_schedule.id;
      end if;
      return; -- Never call Tavily twice for a window with a persisted attempt.
    end if;
  end if;
  v_window:=md5(v_schedule.id::text||'|'||
    ((extract(epoch from v_schedule.next_run_at)*1000000)::bigint)::text)::uuid;
  update public.agent_discovery_schedules set claim_id=p_claim_id,worker_id=p_worker_id,
    claimed_at=v_now,claim_expires_at=v_now+make_interval(secs=>p_lease_seconds),
    current_window_id=v_window,updated_at=v_now where id=p_schedule_id returning * into v_schedule;
  insert into public.agent_discovery_windows(schedule_id,window_id,claim_id,status,claimed_at)
    values (p_schedule_id,v_window,p_claim_id,'CLAIMED',v_now)
    on conflict (schedule_id,window_id) do update set claim_id=excluded.claim_id,
      claimed_at=excluded.claimed_at where agent_discovery_windows.status='CLAIMED'
      and agent_discovery_windows.query_id is null;
  if not found then raise exception 'Discovery window already terminal or attempted'; end if;
  return next v_schedule;
end;
$$;
alter function public.agent_claim_discovery(uuid,text,uuid,integer) owner to postgres;
revoke all on function public.agent_claim_discovery(uuid,text,uuid,integer)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_claim_discovery(uuid,text,uuid,integer) to service_role;

create function public.agent_finish_discovery(
  p_schedule_id uuid,p_claim_id uuid,p_window_id uuid,p_outcome text,p_query_id uuid,
  p_backoff_minutes integer[],p_budget_delay_minutes integer)
returns text language plpgsql security definer set search_path=pg_catalog as $$
declare v_schedule public.agent_discovery_schedules%rowtype;
        v_window public.agent_discovery_windows%rowtype;
        v_query public.agent_discovery_queries%rowtype;
        v_now timestamptz; v_outcome text; v_delay integer;
begin
  if p_schedule_id is null or p_claim_id is null or p_window_id is null or
     p_outcome is null or p_outcome not in ('SUCCEEDED','FAILED','BUDGET_BLOCKED') or
     p_backoff_minutes is null or
     not coalesce(array_length(p_backoff_minutes,1) between 1 and 8,false) or
     exists(select 1 from unnest(p_backoff_minutes) x where x is null or x<1) or
     p_budget_delay_minutes is null or p_budget_delay_minutes<1 then
    raise exception 'invalid Discovery completion'; end if;
  select * into v_schedule from public.agent_discovery_schedules where id=p_schedule_id for update;
  select * into v_window from public.agent_discovery_windows
    where schedule_id=p_schedule_id and window_id=p_window_id for update;
  if not found or v_window.claim_id is distinct from p_claim_id then
    raise exception 'Discovery window/claim mismatch'; end if;
  if v_window.status<>'CLAIMED' then
    if p_query_id is not null and p_query_id is distinct from v_window.query_id then
      raise exception 'Discovery completion query mismatch'; end if;
    return v_window.status;
  end if;
  v_now:=clock_timestamp();
  if v_schedule.claim_id is distinct from p_claim_id or
     v_schedule.current_window_id is distinct from p_window_id or
     v_schedule.claim_expires_at<=v_now then raise exception 'stale Discovery claim'; end if;
  select * into v_query from public.agent_discovery_queries
    where schedule_id=p_schedule_id and schedule_window_id=p_window_id;
  if p_query_id is not null and (not found or v_query.id<>p_query_id) then
    raise exception 'Discovery completion query mismatch'; end if;
  if found and v_query.status='SUCCEEDED' and v_query.scheduled_db_completed_at is not null then
    v_outcome:='SUCCEEDED';
  else
    if p_outcome='SUCCEEDED' then raise exception 'successful Discovery query not persisted'; end if;
    v_outcome:=p_outcome;
  end if;
  if v_outcome='BUDGET_BLOCKED' and
     (v_query.id is null or v_query.started_at is not null or
      v_query.provenance->>'budget_blocked' is distinct from 'true') then
    raise exception 'budget block evidence missing'; end if;
  if v_outcome='SUCCEEDED' then
    update public.agent_discovery_schedules set last_run_at=v_query.scheduled_db_completed_at,
      last_success_at=v_query.scheduled_db_completed_at,consecutive_failures=0,
      last_outcome='SUCCEEDED',next_run_at=v_query.scheduled_db_completed_at+
        make_interval(mins=>v_schedule.interval_minutes),claim_id=null,worker_id=null,
      claimed_at=null,claim_expires_at=null,current_window_id=null,updated_at=v_now
      where id=p_schedule_id;
  elsif v_outcome='BUDGET_BLOCKED' then
    update public.agent_discovery_schedules set last_run_at=v_now,last_outcome='BUDGET_BLOCKED',
      next_run_at=v_now+make_interval(mins=>p_budget_delay_minutes),claim_id=null,
      worker_id=null,claimed_at=null,claim_expires_at=null,current_window_id=null,updated_at=v_now
      where id=p_schedule_id;
  else
    v_delay:=p_backoff_minutes[least(v_schedule.consecutive_failures+1,array_length(p_backoff_minutes,1))];
    update public.agent_discovery_schedules set last_run_at=v_now,last_failure_at=v_now,
      consecutive_failures=consecutive_failures+1,last_outcome='FAILED',
      next_run_at=v_now+make_interval(mins=>v_delay),claim_id=null,worker_id=null,
      claimed_at=null,claim_expires_at=null,current_window_id=null,updated_at=v_now
      where id=p_schedule_id;
  end if;
  update public.agent_discovery_windows set status=v_outcome,
    query_id=case when v_query.id is not null then v_query.id else query_id end,
    completed_at=v_now where schedule_id=p_schedule_id and window_id=p_window_id;
  return v_outcome;
end;
$$;
alter function public.agent_finish_discovery(uuid,uuid,uuid,text,uuid,integer[],integer) owner to postgres;
revoke all on function public.agent_finish_discovery(uuid,uuid,uuid,text,uuid,integer[],integer)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_finish_discovery(uuid,uuid,uuid,text,uuid,integer[],integer)
  to service_role;

commit;
