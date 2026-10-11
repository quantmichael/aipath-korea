# PHASE 11A — Scoped automatic Source approval (offline only)

This change is **disabled by default**. It does not alter the two existing
production Sources, run a production query, write to Supabase, change Cloud Run,
or enable Service Publish. No migration is required: the existing audited
`agent_review_source` RPC remains the sole Source lifecycle writer.

## Current boundary

Scheduled Discovery claims one enabled Conference query, calls Tavily within
its budget, filters URLs and stores SearchResults and SourceCandidates. The
rule evaluator reads search text and optional official-domain hints. Its
`ACCEPT` can register an **inactive CANDIDATE** Source, but never approves or
activates it. Collection requires `ACTIVE` plus a matching approval snapshot.
The three manual transitions (`REVIEW_STARTED`, `APPROVED`, `ACTIVATED`) and
their immutable DB events remain supported.

## Automatic policy

`AGENT_DISCOVERY_AUTO_APPROVAL_ENABLED` defaults to `false`. Enabling it also
requires a nonempty `AGENT_DISCOVERY_AUTO_APPROVAL_SCOPES_JSON` manifest.
Each scope has an exact HTTPS origin, directory path prefix, human scope
reviewer, issue and expiry times (at most 90 days), an officiality evidence
URL on the same origin plus its pinned SHA-256, and an explicit-licence page
URL plus its pinned SHA-256. The `permission_basis` must be `explicit_license`. A person must
review that document's meaning before preparing a scope; a matching hash is
only change detection, **not** a legal interpretation. No production scope is
included in this repository.

For each new result in scope, the application requires AI and Conference
signals, a canonical URL match, live `robots.txt` permission for its own user
agent, the exact officiality and licence document hashes, and a reachable HTML page whose visible
text is relevant. All checks use the existing bounded, DNS-pinned HTTP
transport and reject redirects, missing documents, changed terms, expired
scopes, overlapping scopes and errors. Search-provider "official" hints alone
never grant automatic approval. An out-of-scope or failed candidate remains
`REVIEW_REQUIRED` with a reason in Candidate provenance.

An eligible new Source is registered as inactive `CANDIDATE`; the existing
review RPC then records review, approval and activation with the explicit
machine reviewer `AIPATH_AUTO_APPROVAL_V1`. The audit reason records the scope,
its human reviewer, terms URL and hash, and private-collection limitation.
The Source and Candidate retain policy evidence in provenance. A failure
during transition moves a Source under this machine decision to `HOLD`; a
failure that leaves it collectible fails the Discovery run for operator
attention. Existing Sources are filtered by canonical identity and never
reapproved by this path. The DB unique URL constraint closes concurrent
duplicate registration races.

Each later collection of an automatically approved Source rechecks the live
robots document, pinned licence page, source page and scope expiry. Turning the
feature OFF or losing permission blocks collection. Human-approved Sources
continue through their unchanged collection path. This feature does not
approve Agent Candidates for publication and does not change the Publish Gate.

## Remaining deployment work

Before a separate production activation, a reviewer must create and validate
actual narrow scopes with explicit machine-collection permission. The current
two Sources must not be silently converted to automatic approvals. The runtime
needs a dry run against a nonproduction Supabase project to verify the existing
RPC, triggers, audit events and collection scheduling with a machine reviewer.
If a site's licence is ambiguous or restrictive, keep it in manual review.
Terms can change between the final check and a fetch; per-collection rechecks
reduce but cannot eliminate that interval. The Source Approval RPC records a
reviewer string but does not authenticate the human who prepared a scope, so
deployment configuration and service-role credentials must remain restricted.
