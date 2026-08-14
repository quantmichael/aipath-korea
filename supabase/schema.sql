-- AI PATH KOREA - initial PostgreSQL schema
-- Run this file in the Supabase SQL Editor for a new project.

begin;

create extension if not exists pgcrypto;

create table public.sources (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  homepage_url text,
  collection_method text not null default 'manual'
    check (collection_method in ('manual', 'api', 'submission')),
  license_name text,
  commercial_use_allowed boolean,
  modification_allowed boolean,
  attribution_required boolean not null default true,
  attribution_text text,
  terms_url text,
  last_terms_checked_at timestamptz,
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint sources_name_unique unique (name)
);

create table public.categories (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  slug text not null,
  description text,
  display_order integer not null default 0 check (display_order >= 0),
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint categories_name_unique unique (name),
  constraint categories_slug_unique unique (slug),
  constraint categories_slug_format check (slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$')
);

create table public.opportunities (
  id uuid primary key default gen_random_uuid(),
  source_id uuid references public.sources(id) on delete set null,
  category_id uuid not null references public.categories(id) on delete restrict,
  external_id text,
  title text not null,
  slug text not null,
  summary text not null,
  description text,
  organizer text not null,
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
  official_url text not null,
  image_url text,
  status text not null default 'draft'
    check (status in ('draft', 'scheduled', 'open', 'closed', 'ongoing', 'ended', 'cancelled')),
  is_featured boolean not null default false,
  published_at timestamptz,
  last_verified_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint opportunities_slug_unique unique (slug),
  constraint opportunities_slug_format check (slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'),
  constraint opportunities_application_dates check (
    application_start_at is null
    or application_deadline_at is null
    or application_start_at <= application_deadline_at
  ),
  constraint opportunities_event_dates check (
    event_start_at is null
    or event_end_at is null
    or event_start_at <= event_end_at
  )
);

create unique index opportunities_source_external_id_unique
  on public.opportunities (source_id, external_id)
  where source_id is not null and external_id is not null;

create index opportunities_category_id_idx on public.opportunities (category_id);
create index opportunities_status_idx on public.opportunities (status);
create index opportunities_application_deadline_idx
  on public.opportunities (application_deadline_at);
create index opportunities_event_start_idx on public.opportunities (event_start_at);
create index opportunities_published_at_idx on public.opportunities (published_at);

create table public.tags (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  slug text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint tags_name_unique unique (name),
  constraint tags_slug_unique unique (slug),
  constraint tags_slug_format check (slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$')
);

create table public.opportunity_tags (
  opportunity_id uuid not null references public.opportunities(id) on delete cascade,
  tag_id uuid not null references public.tags(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (opportunity_id, tag_id)
);

create index opportunity_tags_tag_id_idx on public.opportunity_tags (tag_id);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger sources_set_updated_at
before update on public.sources
for each row execute function public.set_updated_at();

create trigger categories_set_updated_at
before update on public.categories
for each row execute function public.set_updated_at();

create trigger opportunities_set_updated_at
before update on public.opportunities
for each row execute function public.set_updated_at();

create trigger tags_set_updated_at
before update on public.tags
for each row execute function public.set_updated_at();

alter table public.sources enable row level security;
alter table public.categories enable row level security;
alter table public.opportunities enable row level security;
alter table public.tags enable row level security;
alter table public.opportunity_tags enable row level security;

-- The browser must use the Python API instead of reading or writing tables directly.
revoke all on table public.sources from anon, authenticated;
revoke all on table public.categories from anon, authenticated;
revoke all on table public.opportunities from anon, authenticated;
revoke all on table public.tags from anon, authenticated;
revoke all on table public.opportunity_tags from anon, authenticated;

grant all on table public.sources to service_role;
grant all on table public.categories to service_role;
grant all on table public.opportunities to service_role;
grant all on table public.tags to service_role;
grant all on table public.opportunity_tags to service_role;

commit;
