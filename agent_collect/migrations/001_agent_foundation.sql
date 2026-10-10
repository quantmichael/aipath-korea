-- PHASE 2: Agent-owned tables in the existing Supabase PostgreSQL project.
-- Apply separately after review. No existing service table is altered.
-- All seven tables live in public with the agent_ prefix for logical isolation.

begin;

create table public.agent_sources (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  source_url text not null,
  canonical_url text not null unique,
  source_type text not null default 'unknown'
    check (source_type in ('official', 'aggregator', 'community', 'unknown')),
  collection_method text
    check (collection_method in ('http', 'rest_api', 'rss', 'html', 'sitemap', 'search')),
  category_hint text,
  country_code text,
  language_code text,
  status text not null default 'CANDIDATE'
    check (status in ('CANDIDATE', 'ACTIVE', 'HOLD', 'REJECTED')),
  is_active boolean not null default false,
  collection_interval_minutes integer
    check (collection_interval_minutes is null or collection_interval_minutes > 0),
  last_success_at timestamptz,
  last_failure_at timestamptz,
  discovery_method text,
  agent_rationale text,
  confidence double precision
    check (confidence is null or (confidence >= 0 and confidence <= 1)),
  provenance jsonb not null default '{}'::jsonb,
  collection_config jsonb not null default '{}'::jsonb,
  discovered_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint agent_sources_urls_http check (
    source_url ~* '^https?://[^[:space:]]+$'
    and canonical_url ~* '^https?://[^[:space:]]+$'
  ),
  constraint agent_sources_active_status check (not is_active or status = 'ACTIVE'),
  constraint agent_sources_provenance_object check (jsonb_typeof(provenance) = 'object'),
  constraint agent_sources_config_object check (jsonb_typeof(collection_config) = 'object')
);

create index agent_sources_status_due_idx
  on public.agent_sources (status, is_active, last_success_at);

create table public.agent_collection_runs (
  id uuid primary key default gen_random_uuid(),
  source_id uuid not null references public.agent_sources(id),
  status text not null default 'RUNNING'
    check (status in ('RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED')),
  collection_method text
    check (collection_method in ('http', 'rest_api', 'rss', 'html', 'sitemap', 'search')),
  source_config_snapshot jsonb not null default '{}'::jsonb,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  found_count integer not null default 0 check (found_count >= 0),
  stored_count integer not null default 0 check (stored_count >= 0),
  failed_count integer not null default 0 check (failed_count >= 0),
  error_summary text,
  created_at timestamptz not null default now(),
  constraint agent_runs_time_order check (finished_at is null or finished_at >= started_at),
  constraint agent_runs_snapshot_object check (jsonb_typeof(source_config_snapshot) = 'object')
);

alter table public.agent_collection_runs
  add constraint agent_runs_id_source_unique unique (id, source_id);

create index agent_runs_source_started_idx
  on public.agent_collection_runs (source_id, started_at desc);

create table public.agent_raw_documents (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.agent_collection_runs(id),
  source_id uuid not null references public.agent_sources(id),
  source_url text not null,
  canonical_url text,
  media_type text,
  content_ref text not null,
  content_sha256 text not null check (content_sha256 ~ '^[0-9a-f]{64}$'),
  http_status integer check (http_status is null or http_status between 100 and 599),
  collected_at timestamptz not null default now(),
  provenance jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint agent_raw_source_url_http check (source_url ~* '^https?://[^[:space:]]+$'),
  constraint agent_raw_provenance_object check (jsonb_typeof(provenance) = 'object')
);

alter table public.agent_raw_documents
  add constraint agent_raw_run_source_fk foreign key (run_id, source_id)
  references public.agent_collection_runs (id, source_id);

alter table public.agent_raw_documents
  add constraint agent_raw_id_run_source_unique unique (id, run_id, source_id);

create index agent_raw_run_idx on public.agent_raw_documents (run_id);
create index agent_raw_source_url_idx on public.agent_raw_documents (source_id, source_url);
create index agent_raw_hash_idx on public.agent_raw_documents (content_sha256);

create table public.agent_candidates (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.agent_collection_runs(id),
  source_id uuid not null references public.agent_sources(id),
  raw_document_id uuid not null references public.agent_raw_documents(id),
  item_fingerprint text,
  status text not null default 'EXTRACTED'
    check (status in (
      'DISCOVERED', 'SOURCE_ANALYZED', 'COLLECTED', 'RAW_STORED',
      'EXTRACTED', 'NORMALIZED', 'CLASSIFIED', 'DEDUP_CHECKED',
      'DUPLICATE', 'STAGED', 'VERIFYING', 'VERIFIED',
      'REVIEW_REQUIRED', 'REJECTED', 'PUBLISH_READY', 'PUBLISHED', 'FAILED'
    )),
  vertical text,
  source_url text,
  official_url text,
  extracted_payload jsonb not null default '{}'::jsonb,
  normalized_payload jsonb not null default '{}'::jsonb,
  classification_confidence double precision
    check (classification_confidence is null or
           (classification_confidence >= 0 and classification_confidence <= 1)),
  duplicate_result jsonb not null default '{}'::jsonb,
  provenance jsonb not null default '{}'::jsonb,
  staged_at timestamptz,
  discovered_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint agent_candidates_extracted_object check (jsonb_typeof(extracted_payload) = 'object'),
  constraint agent_candidates_normalized_object check (jsonb_typeof(normalized_payload) = 'object'),
  constraint agent_candidates_duplicate_object check (jsonb_typeof(duplicate_result) = 'object'),
  constraint agent_candidates_provenance_object check (jsonb_typeof(provenance) = 'object')
);

alter table public.agent_candidates
  add constraint agent_candidates_raw_run_source_fk
  foreign key (raw_document_id, run_id, source_id)
  references public.agent_raw_documents (id, run_id, source_id);

create unique index agent_candidates_raw_fingerprint_unique
  on public.agent_candidates (raw_document_id, item_fingerprint)
  where item_fingerprint is not null;
create index agent_candidates_status_idx on public.agent_candidates (status, created_at desc);
create index agent_candidates_source_idx on public.agent_candidates (source_id, created_at desc);

create table public.agent_verifications (
  id uuid primary key default gen_random_uuid(),
  candidate_id uuid not null references public.agent_candidates(id),
  result text not null check (result in ('VERIFIED', 'REVIEW_REQUIRED', 'REJECTED')),
  confidence double precision not null check (confidence >= 0 and confidence <= 1),
  checks jsonb not null default '{}'::jsonb,
  evidence_refs jsonb not null default '[]'::jsonb,
  reasons jsonb not null default '[]'::jsonb,
  verified_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  constraint agent_verifications_checks_object check (jsonb_typeof(checks) = 'object'),
  constraint agent_verifications_evidence_array check (jsonb_typeof(evidence_refs) = 'array'),
  constraint agent_verifications_reasons_array check (jsonb_typeof(reasons) = 'array')
);

alter table public.agent_verifications
  add constraint agent_verifications_id_candidate_unique unique (id, candidate_id);

create index agent_verifications_candidate_idx
  on public.agent_verifications (candidate_id, verified_at desc);

create table public.agent_publish_queue (
  id uuid primary key default gen_random_uuid(),
  candidate_id uuid not null unique references public.agent_candidates(id),
  verification_id uuid not null references public.agent_verifications(id),
  contract_version integer not null check (contract_version > 0),
  publish_candidate jsonb not null,
  status text not null default 'READY'
    check (status in ('READY', 'PUBLISHING', 'PUBLISHED', 'HELD', 'FAILED')),
  attempt_count integer not null default 0 check (attempt_count >= 0),
  service_opportunity_id uuid,
  service_slug text,
  service_status text
    check (service_status in ('scheduled', 'open', 'ongoing')),
  last_error_code text,
  last_error_detail text,
  queued_at timestamptz not null default now(),
  attempted_at timestamptz,
  published_at timestamptz,
  updated_at timestamptz not null default now(),
  constraint agent_queue_candidate_object check (jsonb_typeof(publish_candidate) = 'object'),
  constraint agent_queue_published_result check (
    status <> 'PUBLISHED' or
    (service_opportunity_id is not null and service_slug is not null
     and service_status is not null and published_at is not null)
  )
);

alter table public.agent_publish_queue
  add constraint agent_queue_verification_candidate_fk
  foreign key (verification_id, candidate_id)
  references public.agent_verifications (id, candidate_id);

create index agent_queue_status_idx on public.agent_publish_queue (status, queued_at);

create table public.agent_logs (
  id uuid primary key default gen_random_uuid(),
  run_id uuid references public.agent_collection_runs(id),
  source_id uuid references public.agent_sources(id),
  candidate_id uuid references public.agent_candidates(id),
  level text not null check (level in ('DEBUG', 'INFO', 'WARNING', 'ERROR')),
  event_type text not null,
  message text,
  details jsonb not null default '{}'::jsonb,
  llm_model text,
  input_tokens integer check (input_tokens is null or input_tokens >= 0),
  output_tokens integer check (output_tokens is null or output_tokens >= 0),
  duration_ms integer check (duration_ms is null or duration_ms >= 0),
  created_at timestamptz not null default now(),
  constraint agent_logs_details_object check (jsonb_typeof(details) = 'object')
);

create index agent_logs_run_time_idx on public.agent_logs (run_id, created_at desc);
create index agent_logs_candidate_time_idx on public.agent_logs (candidate_id, created_at desc);

alter table public.agent_sources enable row level security;
alter table public.agent_collection_runs enable row level security;
alter table public.agent_raw_documents enable row level security;
alter table public.agent_candidates enable row level security;
alter table public.agent_verifications enable row level security;
alter table public.agent_publish_queue enable row level security;
alter table public.agent_logs enable row level security;

revoke all on public.agent_sources from anon, authenticated;
revoke all on public.agent_collection_runs from anon, authenticated;
revoke all on public.agent_raw_documents from anon, authenticated;
revoke all on public.agent_candidates from anon, authenticated;
revoke all on public.agent_verifications from anon, authenticated;
revoke all on public.agent_publish_queue from anon, authenticated;
revoke all on public.agent_logs from anon, authenticated;

grant all on public.agent_sources to service_role;
grant all on public.agent_collection_runs to service_role;
grant all on public.agent_raw_documents to service_role;
grant all on public.agent_candidates to service_role;
grant all on public.agent_verifications to service_role;
grant all on public.agent_publish_queue to service_role;
grant all on public.agent_logs to service_role;

commit;
