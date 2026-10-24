# Autonomous completion checkpoint — 2026-10-23

## Current resume point — Preview automatic publication verified; release build approval next

- Authenticated Preview automatic publication completed using the explicitly approved Source-scoped grant. Actual runtime run `89e1ee43-bed5-4309-af27-5537361c788e`: COMPLETED, errors empty; metadata lead/processed/ready = 1/1/1, publish attempted/published/failed = 1/1/0. Candidate `6e653c62-0b3d-4b5e-afb6-a7196fcbee7b`, synthetic Opportunity `b5c25a4f-72d5-49db-919c-77676805b577`. Immutable events show VERIFIED / UNIQUE / PUBLISH_READY and atomic PUBLISHED. Stored synthetic Discovery and page transports were used: this is NOT real web acquisition or Production publication.
- Exactly one atomic RPC, zero external page/provider calls. Completion exclusion checked read-only; no second publish/Runtime was launched. Source approval then revocation audit retained; Candidate is now REVIEW_REQUIRED, Source HOLD/is_active=false. Synthetic Opportunity and Service Source were deleted by exact IDs; immutable completion retained intentionally.
- Original harness FINISHED reported false only for `INTEGRITY_agent_source_schedules` and `UNEXPECTED_ROW_agent_source_schedules`: existing Migration 005 activation trigger created one new unclaimed schedule for this fixture. Independently verified all pre-existing schedule rows against AUTH_READY SHA `98af396debd142c2a61991be55966beaad654b58687e323ca0d3c7d619bc9130`; exact match. Fixture schedule has no claim/collection history and SQL agent_schedulable_source=false. Retained inert Source/schedule/audits. Corrected only harness fixture accounting, preserving prior-row mutation detection; original failed report preserved, no live rerun. Offline harness tests 31 passed.
- Admin readback at 2026-10-23 11:52:59 UTC: reviewer OFF and prior inactive scope restored, Auth sessions 0, active leases 0, Publish Queue 0. Opportunity 7 / Scope 4 entire row fingerprints unchanged. Approved temporary category removed and original category fingerprint restored. Process PID97884 exited; process-only publish flags no longer active. Existing 17-table protection plus independent schedule and administrator Scope/category verification passed. No Production/GCP changes.
- Proof: `/private/tmp/aipath_preview_auto_publish_original_finish.json`, `/private/tmp/aipath_preview_auto_publish_readonly_verification.json`, `/private/tmp/aipath_preview_auto_publish_cleanup_verified.png`. Do not relabel the original harness exit as success; functional Preview verification was independently established after the reporting defect.
- Follow-on release check: entire Python suite 580 passed, 1 dependency deprecation warning, external socket connections blocked and provider environment credentials scrubbed. Result `/private/tmp/aipath_autonomous_release_pytest.xml`. No repeated full suite. Current 28-file scope = 12 product Python, 14 tests, 2 docs; no Migration/API/UI/Cloud config changes. Limited secret-pattern scan zero findings; git diff --check passed.
- Prepared `/private/tmp/aipath_autonomous_validation_cloudbuild.json`: same existing image build plus `docker run --network none` import/default-OFF/Shadow startup validation, no live Workers/DB. Its inline check passed locally (10 imports, default flags OFF, unsafe startup blocked before connect); actual Python3.13 container build still unverified because Docker unavailable. No Cloud Build submitted.
- Next approval: exact 28-file independent release branch commit/push and one build with isolated image validation. No main push/merge, Production deployment, Migration, Source permission or actual publication included. After build succeeds, Production preflight/deployment still need explicit execution approval. Real Source permission/approval and real publication remain unresolved; K-Startup API-dependent branch only stays deferred.

- User explicitly approved temporary Preview conference category insertion and exact cleanup. Applied one INSERT to Preview `qonlnoyppopyztulvgzv` only, with transaction guards on prior category/Opportunity/Scope fingerprints, reviewer OFF and no active lease. Category ID `5d8cf559-f8b8-49b9-82fd-c6ad55fc0468` now active, slug conference. No migration/grant/Production changes.
- Readback at 2026-10-23 11:45:58 UTC: exact category present; original categories, Opportunities and Scope whole-row fingerprints unchanged; reviewer OFF; active leases 0. Proof `/private/tmp/aipath_preview_category_prepared.png`. This category remains temporarily pending E2E, and MUST be removed after exact fixture cleanup when no Opportunity/legacy Candidate references exist; cleanup already approved. Original category fingerprint `532c612a4e7750ed226dbb639d416cc3` must be restored.
- Next requires actual user Auth via corrected existing script and unchanged approved manifest SHA. No current AUTH_READY or reviewer grant. After login verify fresh process/UUID/exact Source REVIEW, then request scoped reviewer activation separately. Do not rerun completed local tests.

- User attempt ended at 2026-10-23 11:40:19 UTC with generic PREVIEW_E2E_INCOMPLETE, atomic calls 0. Root cause identified from live read-only Preview schema/data: categories has only active `preview-test`, no required active `conference`. Harness stops CATEGORY_UNAVAILABLE before getpass/Auth or fixture writes; old catch recorded only RuntimeError. No new login since 08:53 UTC, auth.sessions 0, fixture Source absent, reviewer OFF, queue/lease 0. Opportunity 7 and Scope 4 fingerprints exactly match the 11:24 baseline (same algorithm).
- Corrected only temporary harness diagnostics: allowlisted fixed error codes and execution stage; unknown SDK/server text never logged. Category helper still requires exactly one active conference; no fallback to preview-test, no gate relaxation. Offline boundary/regression tests: 22 passed. No automatic live retry, no rights grant or DB write.
- Prepared only: exact disposable Preview category ID `5d8cf559-f8b8-49b9-82fd-c6ad55fc0468`, slug conference. SQL `/private/tmp/aipath_preview_auto_publish_category_prepare.sql` and cleanup counterpart. Additional category write/cleanup approval required because original approval named Source/Service Source/Opportunity, not categories. Existing category is preserved; new category removed only after no Opportunity or legacy Candidate references. No schema migration.
- After category scope approved and verified, use same approved manifest/source IDs; authenticate in user TTY, then separate exact Source reviewer grant approval. Do not ask user to retry password before category prerequisite is resolved.

- User approved bounded Preview synthetic automatic publication and exact cleanup (Source `2067fcb2-70bf-4a89-a9dd-880ce8b0b6ab`, Service Source `c205daf1-9e68-40f6-9330-e77d8dcf632e`). This does not approve reviewer activation; request that after live AUTH_READY.
- Chrome Preview project identity verified. Read-only SQL at 2026-10-23 11:24:39 UTC: Opportunity 7, Scope 4, reviewer OFF (prior scope `ec0476ec-d0a0-41c2-8d39-09dc7354831a`), active reviewers/leases 0, atomic RPC and 019 guard present, immutable completion and usage guard triggers enabled. Whole-row baseline saved privately to `/private/tmp/aipath_preview_auto_publish_admin_baseline.json`. Its jsonb_agg fingerprint format differs from old string_agg checks; compare only the same algorithm.
- Manifest SHA remains `35b54e2245493412b4c4c830e5f374c6ac36540158524dcf8af64b9156b2ffb4`. No E2E/auth process launched, no DB writes or grants yet. Next: user runs prepared command in their own TTY, enters hidden password, then verify fresh AUTH_READY and request exact Source-specific grant.

- Continuous execution continued beyond the prior local SQL success. During Preview preparation, verified the preserved 019 SQL SHA `30e0db6360d8fc9434932c049c3aeceb089f8ad53262547500319d41834c418f` and reproduced a real contract mismatch in isolated PostgreSQL: old aggregate `fact_use` reached atomic RPC but 019 rejected missing action rights, yielding UNKNOWN / PARTIAL and no committed Opportunity.
- Reused only existing `MetadataUsageRights`/`usage_references_replayed` from the preserved Evidence implementation. Added optional rights on MetadataAccessPolicy; SDK loads them only from an immutable approved event whose exact URL, scope and permission digest match. Other Metadata Evidence/Timezone/Minimum Policy changes were not bulk integrated.
- Automatic fresh-input loader now holds missing/denied/unverified rights before webpage fetch and replays permission references before fetching the notice. The existing explicit Candidate publication method also requires these rights before RPC. No rights, approvals or references are inferred. The non-publication assessment contract and Full Raw path remain unchanged. Operational rollout of this stricter existing publication contract still needs approval.
- Focused publication/readback/service-contract tests **75 passed**. Rights loading/parser/negative cases, Runtime, SDK and Evidence tests **104 passed** (overlapping scopes, not a summed total).
- Final isolated PostgreSQL 17.10 run **with actual 019 SQL**: PASS, five categories + actual Runtime stored-Discovery handoff; 6 local synthetic registrations; missing rights held BEFORE HTTP/atomic RPC; subsequent dispatch 0 new registrations; revoked Source held; completion failure rolled back Opportunity, Time Evidence scope and completion; reviewers inactive, lease released and prior rows unchanged. No Supabase Auth/SDK persistence claim from this SQL bridge.
- Prepared, not live executed: `/private/tmp/aipath_preview_auto_publish_e2e.py`. Offline fixture extraction/rights/Evidence/UNIQUE/PUBLISH_READY check passed, zero network/writes. Preview project fixed to qonlnoyppopyztulvgzv. Manifest SHA `35b54e2245493412b4c4c830e5f374c6ac36540158524dcf8af64b9156b2ffb4`.
- Proposed exact synthetic Agent Source `2067fcb2-70bf-4a89-a9dd-880ce8b0b6ab`; synthetic Service Source `c205daf1-9e68-40f6-9330-e77d8dcf632e`. Maximum one atomic RPC; external HTTP blocked; existing READY rows must be absent; RPC/write guard restricts candidate/source IDs. One Runtime real pass after dry run, completion exclusion checked read-only. Test-only process publish flags ON; no persistent configuration change.
- Prepared Preview harness transport/manifest boundary tests: **15 passed** offline. They reject Production/external HTTP, Auth Admin, reviewer table writes, other Source/Candidate writes, unscoped deletion and mismatched manifest hash. They do not claim full live cleanup or Auth verification.
- Preview execution approval requested via async question, response pending. Do not create those rows or run --execute until approved. After authenticated user login, exact Source-scoped reviewer activation requires its own execution approval. Grant must be OFF on success/failure. Plan retains immutable Agent/completion audits; removes only exact new Service Opportunity (+FK Scope) and mapping; Source ends HOLD with approval revoked. Scope table is administrator-only, so `/private/tmp/aipath_preview_auto_publish_preflight.sql` prepares read-only whole-row scope fingerprints before/after, without widening grants.
- Production/Preview writes, permissions, deployments and paid API calls in this continuation: **0**. K-Startup only remains deferred. Next: complete Preview preparation checks while waiting; after approval verify SQL baseline, ask user to authenticate via the prepared private TTY, then request the exact scoped grant. Do not reuse an old AUTH_READY or activate an expired session.

## Previous checkpoint — persistent local SQL integration complete

- User requested continuous execution: continue authorized local work without waiting for another “next”. K-Startup alone remains deferred.
- New test-only files: `tests/integration/agent_metadata_runtime_postgres.py` and `tests/integration/metadata_runtime_pg_bridge.mjs`.
- Real `AutomationRuntime.run_once` now verified against disposable PostgreSQL **17.10**, using the repository schema through Agent 018 + Service 004. Five category fixtures were published; an additional stored Discovery result traversed Runtime handoff -> extraction -> persisted VERIFIED/UNIQUE/PUBLISH_READY -> Atomic Publish in one runtime invocation. **6 local synthetic Opportunities**, not live/Preview/Production publications.
- Real SQL approval/revocation RPCs, Source-scoped guard, assessment audit, global lease, Atomic registration, Time Evidence scope and completion rows were exercised. Local test SQL role claims are fixture identities, **not real Supabase Auth**. The SDK wire transport is a test bridge; page HTTP is synthetic. No live keys loaded.
- Dry run full-row snapshots unchanged. Later Runtime invocation produced 0 additional publications. Prior local Opportunities unchanged. Revocation yielded REVIEW_REQUIRED and no publication. Injected completion-trigger failure rolled back Opportunity, Evidence scope and completion; Runtime PARTIAL/PUBLISH_INCOMPLETE, one attempt, no retry. Reviewer fixture rows inactive and lease released.
- Mac shared-memory startup failure was diagnosed. Only the detached 56-byte segment whose key matched this turn's temporary test directory inode was removed after checking creator PID absent and attachments 0. Other shared memory/system settings were not changed. Test-harness logging/cleanup and fixture RPC arguments were corrected; no product contract change was needed. Successful final run exited 0 and disposed its local DB.
- New result proves local persistent SQL integration; actual Preview SDK positive automatic dispatch, real Auth and actual image remain unverified. Next: prepare bounded Preview validation and cleanup with explicit fixture-only scope, then request only necessary authentication/execution approvals. No Production action, preview write, commit/build/deploy or paid call in this continuation.

## Previous checkpoint — Metadata automatic dispatch connected locally

User explicitly deferred K-Startup API key lookup. **Only the K-Startup-dependent branch is BLOCKED**; do not ask for the key again unless that branch resumes. Other local integration work proceeded.

### Completed / changed files

- `agent_collect/metadata/supabase.py`: complete bounded selection of operational PUBLISH_READY Candidate IDs, stable ordering, exclusion of immutable publish completions, fail-closed count/identity/overflow checks.
- `agent_collect/runtime.py`: Automation explicitly enables Metadata selection on the existing PublishWorker. Fresh evidence loader checks ACTIVE/collectible Source, authenticated current exact-Source permission, rejects `synthetic_only`, verifies permission hash, uses existing robots-aware pinned transport, and permits only exact page/permission URLs. Invalid evidence is passed as absent, never invented. Manual worker construction stays explicit-only.
- `agent_collect/publishing/worker.py`: after Full Raw, use remaining slots in the existing max-jobs limit for Metadata; reuse `run_metadata_candidate`. Do not select Metadata after Full Raw failure. Dry run only lists IDs. UNKNOWN/error stops remaining Metadata work; no same-run retry. Existing completion checks preserve duplicate protection.
- `agent_collect/publishing/supabase.py`: existing worker logs include Metadata counts and sanitized outcomes, never page bytes.
- `agent_collect/automation/runtime.py`: nonzero Publish failed_count gives PARTIAL / PUBLISH_INCOMPLETE, so an UNKNOWN publish is not logged as completed successfully.
- `tests/test_agent_metadata_automatic_publish.py`: offline real Runtime-to-publish integration, selection and failure cases. No new authentication, Source approval, Worker, Gate, migration or default flag.
- `docs/AGENT_METADATA_ATOMIC_PUBLISH.md`: dispatch behavior, bounds, compatibility and recovery documented.

### Actual validation

- Focused combined Runtime/Metadata/publishing/precision/shadow tests: **118 passed** after product changes. Four subsequent test-only additions passed; automatic dispatch test file total **29 passed**. No whole-suite repetition.
- Actual `AutomationRuntime.run_once` with synthetic Discovery/HTTP/DB transports traverses Extraction -> VERIFIED -> UNIQUE -> PUBLISH_READY -> existing Metadata publication method -> real Service envelope construction. It includes the local Candidate readback and time-precision corrections. SQL persistence is represented by a test double, not claimed as real DB publication.
- Negative cases: either Publish flag OFF, global lease denied, dry run, inactive Source, explicit synthetic-only Source, expired/revoked approval, altered permission, redirected URL, unrelated page, duplicate Service record, revocation between fetch and assessment, incomplete/oversized scans, UNKNOWN stop, shared Full Raw limit, and manual-worker compatibility.
- Retry test skips completed Candidate on a later Runtime invocation; lost-response test reconciles completion without a second call. No automatic retries inside one run. A later scheduled tick may recheck a pending Candidate through the same current-state and atomic-idempotency contracts.
- Existing PostgreSQL 17.10 Atomic Publish results from the preceding checkpoint are reused; SQL was not changed or redundantly rerun. No new claim about PostgreSQL 17.11 or Migration 019 integration.
- **Preview read-only SDK probe, 2026-10-23 18:33 KST:** project qonlnoyppopyztulvgzv; pending Metadata selection (limit 1) returned 0; 9 table full-row fingerprints matched before/after; Opportunity 7, Source 35, Metadata Candidate 15, Publish Completion 8, Publish Queue 0. HTTP guard allowed only GET to this project's REST tables (20 reads); writes 0, Worker executions 0, external page calls 0. Result `/private/tmp/aipath_metadata_dispatch_readonly_result.json` (0600). This verifies empty-selection SDK compatibility, not live positive automatic publication. Counts supersede older Preview count observations; no mutation was performed by this probe.

### Remaining work / next highest priority

1. Local automatic dispatch is connected. Ten required Runtime imports passed with network connections blocked after the dispatch changes. Docker CLI is unavailable on this Mac, so no actual image was built. Positive persistent validation of the **new** dispatch path and actual image validation remain unverified. The earlier Preview handoff success predates this dispatch addition and must not be relabelled as automatic publication success.
2. Prepare the release/Preview test scope around these exact changes, preserve existing data, and obtain execution approval before any live synthetic Service registration or new reviewer grant. Production deployment, actual Source approval, actual publication and permanent activation remain separate approvals.
3. Newly discovered real Sources still require the existing authenticated exact Source/URL approval; this connection does not change that deliberate policy. Real Source permission acquisition and an approved authorization-policy decision are still needed for human-free operation across arbitrary new Sources. Do not fabricate those approvals.
4. K-Startup API branch only: BLOCKED by user deferral. Keep API credential/schema/JSON extraction questions isolated from other development.
5. Operational limits: Full Raw retains priority, so saturation can defer Metadata. Ready/completed history above SDK max_records fails closed. Existing global lease and atomic SQL locks are reused; no new per-Candidate lease/attempt ledger. Do not claim unlimited throughput or automatic retry scheduling.

### Safety / approval / resume

- Production DB access/writes in this continuation: 0; Preview writes: 0; Cloud Run/Scheduler changes: 0; paid API calls: 0; actual Source approvals/grants: 0; real Opportunities registered: 0. No commit/push/build/deploy. Publish/Worker/Auto Approval defaults unchanged OFF.
- Previous Preview Auth session remains ended; no new login attempted or temporary reviewer enabled. Current live reviewer state was not re-read by this GET probe because service_role has no reviewer-table SELECT permission.
- Resume at release validation preparation and the bounded automatic publication contract above. Do not restart completed tests or repeat K-Startup credential questions. Local integration GO; Production autonomous completion remains unverified/NO-GO pending the stated execution approvals and real Source conditions.

## Previous checkpoint — continuous execution, 2026-10-23 18:20 KST

Historical checkpoint: the current resume section above supersedes its pending work. Earlier entries are chronological evidence, not outstanding authentication requests.

### Completed in this continuation

- Compared current code with release HEAD `6219bbed7bccd3774db303b05018c90ffc828974`, the code baseline associated with the last observed deployed digest `sha256:dfdcadeea014e8018ee85a1a3b4d2694b7ed2c60558189be2a0f00de7137948b`. No fresh Production/GCP read was performed in this continuation; the timestamped observation below is not a live-state claim.
- Confirmed two distinct outstanding automation boundaries: Metadata approvals are authenticated and exact Source/URL-specific (015/016); existing automatic Full Raw approval cannot substitute. `PublishWorker.run_once` consumes Full Raw Queue only; Metadata atomic publication is currently the explicit Candidate-ID method/CLI. PUBLISH_READY alone does not schedule Metadata publication.
- Compared the preserved partial Evidence tree: newer Metadata Evidence (+208/-5), extraction (+95/-8), SDK (+75/-12) and validation (+39/-1) changes differ from the starting release; 019 is absent from this release. These are not a drop-in replacement for local research changes, and were not bulk copied. Counts describe the comparison before the time guard correction below.
- Reproduced an actual readback defect offline: after a lost Atomic Publish response, a mismatched completion Candidate could be reported ALREADY_PUBLISHED. Unified exact Candidate/Opportunity/external-ID confirmation across prior completion, lost response and successful RPC response paths. Unreadable completion returns UNKNOWN without retry or raw exception text. These are local fault-injection findings, not evidence of corrupt Production rows.
- Reproduced a precision-loss defect: `2027-04-13 18:00` and `2027-04-13 오후 6시` became DATE_ONLY. Added the minimal conservative clock guard (reusing the preserved extraction approach, not its broader timezone policy): extraction holds unresolved/additional clocks; shared Evidence replay rejects a date-only value that discards a clock from its citation. Explicit-offset timestamps and genuine date-only values remain supported. No timezone inference or weaker Gate.

### Actual validation

- Publish/readback and existing Full Raw publishing tests: **61 passed**. A follow-on concurrency reporting correction preserves the RPC `already_published` disposition instead of counting it as a new publication; the affected readback/Service-contract tests then passed **46 tests**. Before the fix, the mismatched-Candidate reproduction failed with ALREADY_PUBLISHED instead of UNKNOWN.
- Metadata precision/extraction/SDK/follow-up/Runtime tests: **85 passed**.
- Shared official Evidence, pipeline and integration regression tests: **34 passed**. No entire Python suite rerun.
- Existing isolated Atomic Publish integration on PostgreSQL **17.10**: exit 0; all five categories, retry idempotency, concurrent registration, revoked approval, stale assessment, mismatched payload, completion-failure rollback and anon denial passed. Local temporary server shut down normally. Wrapper: `/private/tmp/aipath_atomic_publish_pg17_validation.mjs`; test SQL unchanged, no Supabase connection. This test covers 001–018 plus Service 004, not 019 or Python-to-live-DB publication.
- The earlier actual Preview positive Runtime E2E remains valid for its recorded code/fixture. The new readback/precision changes have local tests only; do not describe them as newly Preview-verified.

### Changed files this continuation

- `agent_collect/publishing/worker.py`: exact-ID reconciliation and bounded UNKNOWN outcomes.
- `tests/test_agent_metadata_publish_readback.py`: new fault-injection coverage.
- `tests/test_agent_metadata_service_contract.py`: existing successful completion fixture now includes real Service row ID.
- `agent_collect/discovery/evidence.py`: DATE_ONLY cannot discard cited time.
- `agent_collect/metadata/extraction.py`: reject unresolved/additional clocks before field creation.
- `tests/test_agent_metadata_time_precision.py`: parser, replay and real SDK handoff offline tests.
- This progress record and the out-of-repository local release manifest. All earlier dirty files and main remain preserved.

### Remaining work / next priority / required approvals

1. User confirmed existing K-Startup API access/key file but has **not supplied the path**. A follow-up question requests only the absolute file path, never key contents. Next priority is verify credential file permissions and approved endpoint/license scope, then evaluate existing API collection/extraction compatibility. Do not invent a key, register an account, or call a guessed endpoint.
2. Existing ApiCollector can GET/validate JSON but does not by itself establish per-notice Metadata Evidence mapping. Full Raw `ExtractionRunner.run` explicitly returns UNSUPPORTED_MEDIA_TYPE for JSON; current Metadata extractor is labelled HTML. Collection success is therefore not extraction compatibility. Also, CollectionRunner supports secret header_env injection but no secret query-env injection, and persists request/final URLs in RawDocument provenance. Do not put a service key into stored Source URLs/query_params to work around this. Any query-authenticated API path needs an independently verified, non-persisting credential boundary first. Real API schema and permitted representation must be inspected before designing a minimal mapping or declaring this an eligible Source.
3. Authenticated exact-URL Metadata approval is a deliberate security boundary. Unattended approval of newly discovered URLs cannot be fabricated using an old reviewer or Full Raw scope. Any change to that authorization policy needs a concrete Source scope and separate user approval. No new reviewer privileges or approvals were created here.
4. Automatic Metadata publish dispatch is still absent. Do not report the explicit-ID atomic method as a scheduled scanner. Its activation/selection policy must preserve exact eligibility, fresh page/permission checks and bounded execution; the present release does not enable automatic Metadata dispatch.
5. Official documentation read: https://www.data.go.kr/data/15125364/openapi.do confirms REST JSON/XML, free access, unrestricted dataset license and authentication/application requirements. The public page did not expose field-level schema in the retrieved content; no endpoint or payload was inferred from third-party search results. No API request was made. The dataset permission is not a blanket license for linked HTML/attachments.
6. Actual image build, approved Production deployment, real Source approval, real publication and permanent flags remain gated. No commit, push, deployment or external paid call was performed.

### Safety and resumption

- Production/Preview DB writes this continuation: 0. Cloud Run/Scheduler/configuration changes: 0. Real Source grants/approvals: 0. Paid API calls: 0. Real Service publication: 0.
- Previous Preview cleanup proof: reviewer OFF, test Auth sessions 0, Source HOLD, Candidate REVIEW_REQUIRED, original 15-table fingerprints and Scope intact. It was not redundantly rerun.
- Resume from the pending K-Startup key-path response and the exact authorization/dispatch boundaries above; do not repeat the completed Preview authentication/E2E. Overall autonomous operation remains NO-GO; local safety corrections are validated.

## Previous checkpoint — authenticated Preview Runtime E2E completed

This section supersedes the pending-authentication and positive-Preview blockers in the historical entries below. It does not establish Production readiness or real website publication.

- Preview only: `qonlnoyppopyztulvgzv`. Existing authenticated reviewer UUID `508fe790-2100-428d-bcfd-d781a3b9c620` and live waiting process were rechecked before the user's exact Source-specific temporary grant was executed.
- Synthetic Source: `59952bfa-5118-478a-aa2d-8faef58b8a35`; marker `runtime-handoff-cb47fd4a036e4019926da45b05063392`.
- Actual `AutomationRuntime.run_once` run `fa7962d2-1b23-43d6-9079-d4e509c0d4b4` completed at 2026-10-23 08:58:29 UTC (17:58:29 KST), errors empty. One lead processed; one Candidate reached PUBLISH_READY.
- Persistent Candidate `a96ec828-1375-4965-ae6b-398402b7f1ea`: DB audit records VERIFIED / UNIQUE / PUBLISH_READY, DATE_ONLY, with original fixture Evidence references. Real Preview SDK/RPC, lease and log persistence were used. Discovery and page transport used synthetic fixtures: this is not a live Tavily search, production Query claim or external website access.
- Authenticated approval and revocation audits were preserved. Revocation automatically changed PUBLISH_READY to REVIEW_REQUIRED; final reassessment remained REVIEW_REQUIRED. Source ended HOLD and inactive; it was never ACTIVE.
- Temporary reviewer permission was turned OFF immediately after OFF_REQUIRED, with its previous inactive Source scope restored. At 18:00:21 KST administrator readback confirmed active reviewers 0, this test's Auth sessions 0 and active automation leases 0. The harness signed out and reported FINISHED, success=true, errors empty.
- All pre-existing full-row fingerprints across 15 tables matched. Scope 4 full-row MD5 remained `c767ebc7dae3c2423424d13bd5ce3846`; Opportunity 7 and Publish Queue 0 remained unchanged. New rows were restricted to this synthetic Source, review/approval audits, Candidate/events, synthetic Query/result and Runtime log. Audit-bearing synthetic rows were retained, not deleted.
- Actual Service writes 0; atomic publish calls 0; paid API calls 0; external page calls 0. Production DB, Cloud Run, Scheduler and real publishing configuration were not changed.
- Proof: `/private/tmp/aipath_runtime_handoff_verified_result.json`, `/private/tmp/aipath_preview_runtime_handoff_state.json`, `/private/tmp/aipath_runtime_handoff_cleanup_verified.png`. The local manifest records hashes and IDs; these temporary files are not release inputs.

### Follow-on local release checks

- Existing build-context and Shadow entrypoint tests: **6 passed**. Required local runtime imports: **10 passed**, with network connections blocked during import.
- Static checks confirmed six required runtime/`opportunity_status` inputs survive both ignore boundaries. These checks use gitignore interpretation; Docker's actual parser and a built image remain unverified in this continuation.
- Scanned the 13 changed/untracked release files for private-key blocks and common provider/token patterns: zero findings. This is a limited pattern scan, not proof against every possible secret format. `git diff --check` passed.
- No additional product-code changes, full-suite rerun, commit, push, Cloud Build or deployment were performed. Previous focused and PostgreSQL 17.10 results remain historical evidence; they were not redundantly rerun.

### Next gates

1. Preview synthetic Runtime handoff is GO; Production autonomous operation remains NO-GO. New real Opportunities registered: **0**.
2. The optional pre-approval research path still requires an established reviewed access/license scope. It does not grant arbitrary Source rights or automatically create Metadata approval. A real usable Source permission basis (including the pending K-Startup API access question) remains unresolved.
3. The 13-file local release manifest is prepared at HEAD `6219bbed7bccd3774db303b05018c90ffc828974`. Newer Korean/Timezone/Minimum Evidence work in the separate partial directory is not silently included. Release integration, actual image validation and Production deployment require a separately reviewed scope and authorization.
4. Real Source reviewer permission, real approval, actual publication and permanent autonomous flags require their own exact approvals. This successful synthetic grant does not authorize any of them.

## Resume baseline and boundaries

- Working checkout: `/private/tmp/aipath-autonomous-runtime-release`, HEAD `6219bbed7bccd3774db303b05018c90ffc828974`.
- Main `/Users/michaelis/aipath-korea` has unrelated and prior uncommitted work; preserved.
- `/private/tmp/aipath-metadata-publish-atomic` exists but is not currently a Git checkout. Its newer Evidence/Policy changes have not been silently copied into this release.
- During the initial local implementation stage, no commits, pushes, builds, deployments, migrations, Source approvals, reviewer grants, paid model/search calls or DB writes were performed. The later explicitly approved Preview-only writes are recorded in the latest checkpoint above.
- Production writes, deployment, real approval/publication and permission changes require scoped execution approval. Previous one-time grants do not authorize another run.

## Implemented locally

1. `AgentRuntime.process_discovery_metadata` optionally performs pre-approval investigation before the zero-approval return. It reuses `ApprovalScope` and `OfficialFollowupExplorer`; it does not create an approval or Candidate.
2. Optional `AGENT_DISCOVERY_INVESTIGATION_SCOPES_JSON` uses the existing scope JSON schema. Unset means no investigation HTTP. At most five scopes and five investigated results per handoff, only succeeded Queries, exact result/query ownership and complete bounded result scans.
3. An operator-reviewed explicit license scope, unexpired dates, exact origin/path and unchanged officiality/terms document hashes are required. Robots, pinned transport, login restrictions and HTML checks remain active. Obvious attachments/download paths and query/redirect scope escapes are denied before fetching the target. A server can still return non-HTML for a page URL; existing bounded transport rejects that response and stores no body.
4. Research explores depth one, at most two child links per result. Leads remain unverified; only aggregate counts return to Automation. No Source registration, permanent raw storage, approval creation, Metadata write or publish occurs in this path.
5. Optional research failure is counted without exposing remote error text or suppressing already-approved Metadata processing. Dry run does not invoke the handoff.
6. Extraction provider failures now emit allowlisted exception kind, immediate cause kind and valid HTTP error status through the existing `EXTRACTION_FAILED` log. No response/request text, URL, token, API key or raw exception message is retained. Error codes, Budget Guard and retry semantics remain unchanged.

The investigation scope is a reviewed permission configuration, NOT a Metadata approval and NOT evidence of publication permission. No real scope was invented or activated. The new investigation path cannot automatically approve an arbitrary new Source.

## Tests actually run

- Focused suite: 67 passed (preapproval investigation, Metadata Runtime wiring, follow-up, extraction diagnostics, extraction, processing and Runtime).
- After stronger exact diagnostic assertions and import cleanup: 9 affected tests passed.
- After blocking policy-document attachment URLs as well as notice attachments: 23 affected follow-up/Runtime tests passed (one additional test).
- `git diff --check`: passed.
- Real AutomationRuntime.run_once is exercised for approved Metadata, zero approvals, optional research, dry-run and research-error isolation.
- Attachment and query redirect denial, changed terms, expired scopes, robots denial and result identity mismatch tested without external requests.
- No full regression suite or real model regeneration was run. This is not a release-ready declaration.

## Read-only operational checks

At approximately 2026-10-23 10:58 KST:

- Existing Chrome Cloud Shell reconnected with the existing account. Read-only gcloud describe confirms image digest `sha256:dfdcadeea014e8018ee85a1a3b4d2694b7ed2c60558189be2a0f00de7137948b`.
- Scheduler ENABLED, `*/30 * * * *`.
- AI Planner, AI Discovery Execution, Auto Approval, Publish, Publish Worker all false.
- No new Shadow/Canary was executed. Browser execution list showed scheduled execution `aipath-agent-shadow-n687j` successful at 10:30; this does not establish success of the un-deployed changes.
- Production: Opportunity 17, Agent Source 12, Metadata approval 0, Metadata Candidate 0, Publish Queue 1.
- OpenAI budget usage daily 5, monthly 33 (configured prior limits 5/100); Tavily daily 2, monthly 22. Do not reset UNKNOWN reservations or retry model calls.
- Service-role SELECT on `agent_metadata_reviewers` returns SQLSTATE 42501 in both projects. Reviewer activity remains unverified by this credential; no grants were added to bypass this.
- Production Opportunity full-row SHA-256: `497b3251b9aaba4ed5dd314fc21eebd4bd57aaa989c0518882a5d9835d993e41`.
- Preview: Opportunity 7, Agent Source 33, Metadata approval 16 (15 operational cohort), Metadata Candidate 14, Publish Queue 0. Do not assume all existing operational-cohort rows represent real official Sources; prior synthetic E2Es used that cohort.

## Preview SDK verification

Read-only script `/private/tmp/aipath_preview_runtime_readonly_probe.py`:

- Uses the real Preview SDK and `SupabaseMetadataEvidence.assess` for all 14 persisted Candidates with no freshly fetched page or permission document.
- All 14 return REVIEW_REQUIRED; none becomes PUBLISH_READY without evidence.
- Full-row hashes for Opportunity, Agent Source, Metadata approval, Metadata Candidate, approval audit, candidate audit and Publish Queue match before/after.
- Zero writes, external page calls, grants or auth login changes.
- This verifies a persisted negative path and read contracts, NOT positive authenticated Source approval, fresh HTTP Evidence, atomic publication or the whole Runtime against live Preview.

## Remaining work / approval boundaries

1. No operational Metadata approval exists. Known current search results do not have an established permission scope. Existing Full Raw approvals do not imply Metadata display/publication rights.
2. Investigated alternative: official public-data listing https://www.data.go.kr/data/15125364/openapi.do (K-Startup API) states free service, unrestricted dataset usage and automatic application approval. This applies to that API dataset, not blanket HTML/attachments rights. The page states API authentication/application is required. Credential was not found in the inspected local configuration. User has been asked whether existing approved access and a safe key path are available; do not create credentials or apply on their behalf without the required approval.
3. Existing CollectionPlanner already supports REST_API, but K-Startup response-to-Evidence mapping, category fit and live API access remain unverified. Do not label this an acquired Source or qualifying Opportunity.
4. Positive Preview persistent Runtime validation still needs an explicit fixture plan, actual authenticated reviewer and source-specific temporary grant approval. Preserve existing 7 Opportunities, all prior Sources/Candidates and immutable audits. Do not reuse old temporary approvals.
5. Reconcile earlier Extraction/Evidence/Policy work with this verified release base before any release; no semantic Evidence policy was relaxed here. Model accuracy remains unverified because daily OpenAI budget is exhausted.
6. Once local and Preview checks pass, prepare an exact release manifest and request Production deployment approval. Real Source approval, one-record publication and permanent automation flags require separate approvals.

## Completion status

- New official Opportunities registered in this stage: 0.
- Local safe investigation wiring: validated.
- Preview negative SDK path: validated read-only.
- Production autonomous completion: NO-GO pending permission/authentication, positive persistent validation, deployment and real publication. The final Definition of Done has NOT been met.

## Continuation — Extraction contract and Candidate readback

Local integration only, 2026-10-23:

- Copied the already-developed v3 prompt from preserved main into this release checkout. It requests an exact grounded summary passage and an explicitly cited notice L000; it does not backfill unsupported URLs or weaken validation.
- Copied only `SupabaseMetadataEvidence.create_candidate` readback from the preserved Metadata development files. Migration 015 can return an existing Candidate ID for the same approval/URL/application period even when a new request carries different facts or Evidence. SDK now reads `facts,field_evidence` and rejects a mismatch before assessment/status recording. This is a post-RPC consistency check, not a transaction rollback: a newly inserted held Candidate can remain if readback fails. Do not delete it or retry blindly.
- No Korean date replay, timezone policy, usage-rights policy, Migration 019, or other unrelated changes were copied.
- Focused extraction/cache/Metadata/Runtime tests: 60 passed. Added an end-to-end SDK handoff negative test showing conflicting readback cannot call `assess_and_record`; the 5 readback tests then passed. `git diff --check` passed.
- Cache tests include old/new prompt separation and no paid fallback on Processing cache-version mismatch. No live model accuracy claim is made.
- Extended the existing isolated PostgreSQL approval test to demonstrate same-period reuse with changed facts/evidence preserves the original held row. This proves why client readback is needed when it can run.
- PostgreSQL integration did NOT run successfully: bundled 17.11 executable lacks its OpenSSL 4 runtime; available embedded PostgreSQL 17.10 starts but initdb fails at `shmget` with `No space left on device` (shared-memory resource limit, not a disk-space diagnosis). Sandbox failure was separately confirmed first. No existing PostgreSQL process/shared-memory object was terminated or removed. Failed initdb removed its own fresh temporary directories.
- This continuation performed no Supabase reads/writes, auth/grants, paid API calls, deployment, Git commit/push or runtime execution. Prior live-state observations remain timestamped above, not presented as newly refreshed.

Next dependent gates: restore a usable isolated PostgreSQL environment; positive Preview persistent E2E with an authenticated reviewer and separately approved temporary Source-specific permission; obtain a real usable Source permission/authentication basis. The K-Startup credential question remains unanswered. Existing user approvals are not repurposed for new Source grants or production publication.


## Continuation — isolated PostgreSQL recovered

- Confirmed the failed local initdb's exact orphaned SysV shared-memory segment (ID 7864322, key 0x04bb4ad2, creator PID 77979, 56 bytes, zero attachments) and that its creator was no longer running. Removed only that segment; no PostgreSQL process or unrelated segment was stopped/removed. This freed one of the 32 configured slots.
- Ran the existing integration test through `/private/tmp/aipath_candidate_readback_pg17.mjs` against embedded PostgreSQL **17.10**. Exit 0: metadata reviewer authentication, audit, idempotency, RPC-only writes, revocation and RLS passed. The added same-period/different-payload case preserves the original Candidate and REVIEW_REQUIRED status.
- The isolated server shut down normally. This supersedes the earlier shared-memory blocker, not the missing 17.11 runtime. Do not claim a 17.11 test.
- No Supabase write, external provider call, Source approval, grant, deploy, commit or push occurred.
- Positive Preview validation is the next dependent step; user authentication and a new narrowly scoped temporary reviewer grant are still required.


## Continuation — Preview positive Runtime E2E prepared; authentication deferred

Actual Preview read-only preflight at 2026-10-23 11:35 KST, through the existing Chrome Supabase Dashboard:

- Project `qonlnoyppopyztulvgzv`; reviewer `508fe790-2100-428d-bcfd-d781a3b9c620` exists and is OFF; active reviewers 0; active automation leases 0.
- Opportunity 7, Metadata Candidate 14, Publish Queue 0; operational approval cohort 15. Candidate RPC and enabled Source-scoped audit trigger exist.
- SDK read contract checked: Agent Source 33; Source review events 69; Metadata approvals 16; approval events 19; Candidate events 25; publish completions 8; Discovery Queries 8; Search Results 8; Source Candidates 5; Budget reservations 5; Source schedules 14; logs 7.
- `service_role` SELECT on `opportunity_status_automation_scope` correctly returns 42501. No privilege widening. Existing administrator SQL Editor read confirms Scope 4 at 11:40 KST, full-row MD5 `c767ebc7dae3c2423424d13bd5ce3846`. Re-snapshot immediately before/after the eventual E2E.

Prepared `/private/tmp/aipath_preview_runtime_handoff_e2e.py` (outside release files):

- Interpreter with actual SDK: `/opt/anaconda3/bin/python`; Homebrew `python3` lacks Supabase SDK. The initial user command was corrected before authentication.
- Default execution is offline fixture validation only. Its real extractor/resolver returns VERIFIED / UNIQUE / PUBLISH_READY; bytecode compilation and `git diff --check` pass. This is NOT Preview persistent success.
- `--execute` requires the user's private TTY password and existing Preview Auth UUID. Credentials stay in process memory; outbound SDK HTTP is restricted to the Preview host.
- Before grant it creates one clearly labelled synthetic, inactive Source in REVIEW through existing repository/review RPC, then emits AUTH_READY with its actual ID and waits. No fake reviewer UUID or grant is created.
- After a NEW explicit Source-specific grant approval, intended writes are: synthetic approval + immutable audit; Source REVIEW→APPROVED (never ACTIVE); one synthetic completed Query and SearchResult; one actual AutomationRuntime.run_once with real Preview lease/log and real Metadata SDK/RPC; at most one Metadata Candidate and assessment event. Stored Discovery result and page/permission fixtures replace paid/search/website I/O only. Publish/Planner/AI Execution/AutoApproval/Collection/Processing remain OFF in the test settings.
- Finally revoke the synthetic approval with the authenticated RPC, reassess to REVIEW_REQUIRED, hold the synthetic Source, signal OFF_REQUIRED for the operator to deactivate the exact reviewer, verify OFF, sign out locally, and compare all pre-existing rows. Audit-bearing Source/Candidate/Query/log rows are preserved; no deletion or Service write. Scope integrity is a separate administrator read because its SELECT is intentionally denied.
- Existing table snapshots preserve full rows, not only counts; unexpected additions outside this fixture fail verification. No atomic publish RPC is called. This validates a synthetic Preview Runtime handoff, not actual paid Discovery or website access.
- Do NOT grant permission until a current live AUTH_READY process is confirmed and its exact Source ID reviewed. Old approvals do not apply. If grant is activated and the process stops, immediately deactivate the exact reviewer via the existing admin path, record any unresolved synthetic revocation, and do not restart blindly.

User is currently on mobile. No authentication was requested through chat, no E2E --execute has been run by this agent, and no new synthetic DB rows or reviewer grants were created. Pause the authentication-dependent step until the user can enter the Preview password at their Mac. Production was not accessed or changed in this continuation. No external provider calls, Cloud Run/Scheduler changes, release actions or Service publication occurred.


## Continuation — Preview cleanup fault isolation

- Before live execution, review found that a failed synthetic approval revocation could skip the following Candidate hold and Source HOLD operations in the test harness. This was a harness defect, not a proven Production defect.
- Extracted independent cleanup attempts into `cleanup_synthetic` in `/private/tmp/aipath_preview_runtime_handoff_e2e.py`. Approval revocation, Candidate reassessment with no page, and Source HOLD now each run even when an earlier action fails. Failures remain explicit; no retry, deletion, grant widening or silent success. Reviewer OFF and Auth sign-out remain mandatory separate cleanup steps.
- Offline fault-injection tests `/private/tmp/test_aipath_preview_runtime_handoff_cleanup.py`: 7 passed, covering normal cleanup plus approval read, revoke, Candidate read/hold, Source read/hold failures. Assertions confirm exact Source/Candidate IDs, no suppressed later safety action, no retry, and sanitized errors.
- Offline extractor/resolver fixture check passes. Product code was not changed by this continuation. No full regression suite was rerun.
- No current AUTH_READY state file exists. Live Preview E2E, new synthetic rows and reviewer activation have not begun. User previously reported being on mobile; current Mac availability was asked separately. Require actual Auth before any new grant.

## Search facts operation preparation — 2026-10-25

- User explicitly approved simplifying the *new search-result registration path* to public institutional notice facts without Source-by-Source reviewer grants or a rights JSON. Existing Source-approved Metadata and Full Raw paths are unchanged. The K-Startup API experiment was removed from the release worktree and is not a release dependency.
- Isolated release preparation clone: `/private/tmp/aipath-search-facts`, based on pushed `codex/search-registration-mvp` commit `b2471f1`. Modified `agent_collect/metadata/extraction.py`, `agent_collect/metadata/search_registration.py`, `agent_collect/runtime.py`, `tests/test_agent_search_registration.py`; added `docs/SEARCH_FACT_REGISTRATION.md`. Main's dirty worktree was not touched.
- New default-OFF `AGENT_SEARCH_FACT_REGISTRATION_ENABLED` path consumes completed Discovery results, reads a bounded HTTPS `.go.kr`/`.ac.kr` individual notice through existing robots/DNS-pinned transport, replays page facts, normalizes dates, checks Service duplicates and attempts at most one atomic registration per Automation run. It stores facts/page digest, never HTML or attachments. Explicit use prohibitions on the fetched page block the path. `AGENT_PUBLISH_ENABLED` remains a separate OFF-default publication gate; legacy approved-Metadata path is untouched.
- Local full Python regression before the final explicit-prohibition hardening: **630 passed**; focused search/Runtime/time suite **67 passed**. The subsequent HTML-separated prohibition and ambiguous Korean heading tests passed in the 47-test focused search suite. `git diff --check` passed before that final change. No paid provider or live site fetch was made for these tests.
- Preview `qonlnoyppopyztulvgzv` synthetic SDK E2E: one uniquely marked technical Source, Competition Category and synthetic Opportunity were created; Service 004 atomic registration returned `registered`, retry returned `protected_existing` for the same ID. Exact synthetic rows were removed. Full-row fingerprints for existing `sources`, `categories`, `opportunities` matched before/after; Opportunities stayed 7. Scope SELECT remains denied to the SDK; its post-cleanup row-level state was not independently fingerprinted. Production was not accessed or changed.
- **Not operational yet:** new code has not been deployed. Production technical `AIPATH AI Discovery` Service Source, current eligible real notice, current GCP/Scheduler/DB preflight, one-record public canary and sustained ON remain unverified. Production Source creation, deployment, public registration and permanent flag changes require separate exact approval. Resume at release scope/security review, then current Production preflight. Do not claim public registrations or autonomous operation.
