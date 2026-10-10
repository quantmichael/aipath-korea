# PHASE 9 — Publish Worker (migration 007 pending)

`PublishWorker.run_once()` is a bounded, one-shot consumer of `agent_publish_queue`.
Only a version-1 snapshot linked to a `VERIFIED` verification and
`PUBLISH_READY` Gate is due. `REVIEW_REQUIRED`, `REJECTED`, and unknown items are
never promoted. The worker passes a validated `PublishCandidate` to the existing
`SupabaseServiceDBAdapter`; it does not write `opportunities` itself.

## Final checks and mapping

The Adapter repeats queue/snapshot, confidence, approved ACTIVE Agent Source,
temporal precision/timezone, visible publication status, active Service Source,
active `conference` Category, and final Service conflict checks immediately
before INSERT. The snapshot Source homepage/name must still match the approved
Agent Source identity. It uses an exact canonical Service Source homepage or unique domain+name;
missing/ambiguous matches are held. No Service Source, Category, or Tag is
created. Existing tags may be matched but junction rows remain omitted. A
`DATE_ONLY` event cannot be represented faithfully by current Service
`timestamptz`; it is held, not converted to a fictional time. `closed`, `ended`,
and unknown publication status are not inserted. The final Service scan is
bounded to 500 rows and fails closed if incomplete.

`publish-worker --dry-run` reads the due queue and returns candidate ID,
mapped Source/Category, publication status, conflict result, reasons, and
prospective INSERT payload. It makes no claim, Service write, queue mutation,
or log entry. `publish-worker --once` requires both migration 007 and explicit
`AGENT_PUBLISH_ENABLED=true`; the default is false. Maximum jobs default to 1.

## Atomic claim, recovery, and audit

Migration 007 adds DB-server-clock queue claims and leases. A singleton Agent
publish lease serializes worker publication across candidates during the
normal lease window; a second worker cannot claim while it is valid. Claim
expires after a configured 300 seconds by default. The queue carries claim
identity and `next_attempt_at`; failed jobs wait 15 minutes, held/published
jobs are terminal. One completion ledger row per claim makes duplicate
completion idempotent. A late failed completion cannot downgrade a completed
`PUBLISHED` claim. After a crash, an expired claim may be reclaimed. The
Adapter's stable slug/external ID and Service readback reconcile an INSERT
that committed before Agent completion without inserting it again. No
Opportunity UPDATE/MERGE/DELETE is performed.

007 revokes direct queue UPDATE/DELETE from `service_role`, retaining
SELECT/INSERT so Quality Gate can enqueue only verified READY snapshots. A
postgres-owned, fixed-search-path trigger guards those INSERTs. Claim,
completion, and runtime lease mutations occur in narrowly granted
SECURITY DEFINER RPCs. `agent_publish_completions` and
`agent_runtime_leases` are SELECT-only to `service_role`. The existing
manual Adapter route also uses these RPCs after 007; it cannot bypass a claim.

All queue, claim, result, and worker-summary writes are Agent-only. Service
table access remains inside `adapters/service_db/`. Service readback checks
ID, slug, external ID, Source/Category FK, title, summary, organizer, URL,
event start, and API-visible status. A real new-row Backend API test is still
required after a naturally eligible candidate exists and 007 is applied.

## Current runtime outcome and limits

On 2026-10-04 the live Agent queue contained **0** rows, including **0 READY**;
therefore Service INSERT/readback/Backend API verification were not run and
Service writes were **0**. The latest real Processing candidate was
`REVIEW_REQUIRED`, not upgraded. Migration 007 was created but **not applied**.
The cross-database insert/completion is not one transaction. A lease expiry
during a slow INSERT could permit another worker; stable Service unique keys
prevent the same Candidate's duplicate row, but distinct Candidates sharing
an official URL lack a DB unique key. Before multi-worker production enablement,
test this recovery against live PostgreSQL and set an appropriate lease/timeout.
