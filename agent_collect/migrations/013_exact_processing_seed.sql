-- PHASE 12J.1 exact seed. Reviewed and applied once to Preview only.
-- The full provenance and content-addressed reference must match before either
-- inserting a job or returning the ID of an existing one.
begin;

create function public.agent_seed_processing_job_exact(
  p_raw_document_id uuid, p_source_id uuid, p_run_id uuid,
  p_version text, p_content_ref text
) returns uuid language plpgsql security definer set search_path = pg_catalog as $$
declare v_job_id uuid;
begin
  if p_raw_document_id is null or p_source_id is null or p_run_id is null or
     p_version is null or p_version !~ '^[a-z0-9][a-z0-9._-]{0,127}$' or
     p_content_ref is null or p_content_ref !~
       '^supabase:[a-z][a-z0-9-]{2,62}:raw/sha256/[0-9a-f]{2}/[0-9a-f]{64}\.raw$' then
    raise exception 'invalid exact processing seed';
  end if;

  perform 1 from public.agent_raw_documents d
  join public.agent_collection_runs r on r.id=d.run_id and r.source_id=d.source_id
  where d.id=p_raw_document_id and d.source_id=p_source_id and d.run_id=p_run_id
    and r.status='SUCCEEDED' and d.content_ref=p_content_ref
    and d.content_sha256=substring(p_content_ref from '/([0-9a-f]{64})\.raw$')
    and left(d.content_sha256,2)=substring(p_content_ref from 'raw/sha256/([0-9a-f]{2})/')
  for share of d,r;
  if not found then return null; end if;

  insert into public.agent_processing_jobs(raw_document_id,processing_version)
  values (p_raw_document_id,p_version)
  on conflict (raw_document_id,processing_version) do nothing;

  select j.id into v_job_id from public.agent_processing_jobs j
  where j.raw_document_id=p_raw_document_id and j.processing_version=p_version;
  return v_job_id;
end;
$$;

alter function public.agent_seed_processing_job_exact(uuid,uuid,uuid,text,text) owner to postgres;
revoke all on function public.agent_seed_processing_job_exact(uuid,uuid,uuid,text,text)
  from public,anon,authenticated,service_role;
grant execute on function public.agent_seed_processing_job_exact(uuid,uuid,uuid,text,text)
  to service_role;

commit;
