# AIPATH 2.0 — public reading site

This release contains the AIPATH 2.0 reading interface. It reads approved articles from the existing aipath-korea Supabase project only when AIPATH2_SUPABASE_PUBLISHABLE_KEY is configured. Otherwise the sections remain empty. No fixture, draft, source candidate, private review, collector, recommendation API, database secret key, cron, or AI API is included.

The root `app.py` is the FastAPI entrypoint for Vercel. The database schema and unpublished drafts are not included in the deploy bundle. The current aipath.kr repository already contains the AIPATH 2.0 shell.
