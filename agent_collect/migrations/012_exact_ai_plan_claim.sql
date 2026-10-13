-- PHASE 12E.1: exact-plan claim isolation. Apply only after separate approval.
-- Agent 001-011 must already exist. No existing Plan/Query row is rewritten.
begin;

alter table public.agent_discovery_ai_plans
  add column active_claim_id uuid unique,
  add column active_claimed_at timestamptz,
  add constraint agent_ai_plan_active_claim_consistent check (
    (active_claim_id is null and active_claimed_at is null) or
    (active_claim_id is not null and active_claimed_at is not null));

-- The trigger covers both the new scoped RPC and the original unscoped RPC.
-- It also releases the plan lock through the unchanged finish RPC.
create function public.agent_ai_plan_claim_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog as $$
declare v_active uuid;
begin
  if new.id is distinct from old.id or new.plan_id is distinct from old.plan_id then
    raise exception 'plan query identity cannot change';
  end if;
  if old.status='READY' and new.status='CLAIMED' then
    if new.claim_id is null or old.claim_id is not null then
      raise exception 'invalid plan query claim';
    end if;
    select p.active_claim_id into v_active
      from public.agent_discovery_ai_plans p where p.id=new.plan_id for update;
    if not found or v_active is not null or exists (
      select 1 from public.agent_discovery_ai_plan_queries q
        where q.plan_id=new.plan_id and q.id<>new.id and q.status='CLAIMED') then
      raise exception 'plan already claimed';
    end if;
    update public.agent_discovery_ai_plans
      set active_claim_id=new.claim_id,active_claimed_at=clock_timestamp()
      where id=new.plan_id;
  elsif old.status='CLAIMED' and new.status in ('SUCCEEDED','FAILED') then
    if new.claim_id is distinct from old.claim_id then
      raise exception 'plan query claim identity cannot change';
    end if;
    select p.active_claim_id into v_active
      from public.agent_discovery_ai_plans p where p.id=old.plan_id for update;
    if not found or (v_active is not null and v_active<>old.claim_id) then
      raise exception 'plan claim owner mismatch';
    end if;
    -- A claim made before this migration has no plan-level claim to release.
    if v_active is not null then
      update public.agent_discovery_ai_plans
        set active_claim_id=null,active_claimed_at=null where id=old.plan_id;
    end if;
  elsif new.status is distinct from old.status or
        new.claim_id is distinct from old.claim_id then
    raise exception 'invalid plan query transition';
  end if;
  return new;
end;
$$;
alter function public.agent_ai_plan_claim_guard() owner to postgres;
revoke all on function public.agent_ai_plan_claim_guard()
  from public,anon,authenticated,service_role;
create trigger agent_ai_plan_claim_guard_trigger
  before update of status,claim_id on public.agent_discovery_ai_plan_queries
  for each row execute function public.agent_ai_plan_claim_guard();

-- Preserve the original signature and global selection policy while making it
-- honor the same per-plan lock. Existing Cloud Run callers need no code change.
create or replace function public.agent_claim_ai_plan_query(p_claim_id uuid)
returns setof public.agent_discovery_ai_plan_queries
language plpgsql security definer set search_path=pg_catalog as $$
declare v_id uuid;
begin
  if p_claim_id is null then raise exception 'invalid plan query claim'; end if;
  select q.id into v_id from public.agent_discovery_ai_plan_queries q
    join public.agent_discovery_ai_plans p on p.id=q.plan_id
    where q.status='READY' and p.status='READY' and p.active_claim_id is null
      and p.completed_at<=clock_timestamp()-interval '30 minutes'
      and not exists (select 1 from public.agent_discovery_ai_plan_queries other
                      where other.plan_id=p.id and other.status='CLAIMED')
    order by p.planning_date desc,q.priority,q.id
    for update of p,q skip locked limit 1;
  if v_id is null then return; end if;
  return query update public.agent_discovery_ai_plan_queries
    set status='CLAIMED',claim_id=p_claim_id where id=v_id returning *;
end;
$$;
alter function public.agent_claim_ai_plan_query(uuid) owner to postgres;
revoke all on function public.agent_claim_ai_plan_query(uuid)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_claim_ai_plan_query(uuid) to service_role;

create function public.agent_claim_ai_plan_query_exact(
  p_plan_id uuid,p_claim_id uuid)
returns setof public.agent_discovery_ai_plan_queries
language plpgsql security definer set search_path=pg_catalog as $$
declare v_id uuid;
begin
  if p_plan_id is null or p_claim_id is null then
    raise exception 'invalid exact plan claim';
  end if;
  select q.id into v_id from public.agent_discovery_ai_plan_queries q
    join public.agent_discovery_ai_plans p on p.id=q.plan_id
    where p.id=p_plan_id and q.status='READY' and p.status='READY'
      and p.active_claim_id is null
      and p.completed_at<=clock_timestamp()-interval '30 minutes'
      and not exists (select 1 from public.agent_discovery_ai_plan_queries other
                      where other.plan_id=p.id and other.status='CLAIMED')
    order by q.priority,q.id
    for update of p,q skip locked limit 1;
  if v_id is null then return; end if;
  return query update public.agent_discovery_ai_plan_queries
    set status='CLAIMED',claim_id=p_claim_id where id=v_id returning *;
end;
$$;
alter function public.agent_claim_ai_plan_query_exact(uuid,uuid) owner to postgres;
revoke all on function public.agent_claim_ai_plan_query_exact(uuid,uuid)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_claim_ai_plan_query_exact(uuid,uuid)
  to service_role;

commit;
