# Metadata Candidate atomic publication

This release keeps the existing Full Raw publish queue and Service registration
path unchanged. Metadata publication is an explicit Candidate-ID method on
`PublishWorker`; it never lists or claims the Full Raw queue. The runtime wires
it behind both `publish_enabled` and `publish_worker_enabled`. Defaults and the
deployed Shadow Job remain OFF. No automatic metadata queue scanner is added.

Agent and Service tables reside in the same Supabase PostgreSQL project.
Migration 018 adds `agent_publish_metadata_candidate(uuid,jsonb,jsonb)`, granted
only to `service_role`. It locks the Metadata approval and Candidate, checks
the live Source, reviewed permission and revocation audit, the latest fresh
VERIFIED/UNIQUE/PUBLISH_READY assessment audit, a unique approved Service Source mapping and
category, Candidate facts and time provenance. It calls the existing Service
004 `register_opportunity_with_evidence` RPC and writes an immutable Metadata
completion row in the same transaction. A completion insert failure rolls back
the Opportunity and Time Evidence. A preexisting Opportunity is rejected; an
existing completion is returned idempotently. The completion stores the exact
registration envelope, and its Service UUID is historical rather than an FK
so Preview synthetic Service rows can be removed without altering audit rows.

The worker requires a fresh, caller-supplied official page and permission
document. It replays Evidence and re-records assessment before constructing
the Service envelope. SQL checks that the assessment and approval are still
current at DB time. A lost RPC response is reconciled against completion and
Service readback; otherwise the result is UNKNOWN, never success. The DB
cannot replay HTML itself, so the process and its trusted `service_role`
credentials remain the Evidence replay boundary. No external page fetch is
performed by this publish method.

Apply order for a future release is Agent 016, Agent 017, Agent 018, then an
image containing this code. Service 004 is already a prerequisite. Each
migration needs project-specific preflight and approval. Existing images do
not invoke 018, so schema-first deployment is compatible. The migrations are
not automatically reversible because immutable audit records and Service
publication can exist; a rollback should disable both publish flags and
return to the previous image while preserving audit and published records.

The `metadata-publish` CLI requires one `--candidate-id`, `--page-file`,
`--page-url`, `--fetched-at`, `--permission-file`, and
`--permission-checked-at`. `--dry-run` performs reads and replay only;
`--once` also requires both publish flags. It never scans other Candidates.
The caller must provide the transient page bytes and permission document.
Service Publish must be enabled only for a
separately approved operational canary; this release does not turn it on.
Preview synthetic E2E must compare the original Service/Agent row fingerprints,
remove only exact synthetic Service Opportunity and Status Scope IDs, retain
immutable approval/completion audit, and report any remaining synthetic Agent
rows. Production publication is outside this stage.
