# PHASE 11C — Agent DB integration validation

Date: 2026-10-12 KST. Scope: isolated local PostgreSQL 18.4 only. Production Supabase, Cloud Run, Scheduler, Tavily, OpenAI, and Service Publish were not used or changed. No commit, push, or deployment.

## Environment and migration audit

A disposable embedded PostgreSQL cluster was started on loopback with synthetic `anon`, `authenticated`, and `service_role BYPASSRLS` roles. The existing Service `supabase/schema.sql` was loaded first. Agent migrations 001 through 009 were then applied in order to that new cluster; each completed successfully. No operational migration was reapplied. Service schema columns were identical before and after: 106 columns across seven tables. All 19 Agent tables had RLS enabled; required Source, Candidate, Discovery schedule, and Collection window unique indexes, key guard/activation triggers, and SECURITY DEFINER RPCs were present. The cluster stopped after the test.

## Executed checks

- Discovery schedule registration remained reviewed and disabled by default; matching vertical/claim Query accepted, mismatched vertical rejected; schedule RPC and RLS rollback test passed.
- A synthetic Query, Search Result, Candidate, and Source were persisted with valid FKs. A duplicate canonical Source URL failed with the DB unique constraint.
- `AIPATH_AUTO_APPROVAL_V1` drove `CANDIDATE → REVIEW → APPROVED → ACTIVE` through the existing Source review RPC. Three audit events, one approval snapshot, and one Collection Schedule were confirmed.
- Repeating the same decision ID produced no additional event. A fresh duplicate request was rejected. Two simultaneous sessions attempting the initial review produced one successful transition and one rejected stale transition, with one audit event.
- An invalid approval RPC left the Source in REVIEW with no approval event or schedule; an explicit HOLD succeeded. A whole-transaction rollback restored Source, audit, and schedule to their pre-transaction state. An injected activation trigger failure rolled back activation, event, and schedule, then a retry succeeded after the synthetic fault was removed.
- Direct `service_role` lifecycle UPDATE was rejected. `anon` and `authenticated` could not read Agent Source/audit tables or execute the Source review RPC. `service_role` could execute the RPC but could not forge audit rows directly.
- ACTIVE Source appeared in due lookup. One claim succeeded, a second claim was denied. A scheduled Run and a RawDocument pointing to synthetic local HTML bytes were stored; a duplicate Run for the same window failed. Collection completion returned SUCCEEDED twice without duplicate RawDocument or completion. HOLD removed the Source from due/claim results.
- Discovery worker summary logs now include Query IDs and promoted Source IDs alongside classified counters and approval failure reasons. A synthetic log was joined through Query, Source provenance, and all three approval audit events in PostgreSQL. The logging payload itself has a Python regression test.

The reusable [local integration harness](../tests/integration/agent_phase11c_postgres.mjs) and [test package manifest](../tests/integration/package.json) contain only synthetic values and always start a disposable local cluster. The narrower [rollback SQL test](../tests/sql/agent_discovery_coverage_integration.sql) also passed. The harness requires its local `embedded-postgres` development dependency to be installed in `tests/integration`; it must not be pointed at Production.

## Verification limits

The PostgreSQL test inserts synthetic Discovery and Collection records directly through the database client. It proves DB constraints, RPCs, triggers, permissions, concurrent transactions, and metadata persistence, but does not exercise Python's Supabase/PostgREST adapter against a live nonproduction Supabase API. The actual Python DiscoveryRunner → CollectionWorker → LocalRawContentStorage path was covered by offline HTTP fixtures in PHASE 11B. License and robots decisions were not checked against a real external site; their correctness still depends on the reviewed scope and the application revalidation code. A real Supabase Storage bucket and Cloud Run runtime were not tested. Actual service-role behavior was modeled with a local BYPASSRLS role, not an operational credential.

Operational auto approval must remain OFF until a separate nonproduction Supabase end-to-end run confirms the API adapter and Storage path with reviewed test scopes. Migration 009 has **not** been applied to Production or Preview Supabase.
