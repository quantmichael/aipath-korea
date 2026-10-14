-- DRAFT ONLY. Apply after 002 (and 003 on fresh databases) before deploying
-- callers. Production apply is outside STEP 2. No existing row is modified.
begin;

create function public.register_opportunity_with_evidence(
  p_record jsonb, p_evidence jsonb,
  p_candidate_id uuid default null, p_publish boolean default false
) returns table(opportunity_id uuid, disposition text)
language plpgsql security definer set search_path = pg_catalog as $$
declare
  v_input public.opportunities%rowtype;
  v_existing public.opportunities%rowtype;
  v_candidate public.opportunity_candidates%rowtype;
  v_scope public.opportunity_status_automation_scope%rowtype;
  v_id uuid;
  v_status text;
  v_matches integer;
begin
  if p_record is null or jsonb_typeof(p_record) <> 'object'
     or p_evidence is null or jsonb_typeof(p_evidence) <> 'object' then
    raise exception 'invalid registration envelope';
  end if;
  if exists (
    select 1 from jsonb_object_keys(p_record) as k(key)
    where k.key <> all(array[
      'source_id','category_id','external_id','title','slug','summary',
      'description','organizer','target_audience','difficulty','format',
      'region','venue','price_type','price_text','application_start_at',
      'application_deadline_at','event_start_at','event_end_at',
      'official_url','image_url','status','is_featured','published_at',
      'last_verified_at'])
  ) then raise exception 'unsupported Opportunity registration field'; end if;

  v_input := jsonb_populate_record(null::public.opportunities, p_record);
  if v_input.source_id is null or v_input.category_id is null
     or nullif(btrim(v_input.title),'') is null
     or nullif(btrim(v_input.slug),'') is null
     or nullif(btrim(v_input.summary),'') is null
     or nullif(btrim(v_input.organizer),'') is null
     or nullif(btrim(v_input.official_url),'') is null then
    raise exception 'Opportunity registration fields missing';
  end if;
  if p_candidate_id is null then
    if not p_publish or v_input.status not in ('scheduled','open','ongoing') then
      raise exception 'Agent registration requires an eligible public status';
    end if;
    v_status := v_input.status;
  else
    select * into v_candidate from public.opportunity_candidates
      where id = p_candidate_id for update;
    if not found or v_candidate.candidate_status not in ('verified','promoted')
       or v_candidate.source_id is distinct from v_input.source_id
       or v_candidate.external_id is distinct from v_input.external_id
       or v_candidate.slug is distinct from v_input.slug
       or v_candidate.official_url is distinct from v_input.official_url then
      raise exception 'Collect candidate identity or verification changed';
    end if;
    v_status := case when p_publish then 'open' else 'draft' end;
  end if;
  if p_publish and coalesce(p_evidence #>> '{application_deadline,precision}', 'UNKNOWN')
                  not in ('EXACT_TIME','DATE_ONLY')
     and coalesce(p_evidence #>> '{event_end,precision}', 'UNKNOWN') <> 'EXACT_TIME' then
    raise exception 'public registration requires a verified lifecycle boundary';
  end if;

  -- Serialize the same official URL. Unique slug and source/external-id
  -- constraints independently reject concurrent alternate-key collisions.
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(v_input.official_url, 20261013));
  select count(*) into v_matches from public.opportunities o
  where o.slug = v_input.slug or o.official_url = v_input.official_url
     or (v_input.external_id is not null
         and o.source_id = v_input.source_id
         and o.external_id = v_input.external_id);
  if v_matches > 1 then raise exception 'conflicting Opportunity identities'; end if;
  if v_matches = 1 then
    select * into v_existing from public.opportunities o
    where o.slug = v_input.slug or o.official_url = v_input.official_url
       or (v_input.external_id is not null
           and o.source_id = v_input.source_id
           and o.external_id = v_input.external_id)
    for update;
    if v_existing.status <> 'draft' then
      return query select v_existing.id, 'protected_existing'::text;
      return;
    end if;
    -- Agent publication must never mutate a pre-existing draft.
    if p_candidate_id is null then
      raise exception 'Agent registration conflicts with an existing draft';
    end if;
    update public.opportunities o set
      source_id=v_input.source_id, category_id=v_input.category_id,
      external_id=v_input.external_id, title=v_input.title, slug=v_input.slug,
      summary=v_input.summary, description=v_input.description,
      organizer=v_input.organizer, target_audience=v_input.target_audience,
      difficulty=v_input.difficulty, format=v_input.format,
      region=v_input.region, venue=v_input.venue,
      price_type=v_input.price_type, price_text=v_input.price_text,
      application_start_at=v_input.application_start_at,
      application_deadline_at=v_input.application_deadline_at,
      event_start_at=v_input.event_start_at, event_end_at=v_input.event_end_at,
      official_url=v_input.official_url, image_url=v_input.image_url,
      last_verified_at=v_input.last_verified_at
    where o.id=v_existing.id and o.status='draft'
    returning o.id into v_id;
    if v_id is null then raise exception 'draft changed concurrently'; end if;
  else
    insert into public.opportunities
      (source_id,category_id,external_id,title,slug,summary,description,
       organizer,target_audience,difficulty,format,region,venue,price_type,
       price_text,application_start_at,application_deadline_at,event_start_at,
       event_end_at,official_url,image_url,status,is_featured,last_verified_at)
    values
      (v_input.source_id,v_input.category_id,v_input.external_id,v_input.title,
       v_input.slug,v_input.summary,v_input.description,v_input.organizer,
       v_input.target_audience,v_input.difficulty,v_input.format,v_input.region,
       v_input.venue,v_input.price_type,v_input.price_text,
       v_input.application_start_at,v_input.application_deadline_at,
       v_input.event_start_at,v_input.event_end_at,v_input.official_url,
       v_input.image_url,'draft',false,v_input.last_verified_at)
    returning id into v_id;
  end if;

  -- A raised error rolls back the INSERT/UPDATE and candidate state too.
  perform public.record_opportunity_status_evidence(v_id,p_evidence);
  if p_publish then
    select * into v_scope from public.opportunity_status_automation_scope s
      where s.opportunity_id=v_id;
    v_status := public.opportunity_status_target(
      clock_timestamp(),
      v_input.application_start_at, v_scope.application_start_precision,
      v_scope.application_start_source_date,
      v_input.application_deadline_at, v_scope.application_deadline_precision,
      v_scope.application_deadline_source_date,
      v_input.event_start_at, v_scope.event_start_precision,
      v_input.event_end_at, v_scope.event_end_precision);
    if v_status not in ('scheduled','open','ongoing') then
      raise exception 'public registration has no current recruitable lifecycle state';
    end if;
    update public.opportunities o
      set status=v_status, published_at=clock_timestamp()
      where o.id=v_id and o.status='draft';
    if not found then raise exception 'Opportunity publication lost draft state'; end if;
  end if;
  if p_candidate_id is not null then
    update public.opportunity_candidates c set candidate_status='promoted'
      where c.id=p_candidate_id and c.candidate_status in ('verified','promoted');
    if not found then raise exception 'Collect candidate state changed'; end if;
  end if;
  return query select v_id, 'registered'::text;
end;
$$;

revoke all on function public.register_opportunity_with_evidence(jsonb,jsonb,uuid,boolean)
  from public, anon, authenticated;
grant execute on function public.register_opportunity_with_evidence(jsonb,jsonb,uuid,boolean)
  to service_role;

commit;
