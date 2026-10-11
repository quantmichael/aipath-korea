# PHASE 12A — Autonomous Discovery planning (offline)

## Baseline and safety boundary

The current Cloud Run Job runs every 30 minutes, but Discovery executes only a
due, enabled, reviewed schedule. Production's active query is `2027 AI
conference Korea official call for papers` (daily); each provider request asks
for five results. This phase does not alter that schedule, call Tavily/OpenAI,
or enable auto approval or service publishing.

`AgentRuntime.discovery_plan_preview(now=...)` reads all existing schedules and
the persisted Tavily query ledger, then returns a dry-run `SearchPlan`. It makes
no network search and performs no DB mutation. A separate, explicit
`SupabaseDiscoverySchedules.record_plan(plan)` method can write an
`agent_logs/DISCOVERY_PLAN` audit row when a later reviewed integration enables
plan recording. It is not called by the runtime preview path.

## Planning policy

- Compute the year and daily/monthly windows in `Asia/Seoul`, including the
  December 31/January 1 boundary. The catalog contains Korean and English
  variants for the current year, next year and evergreen for each of eight
  categories: conference, exhibition, competition, hackathon, education,
  research, grant and program.
- Select at most three proposals per KST day by deterministic category
  rotation: one current-year, one next-year and one evergreen query. This
  spreads chances across categories without generating random or unlimited
  searches. All proposals remain `DRY_RUN`.
- Compare normalized query text with **all** existing schedules, including
  disabled ones, and with attempted Tavily queries in the last 24 hours.
  Count duplicate and cooldown skips separately. Never overwrite an existing
  schedule.
- Read the persisted attempt ledger; cap proposals by the existing 30/day and
  1,000/month policy. If the ledger read fails, planning fails closed. A plan
  consumes no search credits and reserves no credits. The existing execution
  budget guard remains authoritative when a reviewed query actually runs.
- A `DISCOVERY_PLAN` audit payload distinguishes `planned_query_count` from
  `executed_query_count=0`, with category, year, template and rendered text for
  each proposal. Existing Discovery worker summaries now include
  `planned_query_count=0` and an executed query count derived from query IDs.
  Discovery result and candidate counts remain attached to actual queries.

## Exhibition and schema

`exhibition` and `program` are added to the Agent `Vertical` model and Discovery
schedule checks. Migration `010_discovery_exhibition_program.sql` is a **draft**:
it extends only the `agent_discovery_schedules.vertical` and
`agent_discovery_queries.vertical_hint` CHECK constraints. It does not change
existing rows, enable schedules, or touch Service tables. It depends on Agent
Migrations 001–009 and was tested only in isolated PostgreSQL.

Exhibition evaluation recognizes `exhibition`, `expo`, `trade show`, `전시회`,
`산업전`, `박람회`, `엑스포`, `피지컬 AI` and `스마트팩토리`. These are relevance signals,
not proof of officiality, collection permission, registration status or dates.
Existing Source Approval and collection permission gates remain in force.
Existing Processing/Verification are conference-oriented and would require a
separate quality review before exhibition material could be published.

## Result limit and deduplication

The request limit stays at five. Increasing to ten could expose lower ranked
official results, but would also store/evaluate up to twice as many result rows
per query and may increase irrelevant or repeated candidates. Provider billing
and live recall differences were **not measured**; no Tavily call was made.
Changing the query mix first addresses the observed conference-only coverage
gap while keeping request counts at up to three planned queries per day.

Existing `CandidateFilter` rejects duplicate canonical URLs and already known
Sources/candidates; `PromotionGate` avoids multiple registered Sources from one
domain in one run. Different URLs for the same event still require the existing
downstream opportunity deduplication; this phase does not claim cross-URL event
identity at the search-result stage.

## THE NEXT AI fixture

An offline fake search hit for `https://nextaikorea.com/en` with the title
`2026 THE NEXT AI 피지컬 AI & 스마트팩토리 산업전` reaches a Source candidate under
the new Exhibition category. Without configured officiality evidence, it stays
`REVIEW_REQUIRED`; no Source is activated or collected. A configured domain hint
can create an inactive Source candidate, still without bypassing approval.
This validates pipeline handling **if** a provider returns the URL. Actual
Tavily recall has not been measured.

## Release boundary

The planner currently produces read-only proposals. It does not persist them
as schedules or execute them. Production use requires a separately reviewed
plan activation path with atomic cross-schedule search-credit reservation or
equivalent concurrency control, permission/approval policy, and Migration 010.
The current budget guard counts persisted attempts but its read and provider
call are not an atomic reservation across independent runners. Do not enable
autonomous provider calls on this foundation alone.
