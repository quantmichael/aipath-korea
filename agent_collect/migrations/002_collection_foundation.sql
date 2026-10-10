-- PHASE 3: Extend only Agent tables for the deterministic collection engine.
-- Apply after 001_agent_foundation.sql. Existing service tables are untouched.

begin;

alter table public.agent_sources
  add column if not exists last_collected_at timestamptz;

alter table public.agent_sources
  drop constraint if exists agent_sources_collection_method_check;
alter table public.agent_sources
  add constraint agent_sources_collection_method_check
  check (collection_method in (
    'http_html', 'http', 'rest_api', 'rss', 'html', 'sitemap', 'search', 'browser'
  ));

alter table public.agent_collection_runs
  drop constraint if exists agent_collection_runs_collection_method_check;
alter table public.agent_collection_runs
  add constraint agent_collection_runs_collection_method_check
  check (collection_method in (
    'http_html', 'http', 'rest_api', 'rss', 'html', 'sitemap', 'search', 'browser'
  ));

commit;
