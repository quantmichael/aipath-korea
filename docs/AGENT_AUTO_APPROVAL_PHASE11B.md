# PHASE 11B — Discovery coverage and private collection validation

Status: code and offline tests only. No operational API calls, DB writes, deployment, schedule registration, or automatic approval enablement.

## Discovery boundary

`DISCOVERY_QUERY_TEMPLATES` proposes English and Korean reviewed searches for conference, competition, hackathon, education, research, grant, and recruitment. They are inert until a human registers and enables each schedule. The PHASE 11B migration `agent_collect/migrations/009_discovery_coverage.sql` is **not applied**. It extends the existing scheduled-query CHECK and RPC contract to those seven verticals while preserving the 24-hour cooldown, disabled registration default, claim advisory lock, reviewer fields, service-role-only RPC, RLS, and existing Conference registration RPC. Candidate provenance records the query vertical and explicitly marks it as non-approval evidence.

Discovery still records safe, canonical, previously unknown URLs as candidates outside every approval scope. Preliminary title/snippet matching covers education, contests, research programs, government grants, and recruitment; a search hit is never collection permission.

## Trust and activation

Automatic approval remains OFF by default. When separately enabled, a reviewed scope must identify an exact HTTPS origin and directory, a human-reviewed officiality document and explicit collection-license document with pinned hashes and a maximum 90-day lifetime. Each assessment checks canonical identity, live robots policy, unchanged officiality and terms, and the candidate page. Robots alone never grants permission. Assessment failure leaves the candidate in review (or rejected if irrelevant); a later activation failure leaves a promoted candidate linked to a held Source. Both carry machine-readable reasons. The machine reviewer is `AIPATH_AUTO_APPROVAL_V1`, not a human identity.

The existing Source review RPC remains the transition authority. Runtime now confirms the activation trigger created a matching `agent_source_schedules` row; a missing schedule holds the Source. A second approval request for an already completed matching machine approval returns the current ACTIVE Source rather than demoting it. Existing manually approved Sources use their existing path. The canonical Source URL has a DB unique constraint; Source Candidate URLs also have a DB unique constraint.

For every collection attempt from an automatically approved Source, the page, robots, reviewed scope, officiality hash, and license hash are checked again. A changed or expired permission fails before HTTP collection or raw storage. A revocation between the check and the subsequent fetch remains a time-of-check/time-of-use limitation; the next attempt blocks.

## Observability

The Discovery worker summary adds `discovered_count` (search results), `candidate_count`, `auto_approved_count` (completed audited transitions), `review_required_count`, `rejected_count`, `collection_scheduled_count` (activation plus verified schedule row), and classified `approval_failure_reasons`. The automation summary also records those names plus `collection_success_count` from the Collection worker. Existing `success_count` and other worker fields remain. Failed permission probes are classified as `permission_check_failed`; other assessment failures use the assessor reason code; activation failures use `activation_failed:<exception type>`.

## Verification boundary

Offline tests use a fake search provider and fake HTTP transport, in-memory Source review repository, simulated schedule trigger, CollectionWorker, CollectionRunner, and local private raw storage. They cover seven verticals, out-of-scope candidates, reviewed approval and provenance, schedule confirmation, raw storage, permission withdrawal, duplicate activation, concurrent calls, and fail-closed errors. These tests do **not** establish Supabase RPC, trigger, RLS, privilege, or concurrent transaction behavior. `tests/sql/agent_discovery_coverage_integration.sql` is a rollback-only nonproduction RPC/trigger/RLS/privilege test, prepared but **not executed**. No isolated PostgreSQL executable was found locally; Preview Agent DB access was not established. Migration 009 must be applied and tested in a separate nonproduction Agent database before operational use. Existing production settings and Sources were not changed.
