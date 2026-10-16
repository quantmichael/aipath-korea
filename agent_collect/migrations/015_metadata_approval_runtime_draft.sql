-- DRAFT ONLY. Isolated validation first; do not apply to Preview or Production
-- without separately confirming this exact SQL and project identity.
-- Extends Preview-tested 014; preserves its existing synthetic rows.
begin;

create table public.agent_metadata_reviewers (
  user_id uuid primary key,
  active boolean not null default true,
  granted_at timestamptz not null default clock_timestamp(),
  granted_by text not null check (length(btrim(granted_by)) > 0)
);
alter table public.agent_metadata_reviewers enable row level security;
revoke all on public.agent_metadata_reviewers from public,anon,authenticated,service_role;

create table public.agent_metadata_approval_events (
  id uuid primary key default gen_random_uuid(),
  decision_id uuid not null unique,
  approval_id uuid not null references public.agent_metadata_source_approvals(id),
  source_id uuid not null references public.agent_sources(id),
  action text not null check (action in ('APPROVED','REVOKED')),
  actor_uid uuid not null,
  reason text not null check (length(btrim(reason)) between 3 and 500),
  policy_snapshot jsonb not null default '{}'::jsonb
    check (jsonb_typeof(policy_snapshot)='object'),
  acted_at timestamptz not null default clock_timestamp()
);
create unique index agent_metadata_one_approval_event
  on public.agent_metadata_approval_events(approval_id) where action='APPROVED';
create unique index agent_metadata_one_revocation_event
  on public.agent_metadata_approval_events(approval_id) where action='REVOKED';
create index agent_metadata_approval_events_source_idx
  on public.agent_metadata_approval_events(source_id,acted_at desc);
alter table public.agent_metadata_approval_events enable row level security;
revoke all on public.agent_metadata_approval_events
  from public,anon,authenticated,service_role;
grant select on public.agent_metadata_approval_events to service_role;

create function public.agent_metadata_audit_immutable() returns trigger
language plpgsql set search_path=pg_catalog as $$
begin
  raise exception 'metadata approval audit is append-only';
end;
$$;
create trigger agent_metadata_approval_events_immutable
before update or delete on public.agent_metadata_approval_events
for each row execute function public.agent_metadata_audit_immutable();

alter table public.agent_metadata_candidates
  add column request_id uuid unique;
create unique index agent_metadata_candidate_approval_period_unique
  on public.agent_metadata_candidates (
    approval_id,official_url,
    coalesce(facts->>'application_start',''),
    coalesce(facts->>'application_deadline','')
  );
create table public.agent_metadata_candidate_events (
  id uuid primary key default gen_random_uuid(),
  decision_id uuid not null unique,
  candidate_id uuid not null references public.agent_metadata_candidates(id),
  from_status text not null,
  to_status text not null,
  actor_kind text not null check (actor_kind in ('service_role','reviewer')),
  reason text not null check (length(btrim(reason)) > 0),
  details jsonb not null default '{}'::jsonb
    check (jsonb_typeof(details)='object'),
  acted_at timestamptz not null default clock_timestamp()
);
alter table public.agent_metadata_candidate_events enable row level security;
revoke all on public.agent_metadata_candidate_events
  from public,anon,authenticated,service_role;
grant select on public.agent_metadata_candidate_events to service_role;
create trigger agent_metadata_candidate_events_immutable
before update or delete on public.agent_metadata_candidate_events
for each row execute function public.agent_metadata_audit_immutable();

-- service_role is a machine principal: it can read policy, not assert that a
-- human reviewed it. Only authenticated, allowlisted auth.uid() can do so.
revoke insert,update,delete on public.agent_metadata_source_approvals
  from service_role;

create function public.agent_metadata_approve(
  p_decision_id uuid, p_source_id uuid, p_policy jsonb, p_reason text
) returns uuid language plpgsql security definer set search_path=pg_catalog as $$
declare
  v_actor uuid := auth.uid();
  v_existing public.agent_metadata_approval_events%rowtype;
  v_source public.agent_sources%rowtype;
  v_approval uuid;
begin
  if v_actor is null or not exists (
    select 1 from public.agent_metadata_reviewers
    where user_id=v_actor and active=true
  ) then raise exception 'authenticated metadata reviewer required'; end if;
  if p_decision_id is null or p_source_id is null or
     jsonb_typeof(p_policy) is distinct from 'object' or
     length(btrim(coalesce(p_reason,''))) not between 3 and 500 then
    raise exception 'invalid metadata approval request';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(p_source_id::text,0));
  select * into v_existing from public.agent_metadata_approval_events
    where decision_id=p_decision_id;
  if found then
    if v_existing.action <> 'APPROVED' or v_existing.actor_uid <> v_actor or
       v_existing.source_id <> p_source_id or
       v_existing.policy_snapshot <> p_policy or
       v_existing.reason <> p_reason then
      raise exception 'decision id reused with different approval';
    end if;
    return v_existing.approval_id;
  end if;
  select * into v_source from public.agent_sources where id=p_source_id for update;
  if not found or v_source.source_type <> 'official' or
     v_source.status not in ('REVIEW','APPROVED','ACTIVE') or
     v_source.canonical_url is distinct from p_policy->>'official_url' then
    raise exception 'matching reviewed official Source required';
  end if;
  if exists (
    select 1 from public.agent_metadata_approval_events e
    join public.agent_metadata_source_approvals a on a.id=e.approval_id
    where e.source_id=p_source_id and a.cohort='operational'
      and e.action='APPROVED' and not exists (
        select 1 from public.agent_metadata_approval_events r
        where r.approval_id=e.approval_id and r.action='REVOKED'
      )
  ) then raise exception 'active metadata approval must be revoked first'; end if;
  insert into public.agent_metadata_source_approvals (
    source_id,cohort,reviewer,reviewed_at,expires_at,official_url,
    permission_url,permission_digest,organizer_relationship,approved_organizer,
    page_type,scope_origin,path_prefix,officiality_confirmed,public_read,
    automated_access,fact_use,recheck_allowed,raw_storage_allowed,
    redistribution_allowed
  ) values (
    p_source_id,'operational',v_actor::text,clock_timestamp(),
    (p_policy->>'expires_at')::timestamptz,p_policy->>'official_url',
    p_policy->>'permission_url',p_policy->>'permission_digest',
    p_policy->>'organizer_relationship',p_policy->>'approved_organizer',
    p_policy->>'page_type',p_policy->>'scope_origin',p_policy->>'path_prefix',
    (p_policy->>'officiality_confirmed')::boolean,
    (p_policy->>'public_read')::boolean,
    (p_policy->>'automated_access')::boolean,
    (p_policy->>'fact_use')::boolean,
    (p_policy->>'recheck_allowed')::boolean,
    coalesce((p_policy->>'raw_storage_allowed')::boolean,false),
    coalesce((p_policy->>'redistribution_allowed')::boolean,false)
  ) returning id into v_approval;
  insert into public.agent_metadata_approval_events
    (decision_id,approval_id,source_id,action,actor_uid,reason,policy_snapshot)
  values (p_decision_id,v_approval,p_source_id,'APPROVED',v_actor,p_reason,p_policy);
  return v_approval;
end;
$$;
revoke all on function public.agent_metadata_approve(uuid,uuid,jsonb,text)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_metadata_approve(uuid,uuid,jsonb,text)
  to authenticated;

create function public.agent_metadata_revoke(
  p_decision_id uuid, p_approval_id uuid, p_reason text
) returns uuid language plpgsql security definer set search_path=pg_catalog as $$
declare
  v_actor uuid := auth.uid();
  v_existing public.agent_metadata_approval_events%rowtype;
  v_approval public.agent_metadata_source_approvals%rowtype;
  v_event uuid;
begin
  if v_actor is null or not exists (
    select 1 from public.agent_metadata_reviewers
    where user_id=v_actor and active=true
  ) then raise exception 'authenticated metadata reviewer required'; end if;
  if p_decision_id is null or p_approval_id is null or
     length(btrim(coalesce(p_reason,''))) not between 3 and 500 then
    raise exception 'invalid metadata revocation request';
  end if;
  select * into v_approval from public.agent_metadata_source_approvals
    where id=p_approval_id and cohort='operational' for update;
  if not found then raise exception 'operational approval not found'; end if;
  select * into v_existing from public.agent_metadata_approval_events
    where decision_id=p_decision_id;
  if found then
    if v_existing.action <> 'REVOKED' or v_existing.actor_uid <> v_actor or
       v_existing.approval_id <> p_approval_id or v_existing.reason <> p_reason then
      raise exception 'decision id reused with different revocation';
    end if;
    return v_existing.id;
  end if;
  if not exists (select 1 from public.agent_metadata_approval_events
                 where approval_id=p_approval_id and action='APPROVED') or
     exists (select 1 from public.agent_metadata_approval_events
             where approval_id=p_approval_id and action='REVOKED') then
    raise exception 'approval already revoked or unaudited';
  end if;
  insert into public.agent_metadata_approval_events
    (decision_id,approval_id,source_id,action,actor_uid,reason)
  values (p_decision_id,p_approval_id,v_approval.source_id,
          'REVOKED',v_actor,p_reason) returning id into v_event;
  insert into public.agent_metadata_candidate_events
    (decision_id,candidate_id,from_status,to_status,actor_kind,reason)
  select gen_random_uuid(),c.id,'PUBLISH_READY','REVIEW_REQUIRED',
         'reviewer','metadata_approval_revoked'
  from public.agent_metadata_candidates c
  where c.approval_id=p_approval_id and c.status='PUBLISH_READY';
  update public.agent_metadata_candidates
    set status='REVIEW_REQUIRED',
        gate_decision=jsonb_build_object('status','REVIEW_REQUIRED',
                                         'reason','metadata_approval_revoked')
    where approval_id=p_approval_id and status='PUBLISH_READY';
  return v_event;
end;
$$;
revoke all on function public.agent_metadata_revoke(uuid,uuid,text)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_metadata_revoke(uuid,uuid,text)
  to authenticated;

-- Existing 014 synthetic rows remain. Machine writes use narrow RPCs only.
revoke insert,update,delete on public.agent_metadata_candidates
  from service_role;

create function public.agent_metadata_create_candidate(
  p_request_id uuid, p_approval_id uuid, p_facts jsonb,
  p_field_evidence jsonb, p_discovered_at timestamptz
) returns uuid language plpgsql security definer set search_path=pg_catalog as $$
declare
  v_approval public.agent_metadata_source_approvals%rowtype;
  v_existing public.agent_metadata_candidates%rowtype;
  v_id uuid;
begin
  if p_request_id is null or p_approval_id is null or
     jsonb_typeof(p_facts) is distinct from 'object' or
     jsonb_typeof(p_field_evidence) is distinct from 'object' or
     p_discovered_at is null or p_discovered_at > clock_timestamp()+interval '1 minute' then
    raise exception 'invalid metadata candidate request';
  end if;
  select * into v_approval from public.agent_metadata_source_approvals
    where id=p_approval_id and cohort='operational' for share;
  if not found or v_approval.expires_at <= clock_timestamp() or
     p_facts->>'official_url' is distinct from v_approval.official_url or
     not exists (select 1 from public.agent_metadata_approval_events
                 where approval_id=p_approval_id and action='APPROVED') or
     exists (select 1 from public.agent_metadata_approval_events
             where approval_id=p_approval_id and action='REVOKED') or
     not exists (select 1 from public.agent_sources
                 where id=v_approval.source_id and source_type='official'
                   and status in ('REVIEW','APPROVED','ACTIVE')
                   and canonical_url=v_approval.official_url) then
    raise exception 'active reviewed metadata approval required';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    p_approval_id::text||v_approval.official_url||
    coalesce(p_facts->>'application_start','')||
    coalesce(p_facts->>'application_deadline',''),0));
  select * into v_existing from public.agent_metadata_candidates
    where request_id=p_request_id;
  if found then
    if v_existing.approval_id <> p_approval_id or
       v_existing.facts <> p_facts or
       v_existing.field_evidence <> p_field_evidence then
      raise exception 'request id reused with different candidate';
    end if;
    return v_existing.id;
  end if;
  select * into v_existing from public.agent_metadata_candidates
    where approval_id=p_approval_id and official_url=v_approval.official_url
      and coalesce(facts->>'application_start','') =
          coalesce(p_facts->>'application_start','')
      and coalesce(facts->>'application_deadline','') =
          coalesce(p_facts->>'application_deadline','')
    limit 1;
  if found then return v_existing.id; end if;
  insert into public.agent_metadata_candidates (
    request_id,source_id,approval_id,cohort,official_url,facts,
    field_evidence,status,discovered_at
  ) values (
    p_request_id,v_approval.source_id,p_approval_id,'operational',
    v_approval.official_url,p_facts,p_field_evidence,'REVIEW_REQUIRED',
    p_discovered_at
  ) returning id into v_id;
  insert into public.agent_metadata_candidate_events
    (decision_id,candidate_id,from_status,to_status,actor_kind,reason)
  values (p_request_id,v_id,'NONE','REVIEW_REQUIRED','service_role','candidate_created');
  return v_id;
end;
$$;
revoke all on function public.agent_metadata_create_candidate(uuid,uuid,jsonb,jsonb,timestamptz)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_metadata_create_candidate(uuid,uuid,jsonb,jsonb,timestamptz)
  to service_role;

create function public.agent_metadata_record_assessment(
  p_decision_id uuid, p_candidate_id uuid, p_expected_status text,
  p_status text, p_verification jsonb, p_duplicate jsonb,
  p_gate jsonb, p_verified_at timestamptz
) returns uuid language plpgsql security definer set search_path=pg_catalog as $$
declare
  v_candidate public.agent_metadata_candidates%rowtype;
  v_approval public.agent_metadata_source_approvals%rowtype;
  v_prior public.agent_metadata_candidate_events%rowtype;
begin
  if p_decision_id is null or p_candidate_id is null or
     p_expected_status is null or
     p_status is null or
     p_status not in ('REVIEW_REQUIRED','PUBLISH_READY','REJECTED') or
     jsonb_typeof(p_verification) is distinct from 'object' or
     jsonb_typeof(p_duplicate) is distinct from 'object' or
     jsonb_typeof(p_gate) is distinct from 'object' or
     p_verified_at is null or p_verified_at > clock_timestamp()+interval '1 minute' then
    raise exception 'invalid metadata assessment request';
  end if;
  select * into v_candidate from public.agent_metadata_candidates
    where id=p_candidate_id and cohort='operational';
  if not found then raise exception 'operational candidate not found'; end if;
  -- Lock approval before candidate, matching revocation's lock order.
  select * into v_approval from public.agent_metadata_source_approvals
    where id=v_candidate.approval_id for share;
  select * into v_candidate from public.agent_metadata_candidates
    where id=p_candidate_id and cohort='operational' for update;
  if not found or v_candidate.approval_id <> v_approval.id then
    raise exception 'candidate approval changed';
  end if;
  select * into v_prior from public.agent_metadata_candidate_events
    where decision_id=p_decision_id;
  if found then
    if v_prior.candidate_id <> p_candidate_id or
       v_prior.to_status <> p_status or
       v_prior.details <> jsonb_build_object(
         'verification',p_verification,'duplicate',p_duplicate,
         'gate',p_gate,'verified_at',p_verified_at) then
      raise exception 'decision id reused with different assessment';
    end if;
    return v_candidate.id;
  end if;
  if v_candidate.status <> p_expected_status then
    raise exception 'candidate state changed';
  end if;
  if p_status='PUBLISH_READY' then
    if v_approval.expires_at <= clock_timestamp() or
       not exists (select 1 from public.agent_metadata_approval_events
                   where approval_id=v_approval.id and action='APPROVED') or
       exists (select 1 from public.agent_metadata_approval_events
               where approval_id=v_approval.id and action='REVOKED') or
       not exists (select 1 from public.agent_sources
                   where id=v_candidate.source_id and source_type='official'
                     and status in ('REVIEW','APPROVED','ACTIVE')
                     and canonical_url=v_candidate.official_url) or
       p_verification->>'status' is distinct from 'VERIFIED' or
       coalesce(p_verification->>'time_precision','') not in
         ('DATE_ONLY','EXACT_TIME') or
       p_duplicate->>'status' is distinct from 'UNIQUE' or
       p_gate->>'status' is distinct from 'PUBLISH_READY' or
       jsonb_array_length(coalesce(p_verification->'evidence_refs','[]'::jsonb))=0 then
      raise exception 'metadata publish gate prerequisites failed';
    end if;
  end if;
  update public.agent_metadata_candidates set
    status=p_status,verification=p_verification,
    duplicate_result=p_duplicate,gate_decision=p_gate,verified_at=p_verified_at
  where id=p_candidate_id;
  insert into public.agent_metadata_candidate_events
    (decision_id,candidate_id,from_status,to_status,actor_kind,reason,details)
  values (p_decision_id,p_candidate_id,p_expected_status,p_status,
          'service_role','evidence_assessment',jsonb_build_object(
            'verification',p_verification,'duplicate',p_duplicate,
            'gate',p_gate,'verified_at',p_verified_at));
  return p_candidate_id;
end;
$$;
revoke all on function public.agent_metadata_record_assessment(
  uuid,uuid,text,text,jsonb,jsonb,jsonb,timestamptz)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_metadata_record_assessment(
  uuid,uuid,text,text,jsonb,jsonb,jsonb,timestamptz) to service_role;

commit;
