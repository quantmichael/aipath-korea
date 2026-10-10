# Conference Discovery scheduling — PHASE 10

Migration `008_discovery_scheduling.sql` was applied to the Supabase production
project on 2026-10-04. Its SQL Editor transaction reported success. An
unintended second Run attempt was rejected at the first `CREATE TABLE` with
`42P07` (existing relation); the second transaction did not reapply the
migration. Actual catalog and service-role runtime checks are recorded below.
Historical manual Discovery remains available. Applying 008 did **not** enable
approved queries, Worker flags, or unattended Automation.

## Live runtime validation — 2026-10-04

- Catalog: two new RLS-enabled tables, three expected indexes, six
  `postgres`-owned SECURITY DEFINER functions with `search_path=pg_catalog`,
  and one scheduled-query validation trigger. `service_role` has schedule
  SELECT and claim RPC EXECUTE, but direct UPDATE/DELETE returned `42501`;
  anon/authenticated/PUBLIC claim EXECUTE was absent.
- Both seed queries remained disabled and due count was zero. The separately
  reviewed `runtime_discovery_scheduling_smoke_test` Query was enabled only
  for validation and ended disabled with no claim. Independent DB clients
  contended for one claim: exactly one succeeded; a live lease denied another
  worker; expiry allowed reclaim of the same canonical window UUID.
- The real DiscoveryWorker made **one** guarded Tavily call. Its scheduled
  Query `SUCCEEDED` with five SearchResults and three SourceCandidates;
  zero Sources were promoted. The next run moved 24 hours from DB completion
  time. Late NULL-query-ID failure and duplicate success completion both
  returned the terminal success without schedule drift; the window was not
  claimable again. The same query then returned `QUERY_COOLDOWN` on a read-only
  Guard check.
- Discovery and four-worker Automation dry runs showed due previews with no
  claims or Agent data change. Global Automation lease acquisition, competing
  denial, release and expiry recovery succeeded; final lease was clear.
  All production flags remained false. Existing five HOLD Sources remained
  inactive; Service `opportunities` count was 17 before and after.
- A separate disabled Agent-only failure fixture verified first failure
  backoff of exactly 60 minutes and duplicate failure completion idempotency.
  After the Mac was unlocked, a rollback-only SQL Editor test on that fixture
  verified the second and third delays as exactly 360 and 1440 minutes. A
  second rollback-only test inserted one valid scheduled Query, then confirmed
  the same schedule/window duplicate was rejected by the unique index and
  wrong worker, claim, and window were rejected by the DB trigger. Both tests
  deliberately raised `EXPECTED_ROLLBACK_ONLY` at the end; readback confirmed
  the fixture remained disabled with its original one failure and no Query
  rows. The offline tests passed 207/207. PHASE 10 Discovery scheduling
  runtime validation is complete; unattended automation remains disabled.

## Query Registry and schedule

`agent_discovery_schedules` is an Agent-only registry keyed by normalized query,
Conference vertical, language, and country. It stores interval, next run,
last run/success/failure, failure count, enabled state, review identity/reason,
and current claim. Two sample Korea queries are seeded **disabled**. Registration
or enablement is not inferred from search results. The reviewed enable/disable
RPC records reviewer, reason, and time; a separate reviewed registration RPC
creates only disabled queries. CLI commands `discovery-query-register` and
`discovery-query-review --enable|--disable` expose these human decisions.
Direct `service_role` table mutation is not granted. Initial interval is 24
hours, matching the existing same-query
24-hour cooldown. Default max jobs is one query per Worker invocation.

Due selection is bounded and uses PostgreSQL `clock_timestamp()`:
`enabled AND vertical='conference' AND next_run_at<=DB_NOW` with no active
lease. Application/local time never makes a query due. Atomic claim is a
postgres-owned, fixed-search-path RPC; its transaction advisory lock also
serializes scheduled searches across distinct queries because the existing
SearchBudgetGuard is a usage-ledger check, not a token reservation. An active
lease denies a second claim. A crash can be retried after lease expiry. The
lease defaults to 120 seconds and must exceed the configured Tavily timeout;
operators should raise it if provider+DB processing can exceed that envelope.

The window UUID is deterministic from schedule ID and UTC due timestamp in
microseconds. `agent_discovery_windows` records claim/terminal outcome and a
partial unique index prevents two scheduled `agent_discovery_queries` for one
schedule/window. A scheduled query INSERT/UPDATE must match the live claim,
worker, window, Tavily provider, Conference vertical, and registered text.
Once a query is persisted for a window, an expired lease reconciles its outcome
without a second Tavily call. Persisted success is terminal; late failure
cannot override it. A crash before any query row may reclaim the **same**
window and perform one search. This is DB/window idempotency, not a guarantee
of exactly-once network delivery in every external failure scenario.

## Budget, failure, and human boundary

`DiscoveryWorker` calls the existing `DiscoveryRunner`, which consults
`SearchBudgetGuard` before Tavily. Configured limits are 30/day, 1000/month,
24-hour same-query cooldown, monthly WARNING at 800 and CRITICAL at 950;
the hard stop is at the daily/monthly limit. Disabled provider or absent key
also fails closed. Budget block records `BUDGET_BLOCKED`, makes zero Tavily
calls, and delays the query 24 hours. Provider/processing failure backs off
1 hour, 6 hours, then 24 hours (configurable). A successful query schedules
from its DB completion time plus its query interval. The Worker does not
invent/modify Source approval. Promotion Gate may register only an inactive
`CANDIDATE`; a human still controls REVIEW → APPROVED → ACTIVE.

`discovery-worker --dry-run` previews due queries, interval, next time, and
budget decision. It makes no claim, Tavily call, SearchResult/Candidate row,
or schedule/log write. `discovery-due` is bounded. `discovery-worker --once`
executes one bounded pass. These DB-backed commands require applied 008;
runtime validation used a temporary reviewed Query, while production defaults
remain disabled.

## Automation integration

One-shot `automation-run --once` orders Discovery → Collection → Processing →
Publish. Queues are independent; a Discovery failure does not block later
enabled workers. The existing global Agent DB lease protects the cycle.
`automation_enabled`, `discovery_worker_enabled`, and all other worker flags
default false; Discovery additionally requires `discovery_enabled` and a
working Tavily credential, and Service publishing still has its own flag and
eligibility gate. `automation-run --dry-run` skips the global lease and logs;
all enabled worker previews must be read-only. No daemon or platform scheduler
is installed. An external scheduler may invoke the one-shot CLI after a
separate operational rollout.

`automation-status` prints safe flags/budget policy offline. `--live` performs
bounded read-only due previews (up to 100 per queue), Discovery budget usage,
latest success/failure, and global lease snapshot; unavailable DB objects are
marked unavailable rather than presented as zero. Neither command prints keys.
`DISCOVERY_WORKER_RUN` and `AUTOMATION_RUN` Agent logs contain IDs, timestamps,
counts, and isolated errors. No Service DB write is caused by Discovery.

## Runtime rollout gates and backlog

Before enabling unattended operation: explicitly review/enable a small query
set, confirm budget and cooldown usage, choose an external one-shot scheduler,
and enable the desired flags. Migration 008 and its DB runtime contracts were
verified, but applying 008 alone did not enable any search. The single guarded
Tavily smoke call was a controlled manual Worker invocation.

Non-blocking backlog retained: Agent-only HELD publish fixture cleanup,
Service publish completion ID strengthening, CROSS_RAW_CACHE reuse, raw
retention cleanup, atomic LLM budget reservation, admin review UI,
replacement LLM benchmark, and extraction recall tuning. Manual Discovery
can race scheduled Discovery against the shared non-reserving search budget;
operators should avoid parallel manual searches until atomic search-budget
reservation is designed.
