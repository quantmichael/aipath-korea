-- Metadata-only publication. Apply after Service 004 and Agent 014-017.
-- One PostgreSQL transaction owns the Service registration and completion.
-- The existing Full Raw queue and its completion RPC are unchanged.
begin;

do $$
begin
  if not exists (select 1 from pg_constraint
                 where conname='agent_metadata_candidate_gate'
                   and pg_get_constraintdef(oid) like '%competition%') or
     to_regprocedure('public.register_opportunity_with_evidence(jsonb,jsonb,uuid,boolean)') is null or
     to_regprocedure('public.agent_metadata_record_assessment(uuid,uuid,text,text,jsonb,jsonb,jsonb,timestamptz)') is null then
    raise exception 'Metadata 017, Agent 015 and Service 004 required';
  end if;
end;
$$;

create table public.agent_metadata_publish_completions (
  candidate_id uuid primary key references public.agent_metadata_candidates(id),
  -- Historical identity deliberately survives Preview synthetic Service-row
  -- cleanup; an immutable audit row cannot carry an ON DELETE FK action.
  opportunity_id uuid not null unique,
  approval_id uuid not null references public.agent_metadata_source_approvals(id),
  verification_at timestamptz not null,
  published_at timestamptz not null default clock_timestamp(),
  registration_record jsonb not null check (jsonb_typeof(registration_record)='object'),
  registration_evidence jsonb not null check (jsonb_typeof(registration_evidence)='object'),
  disposition text not null default 'PUBLISHED' check (disposition='PUBLISHED')
);
alter table public.agent_metadata_publish_completions enable row level security;
revoke all on public.agent_metadata_publish_completions
  from public,anon,authenticated,service_role;
grant select on public.agent_metadata_publish_completions to service_role;
create trigger agent_metadata_publish_completions_immutable
before update or delete on public.agent_metadata_publish_completions
for each row execute function public.agent_metadata_audit_immutable();

create function public.agent_publish_metadata_candidate(
  p_candidate_id uuid, p_record jsonb, p_evidence jsonb
) returns table(opportunity_id uuid, disposition text)
language plpgsql security definer set search_path=pg_catalog as $$
declare
  v_candidate public.agent_metadata_candidates%rowtype;
  v_approval public.agent_metadata_source_approvals%rowtype;
  v_source public.agent_sources%rowtype;
  v_existing public.agent_metadata_publish_completions%rowtype;
  v_service_source public.sources%rowtype;
  v_category public.categories%rowtype;
  v_category_slug text;
  v_agent_host text;
  v_service_host text;
  v_source_match_count integer;
  v_field text;
  v_stored_name text;
  v_fact text;
  v_precision text;
  v_result record;
begin
  if p_candidate_id is null or jsonb_typeof(p_record) is distinct from 'object' or
     jsonb_typeof(p_evidence) is distinct from 'object' then
    raise exception 'invalid metadata publish request';
  end if;
  -- Same lock order as the 015 assessment and revocation RPCs.
  select * into v_candidate from public.agent_metadata_candidates
    where id=p_candidate_id and cohort='operational';
  if not found then raise exception 'operational metadata Candidate required'; end if;
  select * into v_approval from public.agent_metadata_source_approvals
    where id=v_candidate.approval_id for share;
  select * into v_candidate from public.agent_metadata_candidates
    where id=p_candidate_id and cohort='operational' for update;
  if not found or v_candidate.approval_id is distinct from v_approval.id then
    raise exception 'metadata Candidate approval changed';
  end if;
  -- Response-loss retries return only an already committed completion.
  select * into v_existing from public.agent_metadata_publish_completions
    where candidate_id=p_candidate_id;
  if found then
    return query select v_existing.opportunity_id, 'already_published'::text;
    return;
  end if;
  select * into v_source from public.agent_sources
    where id=v_candidate.source_id for share;
  if v_source.id is null or v_source.source_type <> 'official' or
     v_source.status <> 'ACTIVE' or not v_source.is_active or
     v_source.canonical_url is distinct from v_approval.official_url or
     v_candidate.official_url is distinct from v_approval.official_url or
     v_candidate.facts->>'official_url' is distinct from v_approval.official_url or
     (v_source.provenance #>> '{source_review,approval,decision}') is distinct from 'APPROVED' or
     (v_source.provenance #>> '{source_review,approval,canonical_url}') is distinct from v_source.canonical_url or
     (v_source.provenance #>> '{source_review,approval,officiality_confirmed}') is distinct from 'true' or
     (v_source.provenance #>> '{source_review,approval,collection_allowed}') is distinct from 'true' or
     (v_source.provenance #>> '{source_review,approval,source_type}') is distinct from v_source.source_type or
     (v_source.provenance #>> '{source_review,approval,collection_method}') is distinct from v_source.collection_method or
     (v_source.provenance #>> '{source_review,approval,confidence}')::double precision
       is distinct from v_source.confidence or
     (v_source.provenance #> '{source_review,approval,collection_config}')
       is distinct from v_source.collection_config then
    raise exception 'active reviewed official Source required';
  end if;
  if v_approval.cohort <> 'operational' or
     not (v_approval.reviewed_at <= clock_timestamp() and
          clock_timestamp() < v_approval.expires_at) or
     not (v_approval.officiality_confirmed and v_approval.public_read and
          v_approval.automated_access and v_approval.fact_use and
          v_approval.recheck_allowed) or
     v_approval.page_type <> 'INDIVIDUAL_NOTICE' or
     not (v_candidate.official_url =
            v_approval.scope_origin||rtrim(v_approval.path_prefix,'/') or
          left(v_candidate.official_url,
               length(v_approval.scope_origin||v_approval.path_prefix)) =
            v_approval.scope_origin||v_approval.path_prefix) or
     left(v_approval.permission_url,length(v_approval.scope_origin)+1)
       is distinct from v_approval.scope_origin||'/' or
     lower(btrim(v_candidate.facts->>'organizer')) is distinct from
       lower(btrim(v_approval.approved_organizer)) or
     not exists (select 1 from public.agent_metadata_approval_events e
                 where e.approval_id=v_approval.id and e.action='APPROVED') or
     exists (select 1 from public.agent_metadata_approval_events e
             where e.approval_id=v_approval.id and e.action='REVOKED') then
    raise exception 'active audited metadata permission required';
  end if;
  if v_candidate.status <> 'PUBLISH_READY' or
     v_candidate.verification->>'status' is distinct from 'VERIFIED' or
     v_candidate.duplicate_result->>'status' is distinct from 'UNIQUE' or
     v_candidate.gate_decision->>'status' is distinct from 'PUBLISH_READY' or
     v_candidate.verified_at is null or
     v_candidate.verified_at > clock_timestamp() or
     v_candidate.verified_at < clock_timestamp()-interval '15 minutes' or
     exists (select 1 from public.agent_metadata_candidates other
             where other.id <> p_candidate_id and
               other.official_url=v_candidate.official_url and
               other.status <> 'REJECTED') or
     jsonb_typeof(v_candidate.verification->'evidence_refs') is distinct from 'array' or
     jsonb_array_length(v_candidate.verification->'evidence_refs')=0 or
     not exists (
       select 1 from public.agent_metadata_candidate_events ce
       join public.agent_metadata_approval_events ae
         on ae.approval_id=v_candidate.approval_id and ae.action='APPROVED'
       where ce.candidate_id=p_candidate_id and ce.to_status='PUBLISH_READY'
         and ce.actor_kind='service_role' and ce.acted_at >= ae.acted_at
         and ce.details->'verification'=v_candidate.verification
         and ce.details->'duplicate'=v_candidate.duplicate_result
         and ce.details->'gate'=v_candidate.gate_decision
         and (ce.details->>'verified_at')::timestamptz=v_candidate.verified_at
     ) then
    raise exception 'fresh VERIFIED UNIQUE PUBLISH_READY assessment required';
  end if;
  v_category_slug := lower(btrim(v_candidate.facts->>'category'));
  if v_category_slug is null or v_category_slug not in
       ('conference','competition','hackathon','education','meetup') then
    raise exception 'unsupported metadata Service category';
  end if;
  select * into v_category from public.categories
    where id=(p_record->>'category_id')::uuid;
  if not found or not v_category.is_active or v_category.slug <> v_category_slug then
    raise exception 'metadata Service category mismatch';
  end if;
  select * into v_service_source from public.sources
    where id=(p_record->>'source_id')::uuid;
  v_agent_host := regexp_replace(lower(substring(v_source.canonical_url
    from '^https://([^/:?#]+)')),'^www[.]','');
  v_service_host := regexp_replace(lower(substring(v_service_source.homepage_url
    from '^https://([^/:?#]+)')),'^www[.]','');
  if not found or not v_service_source.is_active or not (
       rtrim(lower(v_service_source.homepage_url),'/') =
         rtrim(lower(v_source.canonical_url),'/') or
       (v_agent_host is not null and v_agent_host=v_service_host and
        lower(btrim(v_service_source.name))=lower(btrim(v_source.name)))
     ) then
    raise exception 'approved Service Source mapping required';
  end if;
  if rtrim(lower(v_service_source.homepage_url),'/') =
       rtrim(lower(v_source.canonical_url),'/') then
    select count(*) into v_source_match_count from public.sources s
      where s.is_active and rtrim(lower(s.homepage_url),'/') =
        rtrim(lower(v_source.canonical_url),'/');
  else
    if exists (select 1 from public.sources s where s.is_active and
        rtrim(lower(s.homepage_url),'/') =
          rtrim(lower(v_source.canonical_url),'/')) then
      raise exception 'exact Service Source mapping takes precedence';
    end if;
    select count(*) into v_source_match_count from public.sources s
      where s.is_active and v_agent_host is not null and
        regexp_replace(lower(substring(s.homepage_url
          from '^https://([^/:?#]+)')),'^www[.]','')=v_agent_host and
        lower(btrim(s.name))=lower(btrim(v_source.name));
  end if;
  if v_source_match_count <> 1 then
    raise exception 'ambiguous approved Service Source mapping';
  end if;
  if p_record->>'external_id' is distinct from
       'runtime_agent_metadata:'||p_candidate_id::text or
     right(coalesce(p_record->>'slug',''),32) is distinct from
       replace(p_candidate_id::text,'-','') or
     p_record->>'official_url' is distinct from v_candidate.official_url or
     p_record->>'title' is distinct from v_candidate.facts->>'title' or
     p_record->>'summary' is distinct from v_candidate.facts->>'summary' or
     p_record->>'organizer' is distinct from v_candidate.facts->>'organizer' or
     (p_record->>'last_verified_at')::timestamptz is distinct from
       v_candidate.verified_at or
     p_record->>'status' not in ('scheduled','open','ongoing') or
     p_record->>'is_featured' is distinct from 'false' or
     p_evidence->>'evidence_ref' is distinct from
       'agent-metadata:'||p_candidate_id::text then
    raise exception 'metadata Candidate and Service envelope differ';
  end if;
  -- Only the Python preview's well-defined fields may enter the Service RPC.
  if exists (select 1 from jsonb_object_keys(p_record) as k(key)
             where k.key <> all(array['source_id','category_id','external_id',
               'slug','title','summary','organizer','official_url','is_featured',
               'last_verified_at','application_start_at','application_deadline_at',
               'event_start_at','event_end_at','status','published_at'])) then
    raise exception 'unsupported metadata Service field';
  end if;
  foreach v_field in array array['title','summary','organizer','official_url',
                               'application_start','application_deadline',
                               'application_method','category'] loop
    if nullif(btrim(v_candidate.facts->>v_field),'') is null or
       nullif(btrim(v_candidate.field_evidence #>> array[v_field,'evidence_reference']),'') is null or
       v_candidate.field_evidence #>> array[v_field,'source_url'] is distinct from
         v_candidate.official_url then
      raise exception 'metadata fact or provenance missing: %',v_field;
    end if;
  end loop;
  foreach v_field in array array['application_start','application_deadline',
                               'event_start','event_end'] loop
    v_stored_name := v_field||'_at';
    v_fact := nullif(btrim(v_candidate.facts->>v_field),'');
    v_precision := p_evidence #>> array[v_field,'precision'];
    if v_fact is null then
      if v_precision is distinct from 'UNKNOWN' or p_record ? v_stored_name then
        raise exception 'unknown metadata time must remain absent: %',v_field;
      end if;
    elsif v_precision='DATE_ONLY' then
      if p_evidence #>> array[v_field,'source_date'] is distinct from v_fact or
         (p_record->>v_stored_name)::timestamptz is distinct from
           (v_fact::date::timestamp at time zone 'UTC') then
        raise exception 'metadata date evidence mismatch: %',v_field;
      end if;
    elsif v_precision='EXACT_TIME' then
      if (p_evidence #>> array[v_field,'source_literal'])::timestamptz is distinct from
           v_fact::timestamptz or
         (p_record->>v_stored_name)::timestamptz is distinct from
           v_fact::timestamptz then
        raise exception 'metadata exact-time evidence mismatch: %',v_field;
      end if;
    else
      raise exception 'verified metadata time precision required: %',v_field;
    end if;
    if v_fact is not null and
       p_evidence #>> array[v_field,'source_ref'] is distinct from
         v_candidate.official_url||'#'||
         (v_candidate.field_evidence #>> array[v_field,'evidence_reference']) then
      raise exception 'metadata time provenance mismatch: %',v_field;
    end if;
  end loop;
  if v_category_slug <> 'competition' and
     (not v_candidate.facts ? 'event_start' or
      not v_candidate.facts ? 'event_end') then
    raise exception 'metadata category event window required';
  end if;
  -- The existing 004 RPC locks the official URL, creates the Opportunity,
  -- records Time Evidence and derives its current visible lifecycle status.
  select * into v_result from public.register_opportunity_with_evidence(
    p_record,p_evidence,null::uuid,true);
  if v_result.disposition is distinct from 'registered' then
    raise exception 'existing Service Opportunity is not this Candidate completion';
  end if;
  insert into public.agent_metadata_publish_completions
    (candidate_id,opportunity_id,approval_id,verification_at,
     registration_record,registration_evidence)
  values (p_candidate_id,v_result.opportunity_id,v_approval.id,
          v_candidate.verified_at,p_record,p_evidence);
  return query select v_result.opportunity_id, 'registered'::text;
end;
$$;
revoke all on function public.agent_publish_metadata_candidate(uuid,jsonb,jsonb)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_publish_metadata_candidate(uuid,jsonb,jsonb)
  to service_role;

commit;
