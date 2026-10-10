-- PHASE 8 Agent-only processing queue. CREATED ONLY: do not apply automatically.
-- Requires 001-005. Existing Service tables and Source lifecycle are untouched.
begin;

create index agent_raw_processing_seed_idx
  on public.agent_raw_documents(created_at,id);

create table public.agent_processing_jobs (
  id uuid primary key default gen_random_uuid(),
  raw_document_id uuid not null references public.agent_raw_documents(id),
  processing_version text not null check (processing_version ~ '^[a-z0-9][a-z0-9._-]{0,127}$'),
  status text not null default 'PENDING' check (status in
    ('PENDING','PROCESSING','SUCCEEDED','REVIEW_REQUIRED','REJECTED','FAILED','BUDGET_BLOCKED')),
  attempt_count integer not null default 0 check (attempt_count >= 0),
  next_attempt_at timestamptz not null default now(),
  claim_id uuid unique,
  worker_id text,
  claimed_at timestamptz,
  claim_expires_at timestamptz,
  candidate_id uuid references public.agent_candidates(id),
  verification_id uuid references public.agent_verifications(id),
  gate_status text check (gate_status in ('PUBLISH_READY','REVIEW_REQUIRED','REJECTED')),
  last_error_code text,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (raw_document_id, processing_version),
  constraint agent_processing_claim_consistent check (
    (claim_id is null and worker_id is null and claimed_at is null and claim_expires_at is null)
    or (claim_id is not null and nullif(btrim(worker_id),'') is not null and
        claimed_at is not null and claim_expires_at > claimed_at)),
  constraint agent_processing_terminal_complete check (
    status not in ('SUCCEEDED','REVIEW_REQUIRED','REJECTED') or completed_at is not null)
);
create index agent_processing_due_idx on public.agent_processing_jobs
  (next_attempt_at,id) where status in ('PENDING','FAILED','BUDGET_BLOCKED','PROCESSING');
create index agent_processing_raw_idx on public.agent_processing_jobs(raw_document_id);

create table public.agent_processing_completions (
  claim_id uuid primary key,
  job_id uuid not null references public.agent_processing_jobs(id),
  outcome text not null check (outcome in
    ('SUCCEEDED','REVIEW_REQUIRED','REJECTED','FAILED','BUDGET_BLOCKED')),
  completed_at timestamptz not null,
  candidate_id uuid references public.agent_candidates(id),
  verification_id uuid references public.agent_verifications(id),
  error_code text
);
create index agent_processing_completions_job_idx on public.agent_processing_completions(job_id);

alter table public.agent_processing_jobs enable row level security;
alter table public.agent_processing_completions enable row level security;
revoke all on public.agent_processing_jobs from public, anon, authenticated, service_role;
revoke all on public.agent_processing_completions from public, anon, authenticated, service_role;
grant select on public.agent_processing_jobs, public.agent_processing_completions to service_role;

-- Bounded seeding only after a successful CollectionRun. A provider prefix
-- prevents a persistent worker from claiming old local-only raw references.
create function public.agent_seed_processing_jobs(
  p_version text, p_ref_prefix text, p_limit integer
) returns integer language plpgsql security definer set search_path = pg_catalog as $$
declare v_count integer;
begin
  if p_version is null or p_version !~ '^[a-z0-9][a-z0-9._-]{0,127}$' or
     p_ref_prefix is null or not (p_ref_prefix = 'local:' or
       p_ref_prefix ~ '^supabase:[a-z][a-z0-9-]{2,62}:$') or
     p_limit is null or p_limit not between 1 and 100 then
    raise exception 'invalid processing seed policy';
  end if;
  insert into public.agent_processing_jobs(raw_document_id,processing_version)
  select d.id,p_version from public.agent_raw_documents d
  join public.agent_collection_runs r on r.id=d.run_id and r.source_id=d.source_id
  where r.status='SUCCEEDED' and left(d.content_ref,length(p_ref_prefix))=p_ref_prefix
    and not exists (select 1 from public.agent_processing_jobs j
                    where j.raw_document_id=d.id and j.processing_version=p_version)
  order by d.created_at,d.id limit p_limit
  on conflict (raw_document_id,processing_version) do nothing;
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;
alter function public.agent_seed_processing_jobs(text,text,integer) owner to postgres;
revoke all on function public.agent_seed_processing_jobs(text,text,integer)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_seed_processing_jobs(text,text,integer) to service_role;

create function public.agent_list_due_processing(p_version text,p_ref_prefix text,p_limit integer)
returns setof public.agent_processing_jobs
language plpgsql security definer set search_path = pg_catalog as $$
declare v_now timestamptz := clock_timestamp();
begin
  if p_version is null or p_version !~ '^[a-z0-9][a-z0-9._-]{0,127}$' or
     p_ref_prefix is null or not (p_ref_prefix='local:' or
       p_ref_prefix ~ '^supabase:[a-z][a-z0-9-]{2,62}:$') or
     p_limit is null or p_limit not between 1 and 100 then
    raise exception 'invalid processing due query';
  end if;
  return query select j.* from public.agent_processing_jobs j
    join public.agent_raw_documents d on d.id=j.raw_document_id
    where j.processing_version=p_version and j.next_attempt_at<=v_now
      and left(d.content_ref,length(p_ref_prefix))=p_ref_prefix
      and (j.status in ('PENDING','FAILED','BUDGET_BLOCKED') or
           (j.status='PROCESSING' and j.claim_expires_at<=v_now))
    order by j.next_attempt_at,j.id limit p_limit;
end;
$$;
alter function public.agent_list_due_processing(text,text,integer) owner to postgres;
revoke all on function public.agent_list_due_processing(text,text,integer)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_list_due_processing(text,text,integer) to service_role;

-- Read-only preview includes successful RawDocuments that have not yet been
-- seeded. Dry run must not create a job or call Storage/LLM.
create function public.agent_preview_processing(
  p_version text,p_ref_prefix text,p_limit integer
) returns table(job_id uuid,raw_document_id uuid,status text,next_attempt_at timestamptz)
language plpgsql security definer set search_path = pg_catalog as $$
declare v_now timestamptz := clock_timestamp();
begin
  if p_version is null or p_version !~ '^[a-z0-9][a-z0-9._-]{0,127}$' or
     p_ref_prefix is null or not (p_ref_prefix='local:' or
       p_ref_prefix ~ '^supabase:[a-z][a-z0-9-]{2,62}:$') or
     p_limit is null or p_limit not between 1 and 100 then
    raise exception 'invalid processing preview';
  end if;
  return query
  select q.job_id,q.raw_document_id,q.status,q.next_attempt_at from (
    select j.id as job_id,j.raw_document_id,j.status,j.next_attempt_at
    from public.agent_processing_jobs j
    join public.agent_raw_documents d on d.id=j.raw_document_id
    where j.processing_version=p_version and j.next_attempt_at<=v_now
      and left(d.content_ref,length(p_ref_prefix))=p_ref_prefix
      and (j.status in ('PENDING','FAILED','BUDGET_BLOCKED') or
           (j.status='PROCESSING' and j.claim_expires_at<=v_now))
    union all
    select null::uuid,d.id,'PENDING'::text,d.created_at
    from public.agent_raw_documents d
    join public.agent_collection_runs r on r.id=d.run_id and r.source_id=d.source_id
    where r.status='SUCCEEDED' and left(d.content_ref,length(p_ref_prefix))=p_ref_prefix
      and not exists (select 1 from public.agent_processing_jobs j
                      where j.raw_document_id=d.id and j.processing_version=p_version)
  ) q order by q.next_attempt_at,q.raw_document_id limit p_limit;
end;
$$;
alter function public.agent_preview_processing(text,text,integer) owner to postgres;
revoke all on function public.agent_preview_processing(text,text,integer)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_preview_processing(text,text,integer) to service_role;

-- A checkpoint contains only Agent IDs. It lets an expired lease reconcile a
-- committed Quality Gate result without repeating LLM extraction.
create function public.agent_processing_checkpoint(
  p_job_id uuid,p_claim_id uuid,p_candidate_id uuid,p_verification_id uuid,p_gate_status text
) returns boolean language plpgsql security definer set search_path = pg_catalog as $$
declare v_job public.agent_processing_jobs%rowtype;
begin
  select * into v_job from public.agent_processing_jobs where id=p_job_id for update;
  if not found or v_job.status<>'PROCESSING' or v_job.claim_id is distinct from p_claim_id or
     v_job.claim_expires_at<=clock_timestamp() then raise exception 'invalid processing claim'; end if;
  if p_candidate_id is null or not exists (
    select 1 from public.agent_candidates c where c.id=p_candidate_id
      and c.raw_document_id=v_job.raw_document_id) then
    raise exception 'candidate does not match raw document';
  end if;
  if v_job.candidate_id is not null and v_job.candidate_id is distinct from p_candidate_id then
    raise exception 'processing Candidate checkpoint is immutable';
  end if;
  if p_verification_id is not null then
    if p_gate_status not in ('PUBLISH_READY','REVIEW_REQUIRED','REJECTED') or
       not exists (select 1 from public.agent_verifications v
                   where v.id=p_verification_id and v.candidate_id=p_candidate_id
                     and v.checks #>> '{gate_decision,status}'=p_gate_status) then
      raise exception 'verification does not match candidate';
    end if;
  elsif p_gate_status is not null then
    raise exception 'gate status requires verification';
  end if;
  if v_job.verification_id is not null and
     (p_verification_id is distinct from v_job.verification_id or
      p_gate_status is distinct from v_job.gate_status or
      p_candidate_id is distinct from v_job.candidate_id) then
    raise exception 'completed Gate checkpoint cannot be replaced';
  end if;
  update public.agent_processing_jobs set candidate_id=p_candidate_id,
    verification_id=p_verification_id,gate_status=p_gate_status,
    updated_at=clock_timestamp() where id=p_job_id;
  return true;
end;
$$;
alter function public.agent_processing_checkpoint(uuid,uuid,uuid,uuid,text) owner to postgres;
revoke all on function public.agent_processing_checkpoint(uuid,uuid,uuid,uuid,text)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_processing_checkpoint(uuid,uuid,uuid,uuid,text) to service_role;

create function public.agent_claim_processing(
  p_job_id uuid,p_worker_id text,p_claim_id uuid,p_lease_seconds integer,
  p_backoff_minutes integer[],p_max_attempts integer
) returns setof public.agent_processing_jobs
language plpgsql security definer set search_path = pg_catalog as $$
declare
  v_job public.agent_processing_jobs%rowtype;
  v_now timestamptz;
  v_attempts integer;
  v_delay integer;
begin
  if p_job_id is null or p_claim_id is null or nullif(btrim(p_worker_id),'') is null or
     p_lease_seconds is null or p_lease_seconds not between 1 and 86400 or
     p_max_attempts is null or p_max_attempts not between 1 and 100 or
     coalesce(array_length(p_backoff_minutes,1),0)<1 or
     exists (select 1 from unnest(p_backoff_minutes) x where x is null or x<1) then
    raise exception 'invalid processing claim policy';
  end if;
  select * into v_job from public.agent_processing_jobs where id=p_job_id for update;
  if not found then return; end if;
  if not exists (select 1 from public.agent_raw_documents d
                 join public.agent_collection_runs r on r.id=d.run_id and r.source_id=d.source_id
                 where d.id=v_job.raw_document_id and r.status='SUCCEEDED') then return; end if;
  v_now:=clock_timestamp();
  if v_job.status in ('SUCCEEDED','REVIEW_REQUIRED','REJECTED') then return; end if;
  if v_job.status='PROCESSING' then
    if v_job.claim_expires_at>v_now then return; end if;
    -- A completed Gate checkpoint is terminal even if the worker crashed
    -- before completion. No new provider call is allowed for this job/version.
    if v_job.verification_id is not null and v_job.gate_status is not null then
      insert into public.agent_processing_completions
        (claim_id,job_id,outcome,completed_at,candidate_id,verification_id)
      values (v_job.claim_id,v_job.id,case when v_job.gate_status='PUBLISH_READY'
        then 'SUCCEEDED' else v_job.gate_status end,v_now,v_job.candidate_id,v_job.verification_id);
      update public.agent_processing_jobs set
        status=case when gate_status='PUBLISH_READY' then 'SUCCEEDED' else gate_status end,
        completed_at=v_now,last_error_code=null,
        claim_id=null,worker_id=null,claimed_at=null,
        claim_expires_at=null,updated_at=v_now where id=p_job_id;
      return;
    end if;
    v_attempts:=v_job.attempt_count+1;
    v_delay:=p_backoff_minutes[least(v_attempts,array_length(p_backoff_minutes,1))];
    insert into public.agent_processing_completions(claim_id,job_id,outcome,completed_at,error_code)
      values (v_job.claim_id,v_job.id,'FAILED',v_now,'LEASE_EXPIRED');
    update public.agent_processing_jobs set
      status=case when v_attempts>=p_max_attempts then 'REVIEW_REQUIRED' else 'FAILED' end,
      attempt_count=v_attempts,next_attempt_at=v_now+make_interval(mins=>v_delay),
      last_error_code='LEASE_EXPIRED',
      completed_at=case when v_attempts>=p_max_attempts then v_now else null end,
      claim_id=null,worker_id=null,claimed_at=null,claim_expires_at=null,updated_at=v_now
      where id=p_job_id;
    return;
  end if;
  if v_job.next_attempt_at>v_now then return; end if;
  -- Serialize billable processing across workers of this version. Existing
  -- ExtractionBudgetGuard remains the final budget decision before every call.
  if not pg_try_advisory_xact_lock(hashtextextended('agent-processing:'||v_job.processing_version,0))
     or exists (select 1 from public.agent_processing_jobs other
                where other.processing_version=v_job.processing_version
                  and other.id<>v_job.id and other.status='PROCESSING'
                  and other.claim_expires_at>v_now) then return; end if;
  update public.agent_processing_jobs set status='PROCESSING',claim_id=p_claim_id,
    worker_id=p_worker_id,claimed_at=v_now,
    claim_expires_at=v_now+make_interval(secs=>p_lease_seconds),updated_at=v_now
    where id=p_job_id returning * into v_job;
  return next v_job;
end;
$$;
alter function public.agent_claim_processing(uuid,text,uuid,integer,integer[],integer) owner to postgres;
revoke all on function public.agent_claim_processing(uuid,text,uuid,integer,integer[],integer)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_claim_processing(uuid,text,uuid,integer,integer[],integer)
  to service_role;

create function public.agent_finish_processing(
  p_job_id uuid,p_claim_id uuid,p_outcome text,p_candidate_id uuid,
  p_verification_id uuid,p_gate_status text,p_error_code text,
  p_backoff_minutes integer[],p_max_attempts integer
) returns text language plpgsql security definer set search_path = pg_catalog as $$
declare
  v_job public.agent_processing_jobs%rowtype;
  v_prior public.agent_processing_completions%rowtype;
  v_now timestamptz;
  v_attempts integer;
  v_delay integer;
  v_outcome text;
  v_error_code text;
begin
  if p_job_id is null or p_claim_id is null or p_outcome is null or
     p_outcome not in ('SUCCEEDED','REVIEW_REQUIRED','REJECTED','FAILED','BUDGET_BLOCKED') or
     p_max_attempts is null or p_max_attempts not between 1 and 100 or
     coalesce(array_length(p_backoff_minutes,1),0)<1 or
     exists (select 1 from unnest(p_backoff_minutes) x where x is null or x<1) then
    raise exception 'invalid processing completion';
  end if;
  select * into v_job from public.agent_processing_jobs where id=p_job_id for update;
  if not found then raise exception 'processing job not found'; end if;
  select * into v_prior from public.agent_processing_completions where claim_id=p_claim_id;
  if found then
    if v_prior.job_id<>p_job_id or (p_candidate_id is not null and
       p_candidate_id is distinct from v_prior.candidate_id) or
       (p_verification_id is not null and p_verification_id is distinct from v_prior.verification_id) then
      raise exception 'completed processing claim mismatch';
    end if;
    return v_prior.outcome;
  end if;
  if v_job.status<>'PROCESSING' or v_job.claim_id is distinct from p_claim_id or
     v_job.claim_expires_at<=clock_timestamp() then
    raise exception 'stale processing claim';
  end if;
  -- A committed Gate checkpoint wins over late failure/exception handling.
  if v_job.verification_id is not null and v_job.gate_status is not null then
    if (p_candidate_id is not null and p_candidate_id is distinct from v_job.candidate_id) or
       (p_verification_id is not null and p_verification_id is distinct from v_job.verification_id) or
       (p_gate_status is not null and p_gate_status is distinct from v_job.gate_status) then
      raise exception 'processing checkpoint mismatch';
    end if;
    v_outcome:=case when v_job.gate_status='PUBLISH_READY' then 'SUCCEEDED'
                    else v_job.gate_status end;
  else
    v_outcome:=p_outcome;
  end if;
  if v_outcome in ('SUCCEEDED','REVIEW_REQUIRED','REJECTED') then
    if v_job.verification_id is null or v_job.candidate_id is null or
       v_job.gate_status is null or
       v_outcome is distinct from (case when v_job.gate_status='PUBLISH_READY'
         then 'SUCCEEDED' else v_job.gate_status end) or
       (p_candidate_id is not null and p_candidate_id is distinct from v_job.candidate_id) or
       (p_verification_id is not null and p_verification_id is distinct from v_job.verification_id) or
       (p_gate_status is not null and p_gate_status is distinct from v_job.gate_status) then
      raise exception 'terminal outcome requires matching Gate checkpoint';
    end if;
  elsif p_candidate_id is not null and p_candidate_id is distinct from v_job.candidate_id then
    raise exception 'candidate does not match checkpoint';
  end if;
  v_now:=clock_timestamp();
  v_error_code:=case when v_outcome in ('SUCCEEDED','REVIEW_REQUIRED','REJECTED')
                     and v_job.verification_id is not null then null else p_error_code end;
  if v_outcome='FAILED' then
    v_attempts:=v_job.attempt_count+1;
    v_delay:=p_backoff_minutes[least(v_attempts,array_length(p_backoff_minutes,1))];
    if v_attempts>=p_max_attempts then v_outcome:='REVIEW_REQUIRED'; end if;
  elsif v_outcome='BUDGET_BLOCKED' then
    v_attempts:=v_job.attempt_count;
    v_delay:=p_backoff_minutes[1];
  else
    v_attempts:=v_job.attempt_count;
    v_delay:=0;
  end if;
  insert into public.agent_processing_completions
    (claim_id,job_id,outcome,completed_at,candidate_id,verification_id,error_code)
  values (p_claim_id,p_job_id,v_outcome,v_now,v_job.candidate_id,v_job.verification_id,v_error_code);
  update public.agent_processing_jobs set status=v_outcome,attempt_count=v_attempts,
    next_attempt_at=v_now+make_interval(mins=>v_delay),
    last_error_code=v_error_code,
    completed_at=case when v_outcome in ('SUCCEEDED','REVIEW_REQUIRED','REJECTED') then v_now else null end,
    claim_id=null,worker_id=null,claimed_at=null,claim_expires_at=null,updated_at=v_now
    where id=p_job_id;
  return v_outcome;
end;
$$;
alter function public.agent_finish_processing(uuid,uuid,text,uuid,uuid,text,text,integer[],integer) owner to postgres;
revoke all on function public.agent_finish_processing(uuid,uuid,text,uuid,uuid,text,text,integer[],integer)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_finish_processing(uuid,uuid,text,uuid,uuid,text,text,integer[],integer)
  to service_role;

commit;
