# PHASE 8 — RawDocument Processing Worker

## Status and boundary

The one-shot `ProcessingWorker.run_once()` and Agent-only migration `006_processing_foundation.sql` are implemented. **006 was applied on 2026-10-04.** Actual PostgreSQL objects, RLS, RPC ownership/search paths/EXECUTE grants, and denial of direct `service_role` UPDATE/DELETE were checked. A new Source was approved, collected once to private Storage, and its RawDocument processed once; the job correctly failed closed as `CROSS_RAW_CACHE` because identical content had an earlier Candidate. Daily LLM usage was also 5/5, so **RawDocument → Candidate → Gate live E2E remains unverified**. No Service publish occurred. Offline tests use fakes and do not call an LLM.

PHASE 8 final retry: `APP_ENV=development` selects default Extraction limits 20/day and 300/month; production, unset and unknown environments select 5/day and 100/month. The existing `ExtractionBudgetGuard` still enforces these limits before provider calls. The connected Supabase project is `main PRODUCTION` and `APP_ENV` is unset, so the runtime used production 5/100. A separate official AAAI registration-page Source was collected once into private Storage (RawDocument `db9bcf87-0e5e-4a87-81c5-de94398748a0`); readback/hash passed and its extraction fingerprint was new. It appeared in DB-backed dry run with zero changes. Actual processing was **not started** because daily usage remained 5/5. Its Source was formally disabled. No new Candidate, LLM call or Service write occurred; final Candidate→Gate E2E remains open. Do not label a production database as development merely to raise the limit.

Flow: bounded successful RawDocument seed → due queue → atomic DB claim → private raw read/hash check → existing Conference `ExtractionRunner` (including `ExtractionBudgetGuard`) → existing deterministic `NormalizationRunner` → existing `QualityGateRunner` (Dedup→Verification→Publish Gate) → Agent checkpoint/completion/log. No extraction, dedup, verification or gate algorithm is reimplemented in the worker. The Quality Gate's Service duplicate lookup remains SELECT-only inside `adapters/service_db/`.

## Queue, version, claim and lease

`agent_processing_jobs` is unique on `(raw_document_id,processing_version)` and stores status, attempt count, next attempt, candidate/verification checkpoints, error code, claim ID/worker, DB-server `claimed_at`/`claim_expires_at`, completion time and timestamps. `agent_processing_completions` is a one-row-per-claim terminal ledger. Both are Agent-only, RLS-enabled, `service_role` SELECT-only tables. Only `postgres`-owned, fixed-`search_path=pg_catalog`, narrowly granted `SECURITY DEFINER` RPCs seed/list/claim/checkpoint/complete. No caller time input exists. `FOR UPDATE` locks one job; an advisory lock and live-claim check serialize active jobs per processing version so two ProcessingWorkers do not concurrently spend LLM budget through this path. This does not fix the older manual ExtractionRunner's non-atomic budget race against unrelated callers.

The version is configured by `AGENT_PROCESSING_VERSION` (`conference-processing-v1` default). Operators must bump it when extraction prompt/schema, normalization or Quality Gate semantics change. The same raw/version is not automatically rerun after terminal `SUCCEEDED`, `REVIEW_REQUIRED` or `REJECTED`; a new version receives a separate job. Queue seeding is bounded and admits only a `SUCCEEDED` CollectionRun with a RawDocument reference matching the configured storage provider prefix. Existing local references are never silently treated as Supabase objects.

The default lease is 300 seconds and must exceed the extraction timeout/retry envelope. A valid lease blocks a second worker. When expired, claim RPC first reconciles a committed Gate checkpoint; otherwise it records one `LEASE_EXPIRED` failure/backoff, clears the claim and does **not** immediately re-run the provider. An old claim cannot mutate the new job state. Processing completion checks the immutable claim ledger first; duplicate completions do not double-increment attempts or move `next_attempt_at`. A completed Gate checkpoint wins over a late failure completion. If a crash occurs after Extraction but before Gate, the existing extraction fingerprint cache avoids a second billable call for the same RawDocument; the Gate may be recomputed after retry. A cross-RawDocument cache hit is not applied to a different document's job.

## Policy and outcomes

`PENDING → PROCESSING → SUCCEEDED | REVIEW_REQUIRED | REJECTED` are terminal paths. `FAILED` and `BUDGET_BLOCKED` are retryable. Failure reason codes include `RAW_NOT_FOUND`, `HASH_MISMATCH`, `EXTRACTION_FAILED`, `PROVIDER_FAILED`, `VALIDATION_FAILED`, `QUALITY_GATE_FAILED`, `CROSS_RAW_CACHE`, `INTERNAL_ERROR` and `LEASE_EXPIRED`. The default failure delays are 15 minutes, 1 hour and 6 hours (subsequent attempts stay at 6 hours); at 4 failed attempts the job becomes `REVIEW_REQUIRED` instead of retrying forever. A budget block uses a 15-minute wait without incrementing failed attempts. The environment-selected ExtractionBudgetGuard still decides immediately before each billable provider call and logs its attempt first; it is never bypassed by Worker code. The Worker defaults to one job per pass and has no daemon loop.

The worker verifies downloaded SHA-256 against both the content-addressed reference and RawDocument metadata before extraction. It checkpoints candidate ID after extraction and candidate+verification+gate status after Quality Gate. The DB validates RawDocument↔Candidate and Candidate↔Verification relationships, including the persisted Gate decision. `PUBLISH_READY` maps to processing `SUCCEEDED`; `REVIEW_REQUIRED` and `REJECTED` remain Agent outcomes. No Service DB INSERT/UPDATE/DELETE occurs.

## Dry run, CLI and observability

`processing-due [--limit N]` uses a read-only preview RPC, including successful unqueued RawDocuments and existing due jobs. `processing-worker --dry-run` uses the same preview and makes zero claims, Storage content reads, LLM calls, Candidate writes, Gate calls, logs or schedule changes. `processing-worker --once [--raw-dir PATH] [--worker-id ID]` seeds and processes a bounded pass after 006 is explicitly applied. Local provider requires `--raw-dir`; Supabase provider does not. The Worker writes an `agent_logs.PROCESSING_WORKER_RUN` summary with worker ID, start/end, due/claimed/processed/publish-ready/review/rejected/failed/budget-blocked counts. Sensitive raw bytes, credentials and model output are not logged.

## Runtime gate and remaining work

Migration 006 passed safety review and was applied once; a second accidental SQL Editor invocation failed immediately on the already-existing first index and changed no schema. Live checks covered due preview with no mutation, exclusive claim, valid-lease denial, lease expiry followed by policy backoff and later reclaim, duplicate failure completion without drift, and same/new version seeding. The real Worker logged one claim and one `CROSS_RAW_CACHE` failure, with no LLM attempt or new Candidate. Its test Source is now `DISABLED`. Successful Candidate/Normalization/Gate processing, success-terminal reconciliation, and later retry tiers remain **unverified in the live DB**. A strict global LLM budget under simultaneous manual ExtractionRunner invocations still needs an atomic reservation boundary. Reference-aware retention cleanup, deployment secret separation and object lifecycle monitoring remain operational backlog; no automatic deletion or Service publish is included here.

## Shadow stabilization — safe extraction artifact reuse (2026-10-05)

This section supersedes the earlier statement that every cross-Raw cache hit
must fail as `CROSS_RAW_CACHE`. The safety check in ProcessingWorker remains:
the Candidate passed to downstream processing must belong to the current
RawDocument. ExtractionRunner now creates that representation when safe.

### Cache contract and key

The existing fingerprint is SHA-256 of content SHA-256, extraction version,
prompt version, provider and model. Reuse additionally checks the payload's
schema version (currently `conference-grounded-v2`), matching metadata,
original RawDocument/source/run relationship, and original prepared input hash.
Existing successful partial `REVIEW_REQUIRED` extractions can supply an
artifact; failed, malformed or insufficiently grounded artifacts cannot.
Raw bytes are hash checked. The guard still reads the persistent attempt ledger;
a valid cache hit precedes budget blocking and costs zero LLM calls. Neither
budget limits nor attempt rows are changed.

Processing with an existing same-content artifact but an incompatible key
returns `CACHE_VERSION_MISMATCH` without provider fallback. Deliberate manual
extraction under a new version retains its previous separately budgeted cache
miss behavior; this is not the automated cache reuse path.

### Provenance and evidence

RawDocument A's extraction row is an immutable input to reuse; it is never
updated. B receives a separate Candidate with B's raw_document_id, source_id,
run_id and source_url. Its provenance records collection_run_id,
processing_job_id, processing_version, reused_from_raw_document_id,
reused_from_candidate_id, the original artifact snapshot digest and B's input
hash. Processing job IDs identify the creating job; later versioned jobs retain
their own Candidate/checkpoint/completion relationships in the existing ledger.
No normalization, verification, queue state or billable token usage is copied
from A.

Deterministic HTML preprocessing is repeated with both A's and B's URLs using
B's hash-verified identical bytes. Selected evidence blocks (IDs, positions,
text, hashes) must match. Cited links must remain reproducible. The fetched-page
`L000` can be rebound to B only for an ID-selected URL with no model-asserted
raw URL; other changed relative or absolute links are rejected. Every previously
VALID field is revalidated against A and B, including its value and resolved
text. Previously invalid fields cannot become valid through cache reuse.
Evidence/value/input mismatch rejects the whole reuse with zero LLM fallback.

B then continues through deterministic Normalization, current Dedup,
Verification, PublishGate and Processing completion. Its verification evidence
refs use B's RawDocument ID. The actual Gate judgment is retained, including
REVIEW_REQUIRED/REJECTED; reuse does not imply readiness or publication.

### Idempotency and observability

The existing UNIQUE(raw_document_id,item_fingerprint) and a deterministic
Candidate UUID make replays and concurrent insertion reconcile to an existing
row without UPDATE. DB processing claims and terminal checkpoints still govern
same-version reprocessing. Cache-derived Quality Gate evaluations use a stable
`cache_reuse_key` over processing version, thresholds, extraction and judgment;
a deterministic Verification primary key handles response loss/replay between
Gate save and job checkpoint. Changed policy/judgment creates distinct audit
history. Existing UNIQUE(candidate_id) queue behavior remains unchanged.

Logs distinguish `EXTRACTION_CACHE_HIT`, `EXTRACTION_CACHE_REUSED` and
`EXTRACTION_CACHE_REJECTED`. Successful reuse records cache_hit, cache_reused,
llm_saved and origin/current Candidate/RawDocument/job IDs. Unsafe results carry
explicit CACHE_KEY/SCHEMA/PROVENANCE/INPUT/EVIDENCE/INVALID_ARTIFACT error codes
into Processing failure completion. The residual CROSS_RAW_CACHE guard protects
against any extractor still returning a Candidate from another document.

No migration is needed: Candidate provenance/checks JSON and existing primary
and unique constraints suffice. Applied migration 007 is not edited/reapplied.

### Validation and second Shadow run

223 offline tests passed, including 16 new cache tests: cross-source/run
rebinding, extraction/prompt/schema/model/provider/fingerprint mismatches,
input/evidence/value tampering, origin relationships, changed relative links,
failed artifacts, exhausted-budget cache hits, normalization/dedup/verification/
Gate continuation, Candidate and Verification replay, committed INSERT response
loss and zero additional provider calls.

Live read-only eligibility check found the ICCAI artifact
`ec29ff04-9a4e-4b0c-8d85-93a0c2f22f3f` reusable for RawDocument
`0ef6cf18-f54d-47a5-bbb1-aecdd6c08634`: evidence revalidated, zero LLM calls,
prospective extraction status REVIEW_REQUIRED. No Candidate was saved by this
check; it does not constitute live processing completion.

Second Shadow run `ef3af7f7-08eb-44af-b949-6a58476be20a` executed exactly once
on 2026-10-05 00:18:15–00:18:16 Asia/Seoul after preflight, dry run and the
then-current 222-test suite passed. Final idempotency/response-loss verification
was additionally checked offline (223 total); no third live run was performed.
All three enabled workers had due=0, claimed=0, failed=0. The old failed job was
still in backoff until 01:03:57 Asia/Seoul; no due time, approval or budget was
changed. Therefore live claim→reuse→Gate→completion remains NOT VERIFIED.
Tavily/Collection HTTP/OpenAI calls were all zero; publish flags stayed false.
Service opportunities remained 17, all rows identical; Service mutations zero.
Four Automation/Worker summary logs were read back and the global lease was
released. The existing synthetic READY Candidate/HELD queue and original
artifact were unchanged. This is safe one-shot validation, not Cloud Run GO.
