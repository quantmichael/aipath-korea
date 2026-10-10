-- PHASE 7.3B. Agent-only lifecycle and append-only human review audit.
-- Apply separately after 001/002/003. No Service table or existing row is changed.
begin;

alter table public.agent_sources
  drop constraint if exists agent_sources_status_check;
alter table public.agent_sources
  add constraint agent_sources_status_check check (status in
    ('CANDIDATE', 'REVIEW', 'APPROVED', 'ACTIVE', 'HOLD', 'REJECTED', 'DISABLED'));
alter table public.agent_sources
  add constraint agent_sources_reviewed_active_check check (
    status <> 'ACTIVE' or coalesce((
      is_active and provenance #>> '{source_review,approval,decision}' = 'APPROVED'
      and provenance #>> '{source_review,last_decision}' = 'ACTIVATED'
      and provenance #>> '{source_review,last_decision_id}' is not null
      and provenance #>> '{source_review,approval,canonical_url}' = canonical_url
      and provenance #>> '{source_review,approval,domain}' =
        regexp_replace(lower(substring(canonical_url from '^https?://([^/:?#]+)')), '^www\.', '')
      and provenance #>> '{source_review,approval,source_type}' = source_type
      and provenance #>> '{source_review,approval,trust}' = case
        when source_type = 'official' then 'OFFICIAL'
        when source_type = 'aggregator' and confidence >= 0.8 then 'TRUSTED'
        else null end
      and provenance #>> '{source_review,approval,confidence}' is not distinct from confidence::text
      and provenance #>> '{source_review,approval,collection_method}' = collection_method
      and provenance #> '{source_review,approval,collection_config}' = collection_config
      and provenance #> '{source_review,approval,collection_allowed}' = 'true'::jsonb
      and (source_type <> 'official' or
           provenance #> '{source_review,approval,officiality_confirmed}' = 'true'::jsonb)
      and provenance #>> '{source_review,approval,reviewer}' is not null
      and provenance #>> '{source_review,approval,reason}' is not null
      and provenance #>> '{source_review,approval,reviewed_at}' is not null
    ), false)
  ) not valid;

create table public.agent_source_review_events (
  id uuid primary key,
  source_id uuid not null references public.agent_sources(id),
  from_status text not null,
  review_status text not null,
  review_decision text not null,
  reviewer text not null check (length(btrim(reviewer)) > 0),
  review_reason text not null check (length(btrim(review_reason)) > 0),
  reviewed_at timestamptz not null,
  approval_decision text not null,
  activation_decision text not null,
  officiality_confirmed boolean,
  collection_allowed boolean,
  canonical_url text not null,
  normalized_domain text not null,
  source_type_before text not null,
  source_type_after text not null,
  officiality_signals jsonb not null default '[]'::jsonb,
  constraint agent_source_review_signals_array check (jsonb_typeof(officiality_signals) = 'array'),
  constraint agent_source_review_decision_unique unique (source_id, id)
);
create index agent_source_review_events_source_time_idx
  on public.agent_source_review_events(source_id, reviewed_at desc);
alter table public.agent_source_review_events enable row level security;
-- The RPC owner alone writes events. service_role may read, but cannot forge audit rows.
revoke all on public.agent_source_review_events from public, anon, authenticated, service_role;
grant select on public.agent_source_review_events to service_role;

create function public.agent_source_review_audit_immutable() returns trigger
language plpgsql as $$
begin
  raise exception 'source review audit events are append-only';
end;
$$;
create trigger agent_source_review_events_immutable
  before update or delete on public.agent_source_review_events
  for each row execute function public.agent_source_review_audit_immutable();

create function public.agent_source_review_guard() returns trigger
language plpgsql set search_path = pg_catalog as $$
begin
  if tg_op = 'INSERT' then
    if new.status is distinct from 'CANDIDATE' or new.is_active is distinct from false or
       new.provenance is null or jsonb_typeof(new.provenance) <> 'object' or
       new.provenance ?| array['source_review', 'approval', 'approved_at',
                                'reviewer', 'review_reason', 'activation'] then
      raise exception 'new sources must be inactive CANDIDATE without review metadata';
    end if;
    return new;
  end if;
  if (old.status, old.source_type, old.is_active) is distinct from
     (new.status, new.source_type, new.is_active)
     or old.provenance -> 'source_review' is distinct from new.provenance -> 'source_review'
     or (old.status in ('APPROVED', 'ACTIVE') and
         (old.canonical_url is distinct from new.canonical_url or
          old.confidence is distinct from new.confidence or
          old.collection_method is distinct from new.collection_method or
          old.collection_config is distinct from new.collection_config))
  then
    -- A caller can set a custom GUC; the GUC alone is not authorization.
    -- Only the SECURITY DEFINER RPC owner can pass this check. Direct
    -- service_role SQL/REST remains in the service_role execution context.
    if current_setting('agent.source_review_transition', true) is distinct from 'allowed' or
       current_user <> pg_get_userbyid((select proowner from pg_proc where oid =
         'public.agent_review_source(uuid,text,text,text,text,text,uuid,text,boolean,boolean)'::regprocedure)) then
      raise exception 'source lifecycle changes require reviewed transition';
    end if;
  end if;
  return new;
end;
$$;
create trigger agent_source_review_guard_before_write
  before insert or update on public.agent_sources
  for each row execute function public.agent_source_review_guard();

create function public.agent_review_source(
  p_source_id uuid, p_expected_status text, p_target_status text,
  p_reviewer text, p_reason text, p_decision text, p_decision_id uuid,
  p_source_type text default null,
  p_officiality_confirmed boolean default null,
  p_collection_allowed boolean default null
) returns public.agent_sources
-- EXECUTE is restricted below. Fixed search_path and qualified relations avoid
-- object-shadowing in this privilege boundary; no dynamic SQL is used.
language plpgsql security definer set search_path = pg_catalog as $$
declare
  v_source public.agent_sources%rowtype;
  v_prior public.agent_source_review_events%rowtype;
  v_domain text;
  v_type text;
  v_old_type text;
  v_trust text;
  v_review jsonb;
  v_approval text := 'UNCHANGED';
  v_activation text := 'NOT_ACTIVATED';
  v_now timestamptz := now();
begin
  if p_reviewer is null or btrim(p_reviewer) = '' or
     p_reason is null or btrim(p_reason) = '' or p_decision_id is null then
    raise exception 'reviewer, reason and decision id are required';
  end if;
  select * into v_source from public.agent_sources where id = p_source_id for update;
  if not found then raise exception 'source not found'; end if;
  select * into v_prior from public.agent_source_review_events
    where source_id = p_source_id and id = p_decision_id;
  if found then
    if v_prior.review_status <> p_target_status or v_prior.review_decision <> p_decision or
       v_prior.reviewer <> p_reviewer or
       v_prior.review_reason <> p_reason or
       v_prior.source_type_after <> coalesce(p_source_type, v_prior.source_type_before) or
       v_prior.officiality_confirmed is distinct from p_officiality_confirmed or
       v_prior.collection_allowed is distinct from p_collection_allowed then
      raise exception 'decision id reused with different review decision';
    end if;
    return v_source;
  end if;
  if v_source.status <> p_expected_status then
    raise exception 'source status changed; review again';
  end if;
  if not (
    (p_decision = 'REVIEW_STARTED' and p_target_status = 'REVIEW' and
      v_source.status in ('CANDIDATE', 'HOLD', 'REJECTED', 'DISABLED')) or
    (p_decision = 'TYPE_CHANGED' and p_target_status = 'REVIEW' and
      v_source.status = 'REVIEW' and p_source_type is not null and
      p_source_type <> v_source.source_type) or
    (p_decision = 'APPROVED' and p_target_status = 'APPROVED' and v_source.status = 'REVIEW') or
    (p_decision = 'ACTIVATED' and p_target_status = 'ACTIVE' and v_source.status = 'APPROVED') or
    (p_decision = 'HELD' and p_target_status = 'HOLD' and
      v_source.status in ('REVIEW', 'APPROVED', 'ACTIVE')) or
    (p_decision = 'REJECTED' and p_target_status = 'REJECTED' and v_source.status = 'REVIEW') or
    (p_decision = 'DISABLED' and p_target_status = 'DISABLED' and v_source.status = 'ACTIVE')
  ) then raise exception 'invalid source review transition'; end if;
  if p_source_type is not null and p_decision <> 'TYPE_CHANGED' then
    raise exception 'source type may change only during review';
  end if;
  if p_decision = 'APPROVED' then
    if p_collection_allowed is distinct from true or p_officiality_confirmed is null then
      raise exception 'human collection permission and officiality decision required';
    end if;
  elsif p_collection_allowed is not null or p_officiality_confirmed is not null then
    raise exception 'permission decisions belong to approval';
  end if;
  v_old_type := v_source.source_type;
  v_type := coalesce(p_source_type, v_source.source_type);
  if v_type not in ('official', 'aggregator', 'community', 'unknown') then
    raise exception 'invalid source type';
  end if;
  v_domain := lower(substring(v_source.canonical_url from '^https?://([^/:?#]+)'));
  v_domain := regexp_replace(v_domain, '^www\.', '');
  v_review := coalesce(v_source.provenance -> 'source_review', '{}'::jsonb);
  if p_decision = 'APPROVED' then
    if v_source.collection_method is null then raise exception 'collection method required'; end if;
    if v_type = 'official' and p_officiality_confirmed is distinct from true then
      raise exception 'official source requires confirmed officiality';
    end if;
    if v_type = 'official' then v_trust := 'OFFICIAL';
    elsif v_type = 'aggregator' and v_source.confidence >= 0.8 then v_trust := 'TRUSTED';
    else raise exception 'untrusted source type cannot be approved'; end if;
    v_review := jsonb_set(v_review, '{approval}', jsonb_build_object(
      'decision', 'APPROVED', 'reviewer', p_reviewer, 'reason', p_reason,
      'reviewed_at', v_now, 'canonical_url', v_source.canonical_url,
      'domain', v_domain, 'source_type', v_type, 'trust', v_trust,
      'confidence', v_source.confidence,
      'collection_method', v_source.collection_method,
      'collection_config', v_source.collection_config,
      'officiality_confirmed', p_officiality_confirmed,
      'collection_allowed', p_collection_allowed));
    v_approval := 'APPROVED';
  elsif p_decision = 'ACTIVATED' then
    if v_review #>> '{approval,decision}' is distinct from 'APPROVED' or
       v_review #>> '{approval,canonical_url}' is distinct from v_source.canonical_url or
       v_review #>> '{approval,domain}' is distinct from v_domain or
       v_review #>> '{approval,source_type}' is distinct from v_type or
       v_review #>> '{approval,confidence}' is distinct from v_source.confidence::text or
       v_review #>> '{approval,collection_method}' is distinct from v_source.collection_method or
       v_review #> '{approval,collection_config}' is distinct from v_source.collection_config or
       v_review #> '{approval,collection_allowed}' is distinct from 'true'::jsonb or
       (v_type = 'official' and
        v_review #> '{approval,officiality_confirmed}' is distinct from 'true'::jsonb) or
       v_review #>> '{approval,reviewer}' is null then
      raise exception 'matching human approval required';
    end if;
    if v_type = 'official' then v_trust := 'OFFICIAL';
    elsif v_type = 'aggregator' and v_source.confidence >= 0.8 then v_trust := 'TRUSTED';
    else raise exception 'source trust changed since approval'; end if;
    if v_review #>> '{approval,trust}' is distinct from v_trust then
      raise exception 'source trust changed since approval';
    end if;
    v_activation := 'ACTIVATED';
  else
    v_review := v_review - 'approval';
    if p_decision = 'REJECTED' then v_approval := 'REJECTED'; end if;
    if p_decision = 'HELD' then v_approval := 'HELD'; end if;
    if p_decision = 'DISABLED' then v_activation := 'DISABLED'; end if;
  end if;
  v_review := v_review || jsonb_build_object(
    'last_decision_id', p_decision_id, 'last_decision', p_decision,
    'review_status', p_target_status);
  perform set_config('agent.source_review_transition', 'allowed', true);
  update public.agent_sources set status = p_target_status,
    is_active = (p_target_status = 'ACTIVE'), source_type = v_type,
    provenance = jsonb_set(v_source.provenance, '{source_review}', v_review, true),
    updated_at = v_now where id = p_source_id returning * into v_source;
  insert into public.agent_source_review_events (
    id, source_id, from_status, review_status, review_decision, reviewer, review_reason,
    reviewed_at, approval_decision, activation_decision, canonical_url,
    normalized_domain, source_type_before, source_type_after, officiality_signals,
    officiality_confirmed, collection_allowed
  ) values (
    p_decision_id, p_source_id, p_expected_status, p_target_status, p_decision,
    p_reviewer, p_reason, v_now, v_approval, v_activation,
    v_source.canonical_url, v_domain,
    v_old_type,
    v_type, coalesce(v_source.provenance -> 'discovery_officiality_signals', '[]'::jsonb),
    p_officiality_confirmed, p_collection_allowed
  );
  return v_source;
end;
$$;
revoke all on function public.agent_review_source(uuid,text,text,text,text,text,uuid,text,boolean,boolean)
  from public, anon, authenticated, service_role;
grant execute on function public.agent_review_source(uuid,text,text,text,text,text,uuid,text,boolean,boolean)
  to service_role;

commit;
