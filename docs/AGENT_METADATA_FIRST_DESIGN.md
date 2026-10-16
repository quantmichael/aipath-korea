# Metadata-first discovery and evidence: validation draft

This records the earlier PHASE validation result. For the later operational
SDK bridge, Migration 015 draft, and current release blockers, see
[Metadata-first Production integration](AGENT_METADATA_PRODUCTION_INTEGRATION.md).
The saved synthetic Evidence observations have since aged beyond the
15-minute replay window; their **current** read-only result is
`REVIEW_REQUIRED`, even though the original fresh E2E passed.

This is an offline, synthetic validation path. It has no scheduled runtime
entry point and cannot enqueue or publish a Service Opportunity. The existing
RawDocument collection, Processing, PHASE 12J evidence, and Publish Gate paths
remain in place.

## Access and use are separate decisions

| Right | Evidence required | This path |
| --- | --- | --- |
| Public human reading | Public page accessible | Insufficient alone |
| Automated access and recheck | Site policy and robots permit the exact URL | Required |
| Extract and use factual metadata | Reviewed terms, license, or written permission | Required |
| Persist original HTML | Separate storage permission | Never exercised here |
| Redistribute original text | Separate republication permission | Never exercised here |
| Official API/open-data license | License and scope for the endpoint/data | May supply policy evidence, but is not inferred |

An official domain or permissive robots.txt does not supply metadata-use,
storage, or redistribution permission. Search snippets are discovery leads,
not evidence. The AI EXPO 2027 page remains on review hold because no
metadata-use and transient recheck permission has been established.

## Two paths

The existing full-raw path retains Source Approval, RawDocument SHA-256,
Processing, PHASE 12J replay, Verification, and Publish Gate. The new
metadata path accepts an independently reviewed official Source, a
metadata-scoped permission record, field claims, an in-memory official page
recheck, a fresh complete Agent and Service duplicate snapshot, then calls
the existing `DeduplicationEngine` and `PublishGate`.

`MetadataEvidenceResolver` does not fetch or persist the page. The caller
must acquire it under the approved automated-access scope; the resolver
rejects a missing, stale, changed, oversized, or wrong-URL page. Each fact
contains its source URL, block/link ID, short quote, observed time,
confidence, and policy digest. The same deterministic replay function used
by PHASE 12J verifies those references against the transient page.
`official_url` must cite the fetched page link. Date-only deadlines expire
at the end of the calendar day in Asia/Seoul; exact timestamps retain their
explicit offset. Unknown or mixed recruitment precision is held.

Metadata approval is deliberately distinct from full collection approval.
It does not set `source_is_collectible`, write a RawDocument, create a
Processing Job, or grant raw storage. A human review record and a trusted
loader must bind the Source and policy in any future operational adapter.
The current code accepts explicit in-memory reviews for synthetic tests
only; it is not connected to Production or Cloud Run.

The resolver's `PUBLISH_READY` is an **offline gate decision**, not an
`agent_publish_queue` row or authorization to call Atomic Registration.
Before operational use, an SDK adapter must verify the trusted human
review identity, current policy document, complete live duplicate reads,
Agent/Service registration contract, and persist a verification audit.
Service Publish remains off.

## Schema and Preview boundary

`agent_collect/migrations/014_metadata_first_draft.sql` creates separate
metadata approval and candidate tables with Source FKs, cohort labels,
RLS and grants. It does not alter existing Agent/Service rows or publish
flags. After isolated PostgreSQL validation and explicit user approval, the
exact SQL (5,555 characters; FNV-1a checksum `1b725282`) was applied **once**
to Preview `qonlnoyppopyztulvgzv` on 2026-10-15. Production
`fehlvyoskgphlodzawfn` has not received it. The SQL header still says
"draft" because it is the exact approved application artifact; its
Production status remains draft.

The Preview SDK synthetic fixture
`metadata-first-f9d86cc472254c9da4cf05e36f4c9f88` used an in-memory
`fixture.example.org` page and permission document. It created one synthetic
Source in `REVIEW`, one metadata approval and one metadata candidate. The
10 field references replayed, duplicate check returned `UNIQUE`, Evidence
Verification returned `VERIFIED`, and the existing Gate returned
`PUBLISH_READY` with `DATE_ONLY` precision. Existing Preview rows were
byte-for-byte unchanged in SDK snapshots; RawDocument remained 4, ordinary
Agent Candidate and Publish Queue remained 0, and Service Opportunity
remained 7. Synthetic Source review audit and metadata records are retained.
No actual site request, raw storage, Service registration, or publish ran.
The synthetic reviewer is explicitly a fixture identity, not a real organizer
or human approver.

A read-only SDK replay of the saved row independently returned `VERIFIED`
and `PUBLISH_READY`. Removing the approval, changing the permission document,
removing the transient page recheck, substituting a search snippet, or adding
a different Candidate with the same official URL each returned
`REVIEW_REQUIRED`. `EXACT_TIME`, `UNKNOWN`, expiry and date boundaries are
covered by the offline regression tests; the saved Preview row is
`DATE_ONLY`. No Preview publish or ordinary Processing Job was executed.
The saved synthetic cohort metrics are official Sources 1, eligible Sources 1,
metadata Candidates 1, evidence resolution 100%, verified 1, ready 1,
review 0, duplicate 0, and approval bottlenecks 0. These are fixture results,
not operational discovery performance.

The existing Cloud Run Job `aipath-agent-shadow` was inspected read-only on
2026-10-15. `AGENT_AI_DISCOVERY_PLANNER_ENABLED`,
`AGENT_AI_DISCOVERY_EXECUTION_ENABLED`,
`AGENT_DISCOVERY_AUTO_APPROVAL_ENABLED`, `AGENT_PUBLISH_ENABLED`, and
`AGENT_PUBLISH_WORKER_ENABLED` were all `false`; no Job or Scheduler change
was made.

The draft is not yet production-ready: it does not authenticate operational
reviewer identity, provide revocation semantics, or enforce all replay
evidence at the database boundary. Those controls must be resolved before
any Production migration or automatic execution.

## Metrics

`metadata_metrics` reports discovered and eligible official Sources,
candidates, replay resolution rate, verified and ready counts, reviews,
duplicates, and approval bottlenecks. Synthetic and operational cohorts
cannot be mixed. Full-raw metrics remain separate.
