# Opportunity Preview read-only isolation

This document began as a 2026-10-11 KST deployment preparation record. The
later Preview validation below supersedes its earlier "no Preview deployment"
and "remaining isolation steps" notes. Production deployment remains separate.

## Preview validation completed — 2026-10-11 KST

- Preview-only branch `codex/opportunity-preview-validation`, commit `a143233`,
  was deployed as Vercel Preview deployment `3gP319oSfjmNvsfW8tff2dDu4Aj5`:
  `https://aipath-korea-mrc06dd1a-michaelis-2500s-projects.vercel.app/`.
  `main` and `origin/main` remained at `281e601`.
- The deployed list returned six visibly synthetic public Opportunities from
  the separate Preview Supabase project. List, detail, closed/ended filters,
  Korean date display, and legacy-closed non-exposure were observed. Four
  Collect routes returned HTTP 403 in read-only Preview mode.
- Direct `POST /api/recommend` returned HTTP 200 with `count`,
  `recommendations`, and `message`. A profile matching the existing synthetic
  fixtures returned one `preview-exact-open` recommendation and a reason.
  An earlier, unrelated profile returned a valid zero-result message.
- Chrome responsive emulation at 375 px showed the list, detail, and
  recommendation form/result area without visible clipping or horizontal
  overflow. A physical mobile device was not tested.
- The isolated Preview branch passed 276 Python tests, JavaScript syntax
  checks, `git diff --check`, and a selected-file secret-pattern scan before
  deployment. No code change was needed during final HTTP/mobile validation.
- This completion covers Preview validation. Production was not deployed and
  time evidence was not backfilled. The subsequent, separately authorized
  status Cron activation is recorded in
  [the runtime validation record](OPPORTUNITY_STATUS_AUTOMATION_RUNTIME_VALIDATION.md).

## Current Service DB permissions

The production Supabase SQL Editor read-only audit found eight relevant public
tables with RLS enabled. The `anon` role has SELECT on zero of them and has
INSERT, UPDATE, or DELETE on zero. It has no SELECT policies on the six tables
used by public Opportunity reads. `anon` cannot execute either
`opportunity_public_archive_ids(uuid[])` or
`opportunity_recommendation_windows(uuid[])`. The service role can execute both.
`record_opportunity_status_evidence(uuid,jsonb)` is also service-only;
`reconcile_opportunity_statuses()` is unavailable to both roles through the
Data API.

A follow-up audit of every public table, partition, view, and materialized
view found zero `anon` SELECT, INSERT, UPDATE, and DELETE grants. The four
public functions with `anon` EXECUTE grants return `trigger` or
`event_trigger`; none is an ordinary read RPC. One is the existing
`rls_auto_enable()` SECURITY DEFINER event-trigger function. Its execute grant
was observed but no invocation was attempted, so this audit does not claim
to prove every possible database access path is harmless.

Consequently, merely replacing the Preview service key with an anon or
publishable key would make the public API return permission errors. Do not
grant general table access or the evidence/worker RPCs to anon to work around
this. In particular, direct access to legacy `closed` rows must stay denied.

## Runtime controls in the working tree

`VERCEL_ENV=preview`, `VERCEL_TARGET_ENV=preview`, or `AIPATH_READ_ONLY=1`
selects read-only mode. The public API then requires a separate
`SUPABASE_READ_ONLY_KEY` and never falls back to `SUPABASE_SECRET_KEY`.
Recognizable service-role keys are rejected in the read-only slot. All
`/api/collect/*` routes return HTTP 403 before checking a Collect secret or
opening a database connection, and the legacy Collect repository refuses to
create a database client in read-only mode. Production still uses the existing
secret-key path.

These application checks are defense in depth. The Preview project's `anon`
role was checked in SQL Editor after the approved schema and synthetic seed
were applied. The publishable key was entered by the user into the Preview-only
Vercel Secret `SUPABASE_READ_ONLY_KEY`; its value was not read or printed.
Vercel requires a new deployment before that setting affects a runtime.

## Vercel configuration audit

The `aipath-korea` project had `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, and
`CRON_SECRET` scoped to both Production and Preview. Vercel Authentication
with Standard Protection is enabled for pre-production domains. The
`SUPABASE_SECRET_KEY` variable was changed to Production-only; its value and
Production scope were retained. Vercel reported that a new deployment is
needed for the scope change to take effect. No deployment was made.

`CRON_SECRET` was also changed to Production-only; its value and Production
scope were retained. The production `SUPABASE_URL` was changed to
Production-only without changing its value, then a separate Preview-only
`SUPABASE_URL=https://qonlnoyppopyztulvgzv.supabase.co` was added.
`AIPATH_READ_ONLY=1` is Preview-only. The Vercel UI showed those scopes and
reported that a new deployment is needed for them to take effect. The
Deployments page filtered to Preview showed no matching deployments on
2026-10-11. The Preview-only `SUPABASE_READ_ONLY_KEY` Secret is now present.
`BIZINFO_API_KEY` and `OPENAI_API_KEY` still have Production and Preview
scope; they do not grant Supabase write access, but the recommendation endpoint
can use OpenAI when candidates exist. Do not invoke it for candidate inspection.

## Isolated Preview project validation

The user created `aipath-korea-preview` in the Seoul region and approved
applying `supabase/schema.sql`, `supabase/preview/read_only_api.sql`, and
`supabase/preview/seed_synthetic.sql` to that project only. SQL Editor
reported success for each script. Migration 002 and Cron were not installed
in Preview. The seed contains seven visibly labelled synthetic Opportunities,
one source, one category, and four scope rows; no production row was copied.

Read-only SQL Editor checks observed eight public relations with RLS enabled
on all eight and zero `anon` INSERT, UPDATE, or DELETE table grants. The
Preview Opportunity table has `anon` SELECT, while the evidence writer RPC
is absent. In an explicit `SET LOCAL ROLE anon` transaction, six of seven
Opportunities were visible: one automated `closed` and one automated `ended`
were visible, while the legacy `closed` fixture was not. The recommendation
window RPC executed as `anon` and returned 2030-01-20 14:00 KST for the
`EXACT_TIME` fixture and 2030-02-11 00:00 KST for the 2030-02-10
`DATE_ONLY` fixture. These checks validate database permissions and policy,
not the HTTP Data API or Vercel runtime.

The Data API was then configured to expose six read tables
(`opportunities`, `sources`, `categories`, `tags`, `opportunity_tags`, and
`opportunity_candidates`) and two read RPCs
(`opportunity_public_archive_ids` and `opportunity_recommendation_windows`).
Automatic exposure of new objects remains off. `collection_runs` and the
automation scope table were not selected. The dashboard's generated API docs
listed additional objects, so the selection UI alone does not prove HTTP
inaccessibility. A SQL function audit showed that `anon` can execute the two
read RPCs, the read-only RLS helper `preview_is_public_archive`, and the
schema's trigger/event-trigger functions. The helper and trigger functions
were not selected in the Data API settings; their HTTP reachability remains
unverified. There is no evidence-writer or lifecycle-worker function in this
Preview project.

**Exposure has a security side effect:** after saving the Data API selection,
SQL privilege checks found INSERT, UPDATE, and DELETE on all six exposed tables
for both `anon` and `authenticated`. It also granted `anon` SELECT on the
candidate `raw_payload` and `validation_errors` columns. In the Preview
project only, those DML grants were revoked from both roles. Table-level
candidate SELECT was revoked from `anon`, then SELECT on only `id`,
`source_id`, `external_id`, `collection_run_id`, and `created_at` was granted.
A fresh SQL check showed zero `anon` and zero `authenticated` DML privileges
on all eight public tables; candidate `id` remained selectable while
`raw_payload` and `validation_errors` were not. All eight tables still had
RLS enabled. Recheck grants after any future Data API exposure change, since
the dashboard changed them once already.

The user placed the Preview publishable key in a mode-0600 file under
`/private/tmp` for one HTTP check. The check read and unlinked the file before
sending requests, did not print the key, and a separate filesystem check
confirmed the file was absent afterward. Against the Preview Supabase REST
endpoint, HTTP 200 returned six public Opportunities with nested category,
source, and tag data; one open detail row; zero rows for the legacy `closed`
detail; one `closed` and one `ended` row. The archive RPC returned two IDs.
The recommendation-window RPC returned two rows with the expected KST
cutoffs: 2030-01-20 14:00 for `EXACT_TIME` and 2030-02-11 00:00 for
`DATE_ONLY`. The limited candidate proof-column request returned HTTP 200
and zero synthetic rows. Direct requests to `collection_runs` and
`opportunity_status_automation_scope` returned HTTP 401. No write request
was sent. This validates the isolated Supabase Data API over HTTP, but not
the Vercel runtime or every unselected helper-function endpoint.

Local verification after these changes: 277 Python tests passed, both
Opportunity JavaScript files passed `node --check`, and `git diff --check`
passed. The secret-pattern scan of Preview-related source found only the
intentional dummy `sb_secret_test` string in a rejection test.

## Earlier isolation plan (before Preview deployment)

1. Test unselected helper-function HTTP reachability and explicit denial of
   candidate raw columns if a subsequent key handoff is available. The SQL
   privilege audit already showed raw-column SELECT denied.
2. With prior user notice and approval for a Preview deployment, run list,
   detail, closed/ended filters, recommendation candidate selection, legacy
   closed non-exposure, and Collect 403 checks against the isolated Vercel
   runtime. Do not call the LLM recommendation endpoint merely to inspect
   candidates.

At that preparation stage, Migration 002 was not reapplied, Cron was not
activated, and Cloud Run, Scheduler, and deployments were unchanged. The later
Preview deployment and Cron activation are recorded above and in the runtime
validation record.
