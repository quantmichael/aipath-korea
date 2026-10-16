-- DRAFT: isolated PostgreSQL validation only. Not approved for Preview/Production.
-- Separate metadata approval from the existing full-raw collection approval.
-- No existing Source, RawDocument, Candidate, Queue, or Service row is changed.
begin;

create table public.agent_metadata_source_approvals (
  id uuid primary key default gen_random_uuid(),
  source_id uuid not null references public.agent_sources(id),
  cohort text not null check (cohort in ('synthetic','operational')),
  reviewer text not null check (length(btrim(reviewer)) between 3 and 200),
  reviewed_at timestamptz not null,
  expires_at timestamptz not null,
  official_url text not null check (official_url ~ '^https://[^[:space:]]+$'),
  permission_url text not null check (permission_url ~ '^https://[^[:space:]]+$'),
  permission_digest text not null check (permission_digest ~ '^[0-9a-f]{64}$'),
  organizer_relationship text not null check (length(btrim(organizer_relationship)) > 0),
  approved_organizer text not null check (length(btrim(approved_organizer)) > 0),
  page_type text not null check (page_type='INDIVIDUAL_NOTICE'),
  scope_origin text not null check (scope_origin ~ '^https://[^/[:space:]]+$'),
  path_prefix text not null check (left(path_prefix,1)='/' and right(path_prefix,1)='/'),
  officiality_confirmed boolean not null,
  public_read boolean not null,
  automated_access boolean not null,
  fact_use boolean not null,
  recheck_allowed boolean not null,
  raw_storage_allowed boolean not null default false,
  redistribution_allowed boolean not null default false,
  created_at timestamptz not null default clock_timestamp(),
  constraint agent_metadata_approval_interval check (expires_at > reviewed_at),
  constraint agent_metadata_approval_scope check
    (officiality_confirmed and public_read and automated_access and
     fact_use and recheck_allowed),
  unique (source_id, reviewed_at),
  unique (id, source_id, cohort, official_url)
);
create index agent_metadata_approval_source_idx
  on public.agent_metadata_source_approvals (source_id, reviewed_at desc);

create function public.agent_metadata_approval_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog as $$
declare v_source public.agent_sources%rowtype;
begin
  select * into v_source from public.agent_sources where id=new.source_id;
  if not found or v_source.source_type <> 'official' or
     v_source.status not in ('REVIEW','APPROVED','ACTIVE') or
     v_source.canonical_url <> new.official_url then
    raise exception 'metadata approval requires a reviewed matching official Source';
  end if;
  return new;
end;
$$;
create trigger agent_metadata_approval_insert_guard
before insert on public.agent_metadata_source_approvals
for each row execute function public.agent_metadata_approval_guard();
revoke all on function public.agent_metadata_approval_guard()
  from public, anon, authenticated, service_role;

create table public.agent_metadata_candidates (
  id uuid primary key default gen_random_uuid(),
  source_id uuid not null references public.agent_sources(id),
  approval_id uuid not null,
  cohort text not null check (cohort in ('synthetic','operational')),
  official_url text not null check (official_url ~ '^https://[^[:space:]]+$'),
  facts jsonb not null check (jsonb_typeof(facts)='object'),
  field_evidence jsonb not null check (jsonb_typeof(field_evidence)='object'),
  status text not null default 'REVIEW_REQUIRED'
    check (status in ('REVIEW_REQUIRED','PUBLISH_READY','REJECTED')),
  verification jsonb not null default '{}'::jsonb
    check (jsonb_typeof(verification)='object'),
  duplicate_result jsonb not null default '{}'::jsonb
    check (jsonb_typeof(duplicate_result)='object'),
  gate_decision jsonb not null default '{}'::jsonb
    check (jsonb_typeof(gate_decision)='object'),
  discovered_at timestamptz not null,
  verified_at timestamptz,
  created_at timestamptz not null default clock_timestamp(),
  constraint agent_metadata_candidate_approval_fk
    foreign key (approval_id,source_id,cohort,official_url)
    references public.agent_metadata_source_approvals(id,source_id,cohort,official_url),
  constraint agent_metadata_candidate_gate check (
    status <> 'PUBLISH_READY' or
    (verified_at is not null and
     facts ?& array['title','summary','organizer','official_url',
                    'application_start','application_deadline','event_start',
                    'event_end','application_method','category'] and
     field_evidence ?& array['title','summary','organizer','official_url',
                              'application_start','application_deadline','event_start',
                              'event_end','application_method','category'] and
     verification->>'status'='VERIFIED'
     and duplicate_result->>'status'='UNIQUE'
     and gate_decision->>'status'='PUBLISH_READY')
  )
);
create index agent_metadata_candidate_source_idx
  on public.agent_metadata_candidates (source_id, created_at desc);
create index agent_metadata_candidate_status_idx
  on public.agent_metadata_candidates (status, created_at desc);

alter table public.agent_metadata_source_approvals enable row level security;
alter table public.agent_metadata_candidates enable row level security;
revoke all on public.agent_metadata_source_approvals,
  public.agent_metadata_candidates from public, anon, authenticated, service_role;
grant select, insert on public.agent_metadata_source_approvals to service_role;
grant select, insert, update on public.agent_metadata_candidates to service_role;

commit;
