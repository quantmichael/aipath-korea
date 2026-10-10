-- PHASE 4: Agent-only discovery evidence and candidate tables.
-- Apply separately after 001 and 002. This file is NOT applied automatically.
begin;

create table public.agent_discovery_queries (
  id uuid primary key default gen_random_uuid(),
  query_text text not null check (length(btrim(query_text)) > 0),
  provider text not null check (length(btrim(provider)) > 0),
  vertical_hint text not null default 'unknown'
    check (vertical_hint in ('conference', 'competition', 'hackathon', 'education',
      'meetup', 'grant', 'research', 'job', 'unknown')),
  status text not null default 'PENDING'
    check (status in ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')),
  result_count integer not null default 0 check (result_count >= 0),
  candidate_count integer not null default 0 check (candidate_count >= 0),
  promoted_count integer not null default 0 check (promoted_count >= 0),
  error_summary text,
  provenance jsonb not null default '{}'::jsonb
    check (jsonb_typeof(provenance) = 'object'),
  created_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz,
  constraint agent_discovery_counts_order check (
    promoted_count <= candidate_count and candidate_count <= result_count
  ),
  constraint agent_discovery_times_order check (
    (started_at is null or started_at >= created_at)
    and (completed_at is null or (started_at is not null and completed_at >= started_at))
  )
);
create index agent_discovery_queries_status_time_idx
  on public.agent_discovery_queries (status, created_at desc);

create table public.agent_search_results (
  id uuid primary key default gen_random_uuid(),
  query_id uuid not null references public.agent_discovery_queries(id),
  provider text not null,
  rank integer not null check (rank > 0),
  title text not null check (length(btrim(title)) > 0),
  original_url text not null,
  canonical_url text,
  domain text,
  snippet text not null default '',
  status text not null default 'DISCOVERED'
    check (status in ('DISCOVERED', 'FILTERED', 'CANDIDATE')),
  filter_reasons jsonb not null default '[]'::jsonb
    check (jsonb_typeof(filter_reasons) = 'array'),
  provider_metadata jsonb not null default '{}'::jsonb
    check (jsonb_typeof(provider_metadata) = 'object'),
  discovered_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  constraint agent_search_results_query_rank_unique unique (query_id, provider, rank),
  constraint agent_search_results_id_query_unique unique (id, query_id)
);
create index agent_search_results_query_time_idx
  on public.agent_search_results (query_id, discovered_at desc);
create index agent_search_results_canonical_idx
  on public.agent_search_results (canonical_url);

create table public.agent_source_candidates (
  id uuid primary key default gen_random_uuid(),
  query_id uuid not null references public.agent_discovery_queries(id),
  search_result_id uuid not null unique,
  original_url text not null,
  candidate_url text not null,
  canonical_url text not null unique,
  domain text not null,
  title text not null,
  snippet text not null default '',
  status text not null default 'EVALUATION_PENDING'
    check (status in ('EVALUATION_PENDING', 'ACCEPTED', 'REVIEW_REQUIRED',
      'REJECTED', 'PROMOTED')),
  evaluation_decision text check (evaluation_decision in ('ACCEPT', 'REVIEW', 'REJECT')),
  confidence double precision check (confidence between 0 and 1),
  reasons jsonb not null default '[]'::jsonb check (jsonb_typeof(reasons) = 'array'),
  evidence jsonb not null default '[]'::jsonb check (jsonb_typeof(evidence) = 'array'),
  collection_method text check (collection_method in ('http_html', 'http', 'rest_api',
    'rss', 'html', 'sitemap', 'search', 'browser')),
  evaluated_at timestamptz,
  promoted_source_id uuid references public.agent_sources(id),
  promoted_at timestamptz,
  provenance jsonb not null default '{}'::jsonb
    check (jsonb_typeof(provenance) = 'object'),
  discovered_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint agent_source_candidates_result_query_fk
    foreign key (search_result_id, query_id)
    references public.agent_search_results (id, query_id),
  constraint agent_source_candidates_promotion_check check (
    status <> 'PROMOTED' or (evaluation_decision = 'ACCEPT'
      and promoted_source_id is not null and promoted_at is not null)
  )
);
create index agent_source_candidates_query_status_idx
  on public.agent_source_candidates (query_id, status);
create index agent_source_candidates_promoted_source_idx
  on public.agent_source_candidates (promoted_source_id)
  where promoted_source_id is not null;

alter table public.agent_discovery_queries enable row level security;
alter table public.agent_search_results enable row level security;
alter table public.agent_source_candidates enable row level security;
revoke all on public.agent_discovery_queries from anon, authenticated;
revoke all on public.agent_search_results from anon, authenticated;
revoke all on public.agent_source_candidates from anon, authenticated;
grant all on public.agent_discovery_queries to service_role;
grant all on public.agent_search_results to service_role;
grant all on public.agent_source_candidates to service_role;

commit;
