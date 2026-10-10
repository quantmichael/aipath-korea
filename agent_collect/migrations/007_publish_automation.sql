-- PHASE 9 Agent-only publish/automation leases. CREATE ONLY; never auto-apply.
-- Requires migrations 001-006. Does not alter Service tables or historical rows.
begin;

alter table public.agent_publish_queue
  add column claim_id uuid,
  add column worker_id text,
  add column claimed_at timestamptz,
  add column claim_expires_at timestamptz,
  add column next_attempt_at timestamptz not null default now();
alter table public.agent_publish_queue add constraint agent_publish_claim_consistent check (
  (claim_id is null and worker_id is null and claimed_at is null and claim_expires_at is null)
  or (claim_id is not null and nullif(btrim(worker_id),'') is not null and
      claimed_at is not null and claim_expires_at > claimed_at));
-- Quality Gate may INSERT a READY snapshot; all later mutations use the
-- postgres-owned claim/completion RPCs, not service_role table UPDATE.
revoke all on public.agent_publish_queue from public,anon,authenticated,service_role;
grant select,insert on public.agent_publish_queue to service_role;
create function public.agent_publish_insert_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog as $$
begin
  if new.status<>'READY' or new.contract_version<>1 or new.attempt_count<>0 or
     new.claim_id is not null or new.worker_id is not null or
     new.claimed_at is not null or new.claim_expires_at is not null or
     new.service_opportunity_id is not null or new.published_at is not null or
     new.publish_candidate->>'agent_item_id' is distinct from new.candidate_id::text or
     not exists (select 1 from public.agent_verifications v
                 where v.id=new.verification_id and v.candidate_id=new.candidate_id
                   and v.result='VERIFIED'
                   and v.checks #>> '{gate_decision,status}'='PUBLISH_READY') then
    raise exception 'only verified READY publish snapshots may be inserted';
  end if;
  return new;
end;
$$;
alter function public.agent_publish_insert_guard() owner to postgres;
revoke all on function public.agent_publish_insert_guard()
  from public,anon,authenticated,service_role;
create trigger agent_publish_insert_guard_trigger
  before insert on public.agent_publish_queue
  for each row execute function public.agent_publish_insert_guard();
create unique index agent_publish_claim_unique on public.agent_publish_queue(claim_id)
  where claim_id is not null;
create index agent_publish_due_v2 on public.agent_publish_queue(next_attempt_at,id)
  where status in ('READY','FAILED','PUBLISHING');

create table public.agent_runtime_leases (
  name text primary key check (name in ('publish','automation')),
  claim_id uuid,
  worker_id text,
  claimed_at timestamptz,
  expires_at timestamptz,
  constraint agent_runtime_lease_consistent check (
    (claim_id is null and worker_id is null and claimed_at is null and expires_at is null)
    or (claim_id is not null and nullif(btrim(worker_id),'') is not null and
        claimed_at is not null and expires_at > claimed_at))
);
alter table public.agent_runtime_leases enable row level security;
revoke all on public.agent_runtime_leases from public,anon,authenticated,service_role;
grant select on public.agent_runtime_leases to service_role;

create table public.agent_publish_completions (
  claim_id uuid primary key,
  queue_id uuid not null references public.agent_publish_queue(id),
  outcome text not null check (outcome in ('PUBLISHED','HELD','FAILED')),
  service_opportunity_id uuid,
  completed_at timestamptz not null
);
alter table public.agent_publish_completions enable row level security;
revoke all on public.agent_publish_completions from public,anon,authenticated,service_role;
grant select on public.agent_publish_completions to service_role;

create function public.agent_list_due_publish(p_limit integer)
returns setof public.agent_publish_queue
language plpgsql security definer set search_path=pg_catalog as $$
declare v_now timestamptz := clock_timestamp();
begin
  if p_limit is null or p_limit not between 1 and 100 then
    raise exception 'invalid publish limit';
  end if;
  return query select q.* from public.agent_publish_queue q
    join public.agent_verifications v on v.id=q.verification_id
      and v.candidate_id=q.candidate_id
    where q.contract_version=1 and v.result='VERIFIED'
      and v.checks #>> '{gate_decision,status}'='PUBLISH_READY'
      and q.publish_candidate->>'agent_item_id'=q.candidate_id::text
      and q.next_attempt_at<=v_now
      and (q.status in ('READY','FAILED') or
           (q.status='PUBLISHING' and q.claim_expires_at<=v_now))
    order by q.next_attempt_at,q.id limit p_limit;
end;
$$;
alter function public.agent_list_due_publish(integer) owner to postgres;
revoke all on function public.agent_list_due_publish(integer) from public,anon,authenticated,service_role;
grant execute on function public.agent_list_due_publish(integer) to service_role;

create function public.agent_claim_publish(
  p_queue_id uuid,p_worker_id text,p_claim_id uuid,p_lease_seconds integer)
returns table(job_id uuid,candidate_id uuid,claim_id uuid,worker_id text,claim_expires_at timestamptz)
language plpgsql security definer set search_path=pg_catalog as $$
declare v_lock public.agent_runtime_leases%rowtype;
        v_queue public.agent_publish_queue%rowtype;
        v_now timestamptz;
begin
  if p_queue_id is null or p_claim_id is null or nullif(btrim(p_worker_id),'') is null or
     p_lease_seconds is null or p_lease_seconds not between 1 and 86400 then
    raise exception 'invalid publish claim';
  end if;
  insert into public.agent_runtime_leases(name) values ('publish') on conflict do nothing;
  select * into v_lock from public.agent_runtime_leases where name='publish' for update;
  v_now:=clock_timestamp();
  if v_lock.expires_at>v_now then return; end if;
  select * into v_queue from public.agent_publish_queue where id=p_queue_id for update;
  if not found or v_queue.contract_version<>1 or v_queue.next_attempt_at>v_now or
     not (v_queue.status in ('READY','FAILED') or
          (v_queue.status='PUBLISHING' and v_queue.claim_expires_at<=v_now)) or
     v_queue.publish_candidate->>'agent_item_id' is distinct from v_queue.candidate_id::text or
     not exists (select 1 from public.agent_verifications v
                 where v.id=v_queue.verification_id and v.candidate_id=v_queue.candidate_id
                   and v.result='VERIFIED'
                   and v.checks #>> '{gate_decision,status}'='PUBLISH_READY') then
    return;
  end if;
  update public.agent_runtime_leases set claim_id=p_claim_id,worker_id=p_worker_id,
    claimed_at=v_now,expires_at=v_now+make_interval(secs=>p_lease_seconds)
    where name='publish';
  update public.agent_publish_queue set status='PUBLISHING',claim_id=p_claim_id,
    worker_id=p_worker_id,claimed_at=v_now,
    claim_expires_at=v_now+make_interval(secs=>p_lease_seconds),
    attempt_count=attempt_count+1,attempted_at=v_now,updated_at=v_now
    where id=p_queue_id;
  return query select v_queue.id,v_queue.candidate_id,p_claim_id,p_worker_id,
    v_now+make_interval(secs=>p_lease_seconds);
end;
$$;
alter function public.agent_claim_publish(uuid,text,uuid,integer) owner to postgres;
revoke all on function public.agent_claim_publish(uuid,text,uuid,integer)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_claim_publish(uuid,text,uuid,integer) to service_role;

create function public.agent_finish_publish(
  p_queue_id uuid,p_claim_id uuid,p_outcome text,p_service_id uuid,
  p_slug text,p_service_status text,p_published_at timestamptz,p_error_code text)
returns text language plpgsql security definer set search_path=pg_catalog as $$
declare v_queue public.agent_publish_queue%rowtype;
        v_prior public.agent_publish_completions%rowtype;
        v_now timestamptz;
begin
  if p_queue_id is null or p_claim_id is null or
     p_outcome not in ('PUBLISHED','HELD','FAILED') then
    raise exception 'invalid publish completion';
  end if;
  select * into v_queue from public.agent_publish_queue where id=p_queue_id for update;
  if not found then raise exception 'publish queue missing'; end if;
  select * into v_prior from public.agent_publish_completions where claim_id=p_claim_id;
  if found then
    if v_prior.queue_id<>p_queue_id or
       (p_service_id is not null and p_service_id is distinct from v_prior.service_opportunity_id) then
      raise exception 'publish completion mismatch';
    end if;
    return v_prior.outcome;
  end if;
  v_now:=clock_timestamp();
  if v_queue.status<>'PUBLISHING' or v_queue.claim_id is distinct from p_claim_id or
     v_queue.claim_expires_at<=v_now or
     not exists (select 1 from public.agent_runtime_leases l
                 where l.name='publish' and l.claim_id=p_claim_id and l.expires_at>v_now) then
    raise exception 'stale publish claim';
  end if;
  if p_outcome='PUBLISHED' and
     (p_service_id is null or nullif(btrim(p_slug),'') is null or
      p_service_status not in ('scheduled','open','ongoing') or p_published_at is null) then
    raise exception 'published result incomplete';
  end if;
  if p_outcome<>'PUBLISHED' and nullif(btrim(p_error_code),'') is null then
    raise exception 'publish error code required';
  end if;
  insert into public.agent_publish_completions
    (claim_id,queue_id,outcome,service_opportunity_id,completed_at)
    values (p_claim_id,p_queue_id,p_outcome,p_service_id,v_now);
  update public.agent_publish_queue set status=p_outcome,
    service_opportunity_id=case when p_outcome='PUBLISHED' then p_service_id else service_opportunity_id end,
    service_slug=case when p_outcome='PUBLISHED' then p_slug else service_slug end,
    service_status=case when p_outcome='PUBLISHED' then p_service_status else service_status end,
    published_at=case when p_outcome='PUBLISHED' then p_published_at else published_at end,
    last_error_code=case when p_outcome='PUBLISHED' then null else p_error_code end,
    next_attempt_at=case when p_outcome='FAILED' then v_now+interval '15 minutes' else next_attempt_at end,
    claim_id=null,worker_id=null,claimed_at=null,claim_expires_at=null,updated_at=v_now
    where id=p_queue_id;
  update public.agent_runtime_leases set claim_id=null,worker_id=null,
    claimed_at=null,expires_at=null where name='publish' and claim_id=p_claim_id;
  return p_outcome;
end;
$$;
alter function public.agent_finish_publish(uuid,uuid,text,uuid,text,text,timestamptz,text) owner to postgres;
revoke all on function public.agent_finish_publish(uuid,uuid,text,uuid,text,text,timestamptz,text)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_finish_publish(uuid,uuid,text,uuid,text,text,timestamptz,text)
  to service_role;

create function public.agent_acquire_automation_lease(
  p_worker_id text,p_claim_id uuid,p_lease_seconds integer)
returns boolean language plpgsql security definer set search_path=pg_catalog as $$
declare v_lock public.agent_runtime_leases%rowtype; v_now timestamptz;
begin
  if nullif(btrim(p_worker_id),'') is null or p_claim_id is null or
     p_lease_seconds is null or p_lease_seconds not between 1 and 86400 then
    raise exception 'invalid automation lease';
  end if;
  insert into public.agent_runtime_leases(name) values ('automation') on conflict do nothing;
  select * into v_lock from public.agent_runtime_leases where name='automation' for update;
  v_now:=clock_timestamp();
  if v_lock.expires_at>v_now then return false; end if;
  update public.agent_runtime_leases set claim_id=p_claim_id,worker_id=p_worker_id,
    claimed_at=v_now,expires_at=v_now+make_interval(secs=>p_lease_seconds)
    where name='automation';
  return true;
end;
$$;
alter function public.agent_acquire_automation_lease(text,uuid,integer) owner to postgres;
revoke all on function public.agent_acquire_automation_lease(text,uuid,integer)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_acquire_automation_lease(text,uuid,integer) to service_role;

create function public.agent_release_automation_lease(p_claim_id uuid)
returns boolean language plpgsql security definer set search_path=pg_catalog as $$
begin
  if p_claim_id is null then raise exception 'claim required'; end if;
  update public.agent_runtime_leases set claim_id=null,worker_id=null,
    claimed_at=null,expires_at=null where name='automation' and claim_id=p_claim_id;
  return found;
end;
$$;
alter function public.agent_release_automation_lease(uuid) owner to postgres;
revoke all on function public.agent_release_automation_lease(uuid)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_release_automation_lease(uuid) to service_role;

commit;
