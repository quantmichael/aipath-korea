-- PHASE 12B draft, requires Agent 001-010. Apply only after isolated tests.
-- No existing rows, Source approvals, schedules, or Service tables are changed.
begin;

create table public.agent_provider_budget_reservations (
  id uuid primary key default gen_random_uuid(),
  kind text not null check (kind in ('openai','tavily')),
  scope text not null check (scope in ('planner','extraction','discovery')),
  idempotency_key text not null unique check (length(idempotency_key) between 8 and 200),
  query_key text,
  status text not null default 'RESERVED'
    check (status in ('RESERVED','DISPATCHED','SUCCEEDED','FAILED','UNKNOWN')),
  reserved_at timestamptz not null default clock_timestamp(),
  dispatched_at timestamptz,
  completed_at timestamptz,
  error_code text,
  constraint agent_budget_kind_scope check (
    (kind='openai' and scope in ('planner','extraction')) or
    (kind='tavily' and scope='discovery')),
  constraint agent_budget_query check (
    (kind='tavily' and length(query_key) between 3 and 200) or
    (kind='openai' and query_key is null)),
  constraint agent_budget_times check (
    (dispatched_at is null or dispatched_at>=reserved_at) and
    (completed_at is null or (dispatched_at is not null and completed_at>=dispatched_at)))
);
create index agent_provider_budget_kind_time_idx
  on public.agent_provider_budget_reservations(kind,reserved_at);
create index agent_provider_budget_cooldown_idx
  on public.agent_provider_budget_reservations(kind,query_key,reserved_at)
  where query_key is not null;

create table public.agent_discovery_ai_plans (
  id uuid primary key default gen_random_uuid(),
  planning_date date not null unique,
  reservation_id uuid not null unique
    references public.agent_provider_budget_reservations(id),
  status text not null check (status in ('GENERATING','READY','HELD','FAILED')),
  model text not null,
  context_digest text not null check (context_digest ~ '^[0-9a-f]{64}$'),
  queries jsonb not null default '[]'::jsonb check (jsonb_typeof(queries)='array'),
  blocked_reasons jsonb not null default '[]'::jsonb
    check (jsonb_typeof(blocked_reasons)='array'),
  error_code text,
  created_at timestamptz not null default clock_timestamp(),
  completed_at timestamptz
);

create table public.agent_discovery_ai_plan_queries (
  id uuid primary key default gen_random_uuid(),
  plan_id uuid not null references public.agent_discovery_ai_plans(id),
  ordinal integer not null check (ordinal between 1 and 3),
  query_text text not null check (length(query_text) between 8 and 120),
  vertical_hint text not null check (vertical_hint in
    ('conference','exhibition','competition','hackathon','education','research',
     'grant','program','job','meetup','unknown')),
  language text not null check (language in ('ko','en')),
  priority integer not null check (priority between 1 and 5),
  reason text not null check (length(reason) between 5 and 200),
  new_category text,
  status text not null default 'READY' check (status in ('READY','CLAIMED','SUCCEEDED','FAILED')),
  claim_id uuid unique,
  discovery_query_id uuid references public.agent_discovery_queries(id),
  completed_at timestamptz,
  unique(plan_id,ordinal),
  unique(plan_id,query_text)
);
create index agent_discovery_ai_plan_due_idx on public.agent_discovery_ai_plan_queries
  (status,priority,id);

alter table public.agent_provider_budget_reservations enable row level security;
alter table public.agent_discovery_ai_plans enable row level security;
alter table public.agent_discovery_ai_plan_queries enable row level security;
revoke all on public.agent_provider_budget_reservations,
  public.agent_discovery_ai_plans,public.agent_discovery_ai_plan_queries
  from public,anon,authenticated,service_role;
grant select on public.agent_provider_budget_reservations,
  public.agent_discovery_ai_plans,public.agent_discovery_ai_plan_queries to service_role;

-- A transaction-scoped advisory lock protects all budget kinds, including the
-- legacy log/query union during an image rollout. No caller-supplied clock/limit.
create function public.agent_reserve_provider_budget(
  p_kind text,p_scope text,p_key text,p_query_text text default null)
returns uuid language plpgsql security definer set search_path=pg_catalog as $$
declare v_now timestamptz; v_day timestamptz; v_month timestamptz;
        v_daily integer; v_monthly integer; v_scope_daily integer;
        v_scope_monthly integer; v_query_key text; v_id uuid;
begin
  if p_key is null or length(p_key) not between 8 and 200 or
     not ((p_kind='openai' and p_scope in ('planner','extraction') and p_query_text is null) or
          (p_kind='tavily' and p_scope='discovery' and
           p_query_text is not null and length(btrim(p_query_text)) between 3 and 200)) then
    raise exception 'invalid budget reservation';
  end if;
  perform pg_advisory_xact_lock(83161401103);
  v_now:=clock_timestamp();
  v_day:=(date_trunc('day',v_now at time zone 'Asia/Seoul')
          at time zone 'Asia/Seoul');
  v_month:=(date_trunc('month',v_now at time zone 'Asia/Seoul')
            at time zone 'Asia/Seoul');
  if exists (select 1 from public.agent_provider_budget_reservations
             where idempotency_key=p_key) then
    return null;
  end if;
  if p_kind='tavily' then
    v_query_key:=lower(regexp_replace(btrim(p_query_text),'[[:space:]]+',' ','g'));
    select count(*) into v_daily from (
      select id::text from public.agent_provider_budget_reservations
        where kind='tavily' and reserved_at>=v_day
      union all
      select id::text from public.agent_discovery_queries
        where provider='tavily' and started_at>=v_day
          and provenance->>'budget_reservation_id' is null) q;
    select count(*) into v_monthly from (
      select id::text from public.agent_provider_budget_reservations
        where kind='tavily' and reserved_at>=v_month
      union all
      select id::text from public.agent_discovery_queries
        where provider='tavily' and started_at>=v_month
          and provenance->>'budget_reservation_id' is null) q;
    if v_daily>=30 or v_monthly>=1000 then
      raise exception 'tavily budget exhausted';
    end if;
    if exists (select 1 from public.agent_provider_budget_reservations
               where kind='tavily' and query_key=v_query_key
                 and reserved_at>v_now-interval '24 hours') or
       exists (select 1 from public.agent_discovery_queries
               where provider='tavily' and started_at>v_now-interval '24 hours'
                 and provenance->>'budget_reservation_id' is null
                 and lower(regexp_replace(btrim(query_text),'[[:space:]]+',' ','g'))=v_query_key) then
      raise exception 'query cooldown active';
    end if;
  else
    select count(*) into v_daily from (
      select id::text from public.agent_provider_budget_reservations
        where kind='openai' and reserved_at>=v_day
      union all
      select id::text from public.agent_logs
        where event_type='EXTRACTION_LLM_ATTEMPT' and created_at>=v_day
          and details->>'budget_reservation_id' is null) q;
    select count(*) into v_monthly from (
      select id::text from public.agent_provider_budget_reservations
        where kind='openai' and reserved_at>=v_month
      union all
      select id::text from public.agent_logs
        where event_type='EXTRACTION_LLM_ATTEMPT' and created_at>=v_month
          and details->>'budget_reservation_id' is null) q;
    if v_daily>=5 or v_monthly>=100 then
      raise exception 'openai shared budget exhausted';
    end if;
    if p_scope='planner' then
      select count(*) into v_scope_daily from public.agent_provider_budget_reservations
        where kind='openai' and scope='planner' and reserved_at>=v_day;
      select count(*) into v_scope_monthly from public.agent_provider_budget_reservations
        where kind='openai' and scope='planner' and reserved_at>=v_month;
      if v_scope_daily>=1 or v_scope_monthly>=25 then
        raise exception 'planner budget exhausted';
      end if;
    end if;
  end if;
  insert into public.agent_provider_budget_reservations
    (kind,scope,idempotency_key,query_key,reserved_at)
    values(p_kind,p_scope,p_key,v_query_key,v_now) returning id into v_id;
  return v_id;
end;
$$;
alter function public.agent_reserve_provider_budget(text,text,text,text) owner to postgres;
revoke all on function public.agent_reserve_provider_budget(text,text,text,text)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_reserve_provider_budget(text,text,text,text)
  to service_role;

create function public.agent_dispatch_provider_budget(p_id uuid)
returns boolean language plpgsql security definer set search_path=pg_catalog as $$
begin
  update public.agent_provider_budget_reservations
    set status='DISPATCHED',dispatched_at=clock_timestamp()
    where id=p_id and status='RESERVED'
      and reserved_at>=clock_timestamp()-interval '60 seconds'
      and (reserved_at at time zone 'Asia/Seoul')::date=
          (clock_timestamp() at time zone 'Asia/Seoul')::date;
  return found;
end;
$$;
alter function public.agent_dispatch_provider_budget(uuid) owner to postgres;
revoke all on function public.agent_dispatch_provider_budget(uuid)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_dispatch_provider_budget(uuid) to service_role;

create function public.agent_finish_provider_budget(p_id uuid,p_status text,p_error_code text default null)
returns boolean language plpgsql security definer set search_path=pg_catalog as $$
declare v_previous text;
begin
  if p_status not in ('SUCCEEDED','FAILED','UNKNOWN') or
     (p_error_code is not null and p_error_code !~ '^[A-Z0-9_]{1,80}$') then
    raise exception 'invalid budget completion';
  end if;
  select status into v_previous from public.agent_provider_budget_reservations
    where id=p_id for update;
  if v_previous=p_status then return true; end if;
  if v_previous is distinct from 'DISPATCHED' then return false; end if;
  update public.agent_provider_budget_reservations
    set status=p_status,error_code=p_error_code,completed_at=clock_timestamp()
    where id=p_id;
  return true;
end;
$$;
alter function public.agent_finish_provider_budget(uuid,text,text) owner to postgres;
revoke all on function public.agent_finish_provider_budget(uuid,text,text)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_finish_provider_budget(uuid,text,text)
  to service_role;

create function public.agent_provider_budget_usage(p_kind text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare v_now timestamptz:=clock_timestamp(); v_day timestamptz; v_month timestamptz;
        v_daily integer; v_monthly integer;
begin
  if p_kind not in ('tavily','openai') then raise exception 'invalid budget kind'; end if;
  v_day:=(date_trunc('day',v_now at time zone 'Asia/Seoul') at time zone 'Asia/Seoul');
  v_month:=(date_trunc('month',v_now at time zone 'Asia/Seoul') at time zone 'Asia/Seoul');
  if p_kind='tavily' then
    select count(*) into v_daily from (
      select id::text from public.agent_provider_budget_reservations
        where kind='tavily' and reserved_at>=v_day
      union all select id::text from public.agent_discovery_queries
        where provider='tavily' and started_at>=v_day
          and provenance->>'budget_reservation_id' is null) q;
    select count(*) into v_monthly from (
      select id::text from public.agent_provider_budget_reservations
        where kind='tavily' and reserved_at>=v_month
      union all select id::text from public.agent_discovery_queries
        where provider='tavily' and started_at>=v_month
          and provenance->>'budget_reservation_id' is null) q;
  else
    select count(*) into v_daily from (
      select id::text from public.agent_provider_budget_reservations
        where kind='openai' and reserved_at>=v_day
      union all select id::text from public.agent_logs
        where event_type='EXTRACTION_LLM_ATTEMPT' and created_at>=v_day
          and details->>'budget_reservation_id' is null) q;
    select count(*) into v_monthly from (
      select id::text from public.agent_provider_budget_reservations
        where kind='openai' and reserved_at>=v_month
      union all select id::text from public.agent_logs
        where event_type='EXTRACTION_LLM_ATTEMPT' and created_at>=v_month
          and details->>'budget_reservation_id' is null) q;
  end if;
  return jsonb_build_object('daily',v_daily,'monthly',v_monthly);
end;
$$;
alter function public.agent_provider_budget_usage(text) owner to postgres;
revoke all on function public.agent_provider_budget_usage(text) from public,anon,authenticated,service_role;
grant execute on function public.agent_provider_budget_usage(text) to service_role;

create function public.agent_start_ai_plan(
  p_day date,p_reservation_id uuid,p_model text,p_context_digest text)
returns uuid language plpgsql security definer set search_path=pg_catalog as $$
declare v_id uuid;
begin
  if p_day is distinct from (clock_timestamp() at time zone 'Asia/Seoul')::date or
     p_model is null or length(p_model) not between 1 and 100 or
     p_context_digest !~ '^[0-9a-f]{64}$' or
     not exists (select 1 from public.agent_provider_budget_reservations
                 where id=p_reservation_id and kind='openai' and scope='planner'
                   and status='DISPATCHED') then
    raise exception 'invalid AI plan start';
  end if;
  insert into public.agent_discovery_ai_plans
    (planning_date,reservation_id,status,model,context_digest)
    values(p_day,p_reservation_id,'GENERATING',p_model,p_context_digest)
    returning id into v_id;
  return v_id;
end;
$$;
alter function public.agent_start_ai_plan(date,uuid,text,text) owner to postgres;
revoke all on function public.agent_start_ai_plan(date,uuid,text,text)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_start_ai_plan(date,uuid,text,text) to service_role;

create function public.agent_finish_ai_plan(
  p_plan_id uuid,p_queries jsonb,p_error_code text default null,
  p_blocked_reasons jsonb default '[]'::jsonb)
returns boolean language plpgsql security definer set search_path=pg_catalog as $$
declare v_plan public.agent_discovery_ai_plans%rowtype;
begin
  select * into v_plan from public.agent_discovery_ai_plans where id=p_plan_id for update;
  if not found or v_plan.status<>'GENERATING' or
     p_queries is null or jsonb_typeof(p_queries)<>'array' or
     p_blocked_reasons is null or jsonb_typeof(p_blocked_reasons)<>'array' or
     jsonb_array_length(p_blocked_reasons)>10 or
     jsonb_array_length(p_queries)>3 or
     (p_error_code is null and jsonb_array_length(p_queries)=0) or
     (p_error_code is not null and (p_error_code !~ '^[A-Z0-9_]{1,80}$' or
                                    jsonb_array_length(p_queries)<>0)) then
    return false;
  end if;
  if p_error_code is null then
    insert into public.agent_discovery_ai_plan_queries
      (plan_id,ordinal,query_text,vertical_hint,language,priority,reason,new_category)
    select p_plan_id,ordinality,(item->>'query'),(item->>'category'),
      (item->>'language'),(item->>'priority')::integer,(item->>'reason'),
      item->>'new_category'
    from jsonb_array_elements(p_queries) with ordinality as e(item,ordinality);
  end if;
  update public.agent_discovery_ai_plans set status=case
    when p_error_code is null then 'READY'
    when p_error_code='NO_SAFE_QUERIES' then 'HELD'
    else 'FAILED' end,
    queries=p_queries,blocked_reasons=p_blocked_reasons,error_code=p_error_code,
    completed_at=clock_timestamp()
    where id=p_plan_id;
  return true;
end;
$$;
alter function public.agent_finish_ai_plan(uuid,jsonb,text,jsonb) owner to postgres;
revoke all on function public.agent_finish_ai_plan(uuid,jsonb,text,jsonb)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_finish_ai_plan(uuid,jsonb,text,jsonb) to service_role;

create function public.agent_claim_ai_plan_query(p_claim_id uuid)
returns setof public.agent_discovery_ai_plan_queries
language plpgsql security definer set search_path=pg_catalog as $$
declare v_id uuid;
begin
  if p_claim_id is null then raise exception 'invalid plan query claim'; end if;
  select q.id into v_id from public.agent_discovery_ai_plan_queries q
    join public.agent_discovery_ai_plans p on p.id=q.plan_id
    -- A plan is never searched in the same 30-minute automation cycle.
    where q.status='READY' and p.status='READY'
      and p.completed_at<=clock_timestamp()-interval '30 minutes'
    order by p.planning_date desc,q.priority,q.id
    for update of q skip locked limit 1;
  if v_id is null then return; end if;
  return query update public.agent_discovery_ai_plan_queries
    set status='CLAIMED',claim_id=p_claim_id where id=v_id returning *;
end;
$$;
alter function public.agent_claim_ai_plan_query(uuid) owner to postgres;
revoke all on function public.agent_claim_ai_plan_query(uuid)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_claim_ai_plan_query(uuid) to service_role;

create function public.agent_finish_ai_plan_query(
  p_claim_id uuid,p_status text,p_query_id uuid default null)
returns boolean language plpgsql security definer set search_path=pg_catalog as $$
begin
  if p_claim_id is null or p_status not in ('SUCCEEDED','FAILED') or
     (p_status='SUCCEEDED' and p_query_id is null) then return false; end if;
  update public.agent_discovery_ai_plan_queries
    set status=p_status,discovery_query_id=p_query_id,completed_at=clock_timestamp()
    where claim_id=p_claim_id and status='CLAIMED';
  return found;
end;
$$;
alter function public.agent_finish_ai_plan_query(uuid,text,uuid) owner to postgres;
revoke all on function public.agent_finish_ai_plan_query(uuid,text,uuid)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_finish_ai_plan_query(uuid,text,uuid) to service_role;

commit;
