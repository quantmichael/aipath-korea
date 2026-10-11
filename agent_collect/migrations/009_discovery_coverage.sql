-- PHASE 11B: broaden reviewed Discovery schedules. NOT APPLIED to Production.
-- Requires Agent migrations 001-008. Does not enable schedules or auto approval.
begin;

alter table public.agent_discovery_schedules
  drop constraint agent_discovery_schedules_vertical_check;
alter table public.agent_discovery_schedules
  add constraint agent_discovery_schedules_vertical_check
  check (vertical in ('conference','competition','hackathon','education',
                     'research','grant','job'));

create or replace function public.agent_discovery_query_guard()
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
     new.vertical_hint<>v_schedule.vertical or new.query_text<>v_schedule.query_text then
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

create or replace function public.agent_list_due_discovery(p_limit integer)
returns setof public.agent_discovery_schedules
language plpgsql security definer set search_path=pg_catalog as $$
declare v_now timestamptz:=clock_timestamp();
begin
  if p_limit is null or p_limit not between 1 and 100 then raise exception 'invalid Discovery limit'; end if;
  return query select s.* from public.agent_discovery_schedules s
    where s.enabled and s.next_run_at<=v_now
      and (s.claim_expires_at is null or s.claim_expires_at<=v_now)
    order by s.next_run_at,s.id limit p_limit;
end;
$$;

create or replace function public.agent_claim_discovery(
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
  if not found or not v_schedule.enabled or v_schedule.next_run_at>v_now or v_schedule.claim_expires_at>v_now then return; end if;
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

-- Keep the old six-argument registration RPC for Conference callers.
-- All other verticals require the same human-reviewed, initially-disabled path.
create function public.agent_register_discovery_schedule_vertical(
  p_query_text text,p_language text,p_country text,p_interval_minutes integer,
  p_reviewer text,p_reason text,p_vertical text)
returns public.agent_discovery_schedules
language plpgsql security definer set search_path=pg_catalog as $$
declare v_schedule public.agent_discovery_schedules%rowtype;
begin
  if nullif(btrim(p_query_text),'') is null or p_language !~ '^[a-z]{2}$' or
     p_country !~ '^[A-Z]{2}$' or p_interval_minutes is null or
     p_interval_minutes<1440 or nullif(btrim(p_reviewer),'') is null or
     nullif(btrim(p_reason),'') is null or p_vertical is null or
     p_vertical not in ('conference','competition','hackathon','education',
                        'research','grant','job') then
    raise exception 'reviewed AI opportunity query registration required';
  end if;
  insert into public.agent_discovery_schedules(
    query_text,vertical,language,country,interval_minutes,enabled,
    reviewed_by,review_reason,reviewed_at)
  values (p_query_text,p_vertical,p_language,p_country,p_interval_minutes,false,
          p_reviewer,p_reason,clock_timestamp()) returning * into v_schedule;
  return v_schedule;
end;
$$;
alter function public.agent_register_discovery_schedule_vertical(
  text,text,text,integer,text,text,text) owner to postgres;
revoke all on function public.agent_register_discovery_schedule_vertical(
  text,text,text,integer,text,text,text) from public,anon,authenticated,service_role;
grant execute on function public.agent_register_discovery_schedule_vertical(
  text,text,text,integer,text,text,text) to service_role;

commit;
