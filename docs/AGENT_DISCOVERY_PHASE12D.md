# PHASE 12D: Budget cutover release readiness

Read-only audit on 2026-10-12 KST. This document is a future deployment runbook;
no Production migration, Cloud Run update, Scheduler change, or provider call was
performed in PHASE 12D.

## Release scope

- Remote `main`: `a1883c8e5ec396f35a0e59f3b4a48e75ac519827`.
- PHASE 11F base: `b6bf0d3119f5f87dc16bb9ac74e2c1e43d1b2c9b`, descended from remote `main`.
- Validation worktree: PHASE 11F plus PHASE 12A/12B Agent code, tests, docs,
  migrations 010/011, and the PHASE 12D budget/failure-path corrections. No
  Opportunity or Vercel files are in its working-tree changes.
- Build context: `Dockerfile.agent` copies `agent_collect`; both ignore files
  include its Python modules and exclude migrations and secret-like files.
  `requirements.agent.txt` pins the Supabase and OpenAI SDKs. A real Cloud
  Build/image import has not been run for this release.

## Current Production state

- Supabase project `fehlvyoskgphlodzawfn`: Agent 009-era Discovery constraints
  are present. Migration 010 category extensions and all 011 tables/RPCs are
  absent. Migration 011's legacy query/log columns have the expected types.
  Baseline counts: 11 Sources, 5 Discovery schedules, 5 Discovery queries,
  253 Agent logs, and 17 Service opportunities.
- Cloud Run project `aipath-511213`, region `asia-northeast3`, job
  `aipath-agent-shadow`, image tag `b6bf0d3119f5f87dc16bb9ac74e2c1e43d1b2c9b`.
- Scheduler `aipath-agent-shadow-scheduler`: Enabled, `*/30 * * * *`,
  `Asia/Seoul`. It invokes the Cloud Run Jobs API separately from the Cloud Run
  console's built-in trigger list.
- Published-site flags and Source auto approval are false in the current Job.
  AI planner/execution flags are absent and default false in the release code.
- At audit time, active automation leases, RUNNING Discovery/Collection,
  PROCESSING jobs, and LLM/Tavily attempts in the preceding 30 minutes were
  all zero. These are point-in-time observations, not deployment clearance.

## Budget cutover risk and required order

The deployed PHASE 11F image checks legacy query/log counts but does not take
the 011 transaction-scoped advisory lock. The new image now reserves every
Supabase-backed Tavily Discovery and OpenAI Extraction call even with Planner
OFF; local/fake repositories retain the offline path. The 011 RPC includes
unreserved legacy rows in its limits and excludes rows linked to a reservation
to avoid double counting. A concurrently running old image can still bypass
that lock. An old-image rollback can undercount reservations committed before
a query/log row was written, and will not see Planner reservations.

The PHASE 12D fix also normalizes provider failure codes before the 011 RPC and
marks a Discovery query FAILED when reservation dispatch fails before the
provider call. No provider call is made on reservation/dispatch failure.

Future release sequence, subject to a fresh preflight:

1. Record Job image digest, flags, Scheduler state, Agent table counts, KST
   daily/monthly legacy usage, and 011 object absence. Stop if objects are
   partially present or if an unexpected Production data change is found.
2. Pause only `aipath-agent-shadow-scheduler`. Verify PAUSED. Allow any
   already-delivered invocation to finish; verify Cloud Run has no running
   execution, automation lease is clear, and no Discovery, Collection, or
   Processing row remains actively claimed. Check for unresolved external
   provider attempts in logs. A Scheduler pause alone does not drain them.
3. Apply 010 once, verify both category CHECK constraints; apply 011 once,
   verify three RLS tables, eight RPCs, indexes, service-role-only grants,
   and unchanged legacy table counts. Both files are transactional. Never
   reapply based solely on a filename or SQL Editor history.
4. Build from a reviewed commit SHA, inspect image digest and module presence,
   then update the Job with Planner OFF, AI Execution OFF, Auto Approval OFF,
   both Publish flags OFF, and the existing service account/secret references.
   Keep the Scheduler paused throughout the image switch.
5. Run one manual Shadow execution. Confirm exit code, runtime and worker
   audits, no unintended Source/Service publication, and the before/after
   provider budget ledger. If Discovery or Extraction is due, verify the
   reservation ID links exactly one query/log. If neither is due, call-path
   budget integration remains unverified in Production.
6. Resume the Scheduler only after successful Shadow checks. Observe the
   first scheduled execution and recheck budget counts and flags.

## Rollback rule

Keep the Scheduler paused on a failed cutover. The PHASE 11F image is **not** a
safe direct rollback target while new reservations may fall in current KST
daily/monthly windows. Prefer a tested fallback image that retains the 011
reservation adapters while disabling Planner and new planning execution. If
only the old image is available, do not resume billable Discovery or Extraction
until in-flight calls and all ledger/legacy gaps are reconciled; disable those
provider paths or wait for the relevant budget windows to expire. Do not
remove 010/011 merely to roll back the image.

## Evidence and current gate

- Python regression: 317 tests passed after both PHASE 12D corrections.
- Isolated PostgreSQL: migrations 001-011, advisory-lock contention, shared
  OpenAI/Tavily caps, KST boundaries, legacy counting, RPC/RLS passed.
- PHASE 12C Preview SDK exercised real 011 RPCs with mock providers; Production
  010/011 and a new Cloud Run image remain unapplied/unbuilt.
- Deployment is NO-GO until a committed release artifact, Production migration
  preflight, drained Scheduler/Job, and budget-aware rollback image or equivalent
  provider-disabled rollback procedure are ready.
