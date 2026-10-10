# PHASE 8 — Persistent Raw Storage

## Decision and runtime status

The existing `RawContentStorage` port remains the boundary for CollectionRunner and ExtractionRunner. `LocalRawContentStorage` remains the default for offline tests/local development. Operators select `AGENT_RAW_STORAGE_PROVIDER=supabase` and `AGENT_RAW_STORAGE_BUCKET=agent-raw` for persistent multi-process use. `AgentRuntime.raw_storage()` constructs the chosen provider; collectors and extractors do not import Supabase Storage SDK code.

The current Supabase project had Storage enabled and no prior buckets. On 2026-10-04 a dedicated `agent-raw` bucket was created with `public=false`, a 5 MiB object limit matching the default collection response limit, and `application/octet-stream` MIME allowance. The service-role SDK can read its metadata. Two isolated smoke objects were uploaded, read back with SHA-256 verification (one from a separate Python process), and removed. No existing RawDocument row was changed. The bucket remains private and empty after the smoke tests. Its existence does not mean a production retention job or deployment credentials have been configured.

## Object identity, integrity, and access

`SupabaseRawContentStorage` uses `supabase:<bucket>:raw/sha256/<first-two-hash-chars>/<sha256>.raw`. Keys contain no Source URL, title, credential or personal identifier. The SHA-256 is computed before upload, compared with `CollectionResult.content_hash`, and checked again on every download. `agent_raw_documents` continues to store `content_ref` and `content_sha256`, never the bytes. Repeated identical content uses the same key; if the object already exists, its bytes are read and verified before reuse. A race on upload is accepted only when the winning object reads back identically. Missing objects/provider errors fail closed.

The provider checks bucket metadata and refuses to run unless `public` is explicitly false. It never requests a public URL or signs one. `service_role` credentials are server-side environment values only. Other providers (including S3-compatible) can implement the same `put/get/exists/delete` contract and reference parser without changing CollectionRunner or ExtractionRunner.

## Failure and retention safety

Local storage may immediately delete a newly written file if RawDocument metadata creation fails. Persistent content-addressed objects can be shared across RawDocuments, so CollectionRunner does **not** immediately delete a Supabase object after metadata failure. Such an upload is a possible orphan, not a license to remove an object that another Run may reference. A future cleanup job must enumerate expired metadata and check all live `content_ref` references before deletion. `delete()` is an explicit primitive for an already proven unreferenced object; it is not called by normal persistent metadata-failure handling.

The configured retention default remains 90 days. New RawDocument provenance records `retention_policy.days` and an ISO `expires_at` derived from `collected_at`; older rows can use `collected_at + retention_days`. A cleanup candidate is past that expiry, subject to legal/operational retention and shared-object reference checks. No automatic purge daemon, cross-table delete, or background cleanup runs in PHASE 8. Existing `local:` RawDocuments are not migrated or rewritten; a Supabase worker seeds only `supabase:agent-raw:` references. Local storage's manual `purge_expired()` remains available for local files.

## Operational configuration

- `AGENT_RAW_STORAGE_PROVIDER=local|supabase` (default `local` for compatibility)
- `AGENT_RAW_STORAGE_BUCKET=agent-raw`
- `AGENT_RAW_RETENTION_DAYS=90`
- `AGENT_MAX_RESPONSE_BYTES=5242880` must remain no larger than the bucket object limit unless the bucket policy is deliberately changed.

With the Supabase provider, CLI collection commands need no `--raw-dir`; local mode still requires it. Private bucket creation was a one-time project operation, not an automatic runtime side effect. Storage SDK compatibility was verified with `supabase` Python 2.32.0 in an isolated temporary virtual environment. Service DB tables were not written.
