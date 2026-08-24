-- AI PATH KOREA - collect pipeline schema extension
-- Safe to run more than once.

begin;

alter table public.sources
  add column if not exists source_type text not null default 'regular',
  add column if not exists collection_url text,
  add column if not exists is_active boolean not null default true,
  add column if not exists last_collected_at timestamptz,
  add column if not exists last_collection_status text;

do $$
declare
  constraint_name text;
begin
  select conname
  into constraint_name
  from pg_constraint
  where conrelid = 'public.sources'::regclass
    and contype = 'c'
    and pg_get_constraintdef(oid) like '%collection_method%'
  limit 1;

  if constraint_name is not null then
    execute format('alter table public.sources drop constraint %I', constraint_name);
  end if;
end;
$$;

alter table public.sources
  drop constraint if exists sources_source_type_check,
  drop constraint if exists sources_collection_method_check,
  drop constraint if exists sources_last_collection_status_check;

alter table public.sources
  add constraint sources_source_type_check
    check (source_type in ('regular', 'irregular', 'discovery')),
  add constraint sources_collection_method_check
    check (collection_method in (
      'manual',
      'api',
      'rss',
      'html',
      'ai_extract',
      'ai_search',
      'submission'
    )),
  add constraint sources_last_collection_status_check
    check (last_collection_status in ('success', 'failed', 'partial'));

update public.sources
set collection_url = homepage_url
where collection_url is null
  and homepage_url is not null;

create table if not exists public.collection_runs (
  id uuid primary key default gen_random_uuid(),
  source_id uuid references public.sources(id) on delete set null,
  collection_method text not null
    check (collection_method in (
      'manual',
      'api',
      'rss',
      'html',
      'ai_extract',
      'ai_search',
      'submission'
    )),
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  status text not null default 'running'
    check (status in ('running', 'success', 'failed', 'partial')),
  found_count integer not null default 0 check (found_count >= 0),
  inserted_count integer not null default 0 check (inserted_count >= 0),
  updated_count integer not null default 0 check (updated_count >= 0),
  duplicate_count integer not null default 0 check (duplicate_count >= 0),
  failed_count integer not null default 0 check (failed_count >= 0),
  error_message text,
  created_at timestamptz not null default now()
);

create index if not exists collection_runs_source_id_idx
  on public.collection_runs (source_id);

create index if not exists collection_runs_started_at_idx
  on public.collection_runs (started_at desc);

create table if not exists public.opportunity_candidates (
  id uuid primary key default gen_random_uuid(),
  source_id uuid references public.sources(id) on delete set null,
  collection_run_id uuid references public.collection_runs(id) on delete set null,
  category_id uuid references public.categories(id) on delete set null,
  external_id text,
  title text,
  slug text,
  summary text,
  description text,
  organizer text,
  target_audience text,
  difficulty text check (difficulty in ('beginner', 'intermediate', 'advanced', 'all')),
  format text check (format in ('online', 'offline', 'hybrid')),
  region text,
  venue text,
  price_type text check (price_type in ('free', 'paid', 'mixed', 'unknown')),
  price_text text,
  application_start_at timestamptz,
  application_deadline_at timestamptz,
  event_start_at timestamptz,
  event_end_at timestamptz,
  official_url text,
  image_url text,
  candidate_status text not null default 'pending'
    check (candidate_status in (
      'pending',
      'needs_review',
      'verified',
      'rejected',
      'promoted',
      'duplicate'
    )),
  validation_errors jsonb not null default '[]'::jsonb,
  raw_payload jsonb not null default '{}'::jsonb,
  discovered_at timestamptz not null default now(),
  last_verified_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists opportunity_candidates_source_external_id_unique
  on public.opportunity_candidates (source_id, external_id)
  where source_id is not null and external_id is not null;

create unique index if not exists opportunity_candidates_official_url_unique
  on public.opportunity_candidates (official_url)
  where official_url is not null;

create index if not exists opportunity_candidates_status_idx
  on public.opportunity_candidates (candidate_status);

create index if not exists opportunity_candidates_discovered_at_idx
  on public.opportunity_candidates (discovered_at desc);

drop trigger if exists opportunity_candidates_set_updated_at
  on public.opportunity_candidates;

create trigger opportunity_candidates_set_updated_at
before update on public.opportunity_candidates
for each row execute function public.set_updated_at();

alter table public.collection_runs enable row level security;
alter table public.opportunity_candidates enable row level security;

revoke all on table public.collection_runs from anon, authenticated;
revoke all on table public.opportunity_candidates from anon, authenticated;

grant all on table public.collection_runs to service_role;
grant all on table public.opportunity_candidates to service_role;

commit;
