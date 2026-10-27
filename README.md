# AIPATH 2.0 — public site shell

This release contains the AIPATH 2.0 reading interface with no articles yet. AI NOW, AI TIPS, and PROMPT LAB show honest empty states. No fixture, draft, source candidate, private review, collector, recommendation API, database connection, cron, or AI API is included.

The root `app.py` is the FastAPI entrypoint for Vercel. Replacing the existing aipath.kr repository with this tree will remove the old opportunity calendar and its APIs. Preserve the current main commit for rollback. Before merging, verify the Vercel preview and exact domain target.
