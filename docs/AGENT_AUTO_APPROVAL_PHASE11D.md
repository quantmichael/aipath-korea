# PHASE 11D — Preview Supabase E2E validation

Date: 2026-10-12 KST. Target: `aipath-korea-preview` (`qonlnoyppopyztulvgzv`). The Production project in the local configuration and Dashboard is `fehlvyoskgphlodzawfn`; the refs differ. Production Supabase, Cloud Run, Scheduler, Service Publish, Tavily, and OpenAI were not changed or invoked.

## Pre-flight and migration

The Preview SQL Editor showed the existing Service schema, seven synthetic Opportunities, one Service Source, one category, no Agent tables/functions/triggers, no Storage buckets, and no `supabase_migrations.schema_migrations` table. Agent migrations 001–009 were applied in numeric order **once to Preview only**, each with a visible SQL Editor success result. There is no migration ledger in this project, so the catalog and this record are the evidence of application; do not reapply these scripts.

After application: 19 Agent tables, 31 Agent functions, RLS enabled on all 19 Agent tables, zero Agent Sources, and two disabled example Discovery schedules. The seven Service tables still had 106 columns; Service row counts remained seven Opportunities, one Source, one category, and zero Collection Runs. Agent Migration 008 intentionally creates two disabled Conference schedule examples. No Service data was copied or changed by the migration validation.

## Storage and security

A new Preview-only `agent-raw-preview` bucket was created with Public bucket OFF. A SQL catalog query confirmed `storage.buckets.public = false` and RLS on `storage.objects`. None of the 19 Agent tables grant `anon` or `authenticated` SELECT/INSERT/UPDATE/DELETE. `service_role` has EXECUTE on `agent_review_source`. Two Agent trigger-only functions (`agent_source_review_audit_immutable`, `agent_source_review_guard`) retain default `anon` EXECUTE; this is a least-privilege cleanup candidate, although PostgreSQL trigger functions cannot be invoked as ordinary functions and Agent table privileges remain denied. Actual PostgREST and Storage HTTP denial still require SDK verification.

## Preview SDK E2E (2026-10-12 KST)

The [synthetic Preview integration script](../tests/integration/agent_phase11d_preview.py) was run against the hardcoded Preview project URL with the Supabase Python SDK. Both operator-provided key files existed, were nonempty, and had mode `0600`. Their values were neither printed nor written to the repository. The local Production project ref differs from the hardcoded Preview ref; the script creates clients only for Preview. An initial sandboxed attempt could not resolve DNS; the same Preview-only probe and full E2E succeeded with network access. Each test process exited after use, so its client and key variables were not retained in the test process.

The service client read Agent tables and called the due-source RPC through PostgREST. The publishable-key client was denied Agent Source SELECT and Source approval RPC. It was also denied download of the private raw object. The Preview bucket was confirmed private by the SDK. Authenticated-user behavior, network-level controls outside these SDK calls, and Production credentials were not tested.

Three full synthetic E2E runs succeeded. Each used a unique `phase11d-` URL, an in-memory search result, and an in-memory HTTP transport for officiality, terms, robots, and raw HTML. These fixtures prove integration behavior, **not** that `example.org` grants real collection permission. Each run recorded one Discovery result and candidate; Source transition `CANDIDATE → REVIEW → APPROVED → ACTIVE`; three machine-identified approval audit events; and one Collection Schedule. A replayed activation decision and rediscovery did not duplicate the audit or Source. An invalid approval RPC left the failure fixture in REVIEW with one unchanged audit event before it was moved to HOLD.

The scheduled Collection succeeded and wrote one RawDocument to private Storage. The third run used unique raw bytes and confirmed that its content-addressed object did not exist before Collection, then existed with matching downloaded bytes and SHA-256 after Collection. The RawDocument's `run_id` matched a successful Collection Run (`stored_count=1`) and its successful completion ledger; its Source retained the Discovery query ID in provenance. An immediate scheduled rerun made no additional claim, Run, or RawDocument. Reputting identical bytes retained the same content-addressed reference. A simulated Storage failure created no additional RawDocument, and changing the fixture's terms evidence blocked subsequent collection. Tested Sources were left in HOLD. The immutable audit and linked records were retained in Preview; no blanket cleanup or deletion of non-fixture data was attempted.

Representative third-run identifiers: Source `759178b7-82be-4a72-b093-7f17adbf43b2`, Discovery query `eb080303-6116-4a13-a47c-4c5ee0bfd66f`, Collection Run `99d1a7b6-ed80-446f-b740-999933193d2a`. These IDs identify only synthetic Preview rows.

The full Python regression suite passed: 294 tests, one Starlette deprecation warning. The integration script passed `py_compile`. No commit, push, or deployment was performed. Operational automatic approval remains OFF; Service Publish remains OFF. Production Migration 009 and real official-source permission scopes remain unvalidated and require a separate production readiness review.
