# Supabase

This directory contains the PostgreSQL database schema, seed data, and migrations used by AI PATH KOREA.

`schema.sql` configures the PostgreSQL tables, relationships, indexes, date validation, automatic update timestamps, and Row Level Security (RLS) policies.

## Core Tables

- `sources`
- `categories`
- `opportunities`
- `tags`
- `opportunity_tags`
- `collection_runs`
- `opportunity_candidates`

## Initial Setup

1. Run the full `schema.sql` script in the Supabase SQL Editor.
2. Verify that the seven core tables have been created in the Table Editor.
3. Run `seed.sql` to insert the initial categories and tags.
4. Run `sample-opportunity.sql` to verify an opportunity from an official source and its tag relationships.

Direct table access is not granted to the browser-facing `anon` and `authenticated` roles.

Database access is handled through the server-side Python API using secure environment variables.

## Applying the Collection System to an Existing Database

For an existing production Supabase project, do not run `schema.sql` again.

Instead:

1. Run `migrations/001_collect_sources.sql`.
2. Run `seed.sql` again.

This updates existing source records with the default values required by the collection system, including:

- `collection_url`
- `source_type`
- `collection_method`

This migration approach allows the collection pipeline to be added without rebuilding the existing production database.
