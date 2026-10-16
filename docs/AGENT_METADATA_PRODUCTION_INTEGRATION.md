# Metadata-first Production integration: release checkpoint

This release branch starts at `origin/main` commit
`1e6eddc80468f57875a516d5ddeba75559ba05f9` and brings the existing
PHASE 12J evidence, Deep Search and search-quality work together with the
metadata-first resolver. The main checkout and other dirty worktrees are
preserved. The implementation is **not deployed**.

## Contracts and policy boundary

- Deep Search `FollowupLead` is a lead, never field evidence. The new
  `SupabaseMetadataEvidence.process_followup_lead` accepts only an individual
  lead that exactly matches a reviewed operational Source and a current
  authenticated metadata approval. When the caller supplies an already-fetched
  transient page and permission document with `facts=None`, a deterministic
  extractor reads only unique, explicitly labelled fields from the page. It
  omits ambiguous fields, then replays all field evidence and checks the existing
  Gate before creating a held Candidate through the narrow 015 RPC, then
  rechecks and records the result. It makes no HTTP request, stores no raw
  page, enqueues nothing, and cannot publish to Service.
- The bridge does **not** create field claims from search snippets or
  automatically fetch an official page. A separately permitted transient page
  acquisition caller is still required before the full automated
  Discovery-to-Candidate path exists. A missing or changed permission document
  prevents extraction.
- Full-raw Source Approval does not grant metadata usage. Policy review must
  establish public reading, automated access, fact use, transient recheck,
  exact URL/path scope, official relationship, and an evidence hash. Raw
  storage and redistribution remain separate permissions.
- Operational approval is tied to an allowlisted authenticated `auth.uid()`
  and an append-only event. Service-role code cannot assert human approval.
  Revocation is append-only and places ready metadata Candidates on hold.
  Approval, revocation, expiry, changed permission evidence, missing field
  evidence, uncertain dates, incomplete duplicate scan and duplicate matches
  all fail closed before the Gate.

## Database state and migration order

Read-only REST counts before Preview 015 on 2026-10-15 KST:

| Project | Service | Agent Source | Agent Raw | Agent Candidate | Publish queue | Metadata approval | Metadata Candidate | 015 audit table |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Preview `qonlnoyppopyztulvgzv` | 7 | 8 | 4 | 0 | 0 | 1 | 1 | `PGRST205` |
| Production `fehlvyoskgphlodzawfn` | 17 | 11 | 16 | 16 | 1 | `PGRST205` | `PGRST205` | `PGRST205` |

`PGRST205` means the object is absent from the current PostgREST schema
cache; it is **not** a PostgreSQL catalog proof. Preview 014 was separately
approved and applied in the prior validation task. The saved synthetic
Preview Candidate was originally `PUBLISH_READY`, but its field observations
are older than the 15-minute replay window and current read-only replay
correctly returns `REVIEW_REQUIRED`. That fixture does not prove current
operational readiness.

Migration 015 was absent from both projects at the earlier checkpoint. After
separate user approval, the exact 18,385-byte SQL file with SHA-256
`bdc3a80142df79c742a8c88f9495026ac0909eb9d562421ad613199de22cc817`
was applied **once to Preview only** on 2026-10-15 KST. SQL Editor returned
`Success. No rows returned`. The three 015 tables, four restricted RPCs,
RLS, audit immutability triggers, and narrow role grants were confirmed in
the Preview catalog. Service-role direct INSERT/UPDATE to 014 metadata tables
is revoked. Anon SELECT to the three new tables returned SQLSTATE `42501`;
service-role audit SELECT is allowed. Existing Source, Opportunity, RawDocument,
Agent Candidate, Publish Queue, Metadata approval and Metadata Candidate
row fingerprints matched their pre-apply values after excluding only the new
nullable `request_id` column. Production 015 remains unverified and untouched.

The later Preview E2E used a real authenticated Preview user and a separately
approved temporary reviewer grant. A unique synthetic Source was approved,
its field evidence reached `VERIFIED`, duplicate result `UNIQUE`, and the
Publish Gate reached `PUBLISH_READY`. Authenticated revocation then held the
same Candidate at `REVIEW_REQUIRED`. The temporary reviewer grant was turned
off, the synthetic queue remained empty, and the seven existing Service
Opportunities were unchanged. The append-only approval and revocation audit
was preserved. This proves the Preview contract, not permission to approve a
real Production Source.
`agent_collect/migrations/015_metadata_approval_runtime_draft.sql` depends on
014, adds authenticated reviewer allowlisting, append-only approval and
candidate events, RPC-only machine writes, revocation hold, and idempotency.
Its scope is access-sensitive because it revokes the broad service-role table
writes originally granted by 014 and replaces them with narrow RPCs.

The 2026-10-17 Production catalog read shows 014 and 015 absent, while the
existing Source review RPC, Auth UID helper, Agent tables and Service atomic
registration RPC are present. The exact-processing seed RPC from 013 is also
absent in Production; the Metadata SDK does not call it. Production migration
still requires a final catalog-level dependency check and execution approval.
No migration is inferred from its file number alone.

## Validation and release gates

The isolated 001–015 PostgreSQL integration test covers reviewer auth,
approval/revocation, append-only audit, RLS, narrow RPC writes, idempotency
and concurrent approval. The 12A, 12B, 12E1, 12J and metadata-first
PostgreSQL integrations also passed in the separate release worktree. Python
regression: 428 passed, including the Metadata SDK startup hook. No Production Migration,
Git release commit, push, Cloud Build, Cloud Run cutover or real Source
promotion has occurred at this checkpoint.

The release is `NO-GO` until the Production schema/catalog preflight, image
build and shadow controls are verified. The automated page acquisition
handoff remains outside this release and cannot be claimed as live Discovery.
AI Planner, AI Discovery execution, Auto Approval, Service Publish and Publish
Worker defaults remain false.
No real official Source has a verified metadata-use approval in this work.
The official AISTATS 2027 Call for Papers lists its full-paper deadline as
2026-10-06, before this 2026-10-15 checkpoint, so it is not an open canary.
No reviewed terms or license establishing automated metadata use was found
for it; its published AI-use policy concerns submitted papers, not this
collector's access or reuse rights.
