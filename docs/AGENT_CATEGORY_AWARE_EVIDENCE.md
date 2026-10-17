# Category-aware Metadata Evidence and Service contract release candidate

## Confirmed blockers and boundary

The deployed Metadata resolver required both `event_start` and `event_end`
for every category. Its event-window check also rejected their absence. The
database 014 `PUBLISH_READY` check requires those ten evidence keys, the 015
assessment RPC requires confirmed recruitment precision, and the existing
Full Raw `PublishGate`, `PublishCandidate` v1, and queue publisher support only
`conference`. The new Metadata Gate and read-only Service envelope preview
extend category evaluation without enabling the publish worker or changing
the Full Raw handoff.

The Production read-only check on 2026-10-17 found zero Auth users, zero
Metadata reviewers, zero operational Metadata approvals and zero Metadata
Candidates. No Preview user ID is transferable to Production. Supabase Auth
registration, email ownership verification, and reviewer grants are separate
operations requiring user participation and execution approval.
The Production Auth dashboard has Email enabled, new-user signups enabled,
email confirmation required, and custom SMTP disabled. Delivery to the
proposed address has not been tested; registration cannot be reported ready
merely from those switches.

## Evidence policy

All categories require replayed title, summary, organizer, official URL,
application start, application deadline, application method, and category.
The application interval must have equal known precision at both ends, be
ordered, and not be closed. Invitation-only, evergreen, unknown, or mixed
precision intervals remain `REVIEW_REQUIRED`; no date is fabricated.

| Category | Event start/end evidence | Evidence result when dates and policy pass | Publish Gate |
| --- | --- | --- | --- |
| Conference, Hackathon, Education, Meetup | Both required | `VERIFIED` | Metadata Gate may return `PUBLISH_READY` when duplicate check passes |
| Competition | Optional pair; if either is present, both must replay in order | `VERIFIED` | Metadata Gate may return `PUBLISH_READY` |
| Exhibition | Both required | `VERIFIED` | `REVIEW_REQUIRED`: Service mapping unavailable |
| Research, Grant, Program, Job | Optional pair; if either is present, both must replay in order | `VERIFIED` | `REVIEW_REQUIRED`: Service mapping unavailable |
| Unknown/unrecognized | No fallback | `REVIEW_REQUIRED` | `REVIEW_REQUIRED` |

The same Source approval, current permission digest, official transient page,
field replay, complete duplicate snapshot, and confidence threshold apply to
every category. Optional event evidence is validated whenever supplied.
Source permission and field sufficiency are independent decisions. Full Raw
Collection and its Conference Verification/Publish Gate remain unchanged.
Metadata `PUBLISH_READY` remains an Agent-only Gate result: it never enqueues
or calls Atomic Registration. A separate read-only Service preview additionally
requires active reviewed Source collection permission, current Metadata usage
approval, a fresh verified result, exact Service Source identity, and one
active Service category row. The preview returns an RPC envelope, never sends it.

DATE_ONLY is stored as the UTC date placeholder required by Migration 002;
its original date is retained in `source_date` and the status function uses
KST day start/end. EXACT_TIME keeps the explicit offset. UNKNOWN and mixed
recruitment precision stay on review hold. Missing optional event dates remain
NULL/UNKNOWN, so status automation never invents `ongoing` or `ended`.

## Actual Service category contract

The 2026-10-17 Production read-only lookup found active Service slugs
`conference`, `competition`, `hackathon`, `education`, `meetup`, `webinar`, and
`growth-program`. Agent metadata has an exact mapping for the first five.
Agent `program` is broader than `growth-program`, so it is not mapped;
`webinar` is not currently an Agent vertical. Exhibition, Research, Grant,
and Job also have no current Service slug. These remain on review hold;
no new public taxonomy is silently created. Preview currently has only the
active `preview-test` category, so an actual Preview registration E2E needs
temporary test-only category rows, followed by exact-ID cleanup.

The existing `register_opportunity_with_evidence` RPC already accepts a
valid Service `category_id` and nullable event dates. No Service schema or
public API migration is needed for the five existing Production slugs. The
isolated PostgreSQL test applies Service 002+004 and verifies date-only
registration, Competition without event dates, duplicate protection, atomic
rollback, and RPC grants. The current RPC does **not** atomically bind an
Agent Metadata approval to the Service insert; therefore the preview envelope
must not be used as an operational publishing authorization. A trusted
publish handoff requires a separately reviewed binding design.

## Reviewer Source scope: Migration 016

Migration 015 permits any active Metadata reviewer to approve or revoke any
Source. The proposed 016 migration adds a `source_id` FK to the existing
reviewer row, requires it whenever `active=true`, and checks the Source scope
when an immutable approval/revocation audit event is inserted. A rejected
audit insert rolls back the entire RPC. The 015 RPC signatures and grants stay
unchanged. An active unscoped legacy reviewer causes migration failure;
inactive Preview rows remain intact. A reviewer must be deactivated before
changing the Source scope. This supports one active Source per reviewer;
multiple Source grants would require a separate design.

Preview `qonlnoyppopyztulvgzv` received 016 once after an execution-time
approval. The executed SQL digest was
`b3e147c555519de32bc59fc15f6ae12c353ae4424778d63cf70a117696957b5c`.
Both Source-scope triggers and the CHECK exist; the existing reviewer remains
inactive. Source, approval, audit, Candidate, Opportunity, and queue row
fingerprints matched before and after. Do not reapply 014–016. Production 016 application,
Production Auth account creation, and Production reviewer grant each require
separate approval. An authenticated Production user must be registered through
Supabase Auth with a user-set hidden password; administrator-set email
confirmation must not be reported as proof of email ownership. Grant only the
selected Source ID, turn the grant OFF after the approved review, and preserve
the append-only audit.

The Preview Source-scope SDK E2E authenticated existing Auth UUID
`508fe790-2100-428d-bcfd-d781a3b9c620`, prepared two clearly synthetic
Sources, and temporarily scoped its inactive reviewer row to Source A after
separate execution approval. Source B approval was rejected with zero
approval rows. Source A approval and revocation both succeeded and repeated
decision IDs were idempotent. The exact Source A audit contains one APPROVED
and one REVOKED event; existing rows were unchanged, with zero queue and
Service writes. The reviewer row was returned to `active=false`, and the
authenticated RPC probe observed OFF. Synthetic Source/review/approval audit
records remain for traceability; they are not real Source permission.

## Preview synthetic Service registration E2E

On 2026-10-17, the Preview SDK submitted five isolated, category-specific
synthetic envelopes to Migration 004's atomic RPC using `service_role`. A
fresh in-memory Metadata Evidence replay yielded `VERIFIED`, `UNIQUE`, and
`PUBLISH_READY` before each read-only Service envelope preview. Conference,
Competition, Hackathon, Education, and Meetup each registered once;
repeating the same envelope returned `protected_existing`. SQL Editor
inspection found exactly five new Opportunity rows and five matching Time
Evidence Scope rows. All five recruitment deadlines were `DATE_ONLY`;
Competition alone had `UNKNOWN` event start/end, without invented dates.

The test then deleted only those five exact synthetic Opportunity IDs and
their exact synthetic Source/category IDs. The SDK's before/after row
snapshot matched for Service Opportunity, Source, Category, Agent Source,
Metadata Candidate, approval audit, and publish queue. SQL Editor confirmed
Opportunity 7 and Scope 4 after cleanup, with zero synthetic Scope rows.
No Agent publish queue item was created and no publish worker ran. This
exercise proves the Preview registration contract, not operational Metadata
publishing authorization or Production public API behavior.

The remaining Exhibition, Research, Grant, Program, and Job fixtures used
the live Preview duplicate snapshot in a read-only SDK check. All five
replayed `VERIFIED` with `UNIQUE` duplicates, but their Gate held at
`REVIEW_REQUIRED` and the Service envelope was ineligible because there is
no exact Service category mapping. They were not inserted into Agent or
Service tables.

Migration 017 is a separate proposed change to the 014 Metadata Candidate
READY constraint. It allows only the five exact Service slugs above, requires
the eight base evidence keys, and retains the event pair for all except
Competition. It does not change an RPC signature or GRANT and does not update
existing rows. Isolated PostgreSQL validation passed; Preview application
requires its own action-time approval and post-apply fingerprint check.

## Release and rollback

The code is based on deployed release `79d021c4b24abdd9d6e3afe35b05309112dbe7e9`,
which descends from current `origin/main` `1e6eddc80468f57875a516d5ddeba75559ba05f9`.
The changed Python files are copied by `Dockerfile.agent`; 016 and 017 are
separate SQL application artifacts, not image startup migrations. No UI,
Vercel, Service schema, Cloud Run configuration, Scheduler, or publish flag
change is part of this release. Old image + 016 is fail-closed because the
database guard applies to the existing RPC. Old image + 017 still emits only
Conference Metadata outcomes. New image + old 014 constraint will reject
newly READY Competition Candidates with optional event dates, so deploy 017
before the new image and verify the Preview path first. Production reviewer
grants must wait for 016. Roll back code by restoring the prior image digest;
keep 016 in place and set reviewer `active=false` rather than dropping the
security guard or audit. Leave Service Publish OFF throughout this stage.
