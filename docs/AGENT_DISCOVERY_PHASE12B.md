# PHASE 12B: Goal-driven Discovery foundation

Status: offline implementation only. Agent Migration 011 is a draft and has not
been applied to Production or Preview. No live OpenAI/Tavily call was made.

## Execution boundary

`AGENT_AI_DISCOVERY_PLANNER_ENABLED` and `AGENT_AI_DISCOVERY_EXECUTION_ENABLED`
default to false. With both false, the existing scheduled Discovery and
Extraction paths retain their previous budget behavior and do not require
Migration 011. Execution cannot be enabled without planning.

With planning enabled, the automation lease runs `ai_plan` before the existing
Discovery worker. It builds bounded read-only context from Agent tables,
reserves one shared OpenAI credit, stores a plan attempt, and requests a strict
JSON plan using the configured extraction model (production policy:
`gpt-5.4-nano`). Invalid output is held. PHASE 12A's deterministic policy
screens duplicates, cooldown, budget, and category diversity. This preview
does not authorize a provider call.

If execution is also enabled, `ai_execute` can claim one saved item **at least
30 minutes after** plan completion. It calls the existing DiscoveryRunner,
which performs the final PostgreSQL Tavily reservation before the HTTP request.
The same reservation adapter is used by legacy scheduled/manual Tavily and by
OpenAI Extraction whenever either AI Discovery flag is enabled. Existing
Source Approval, Auto Approval, Collection, Processing, and Service Publish
gates are unchanged. New categories map to `unknown` for Discovery evidence;
they are not a collection or publication approval.

## Budget and failure policy

Migration 011 adds one provider-reservation ledger, AI plan audit rows, and
plan-query claims. A transaction-scoped advisory lock serializes reservations.
Limits are DB-authoritative: Tavily 30/KST day, 1,000/KST month, 24-hour
normalized-query cooldown; shared OpenAI 5/KST day and 100/KST month;
Planner at most 1/KST day and 25/KST month. Legacy `agent_discovery_queries`
and `EXTRACTION_LLM_ATTEMPT` logs remain counted without a backfill.

A reservation is counted before dispatch. Dispatch is allowed only once,
within 60 seconds, and on the same KST day. A missing reservation, dispatch
acknowledgement, audit write, or database read prevents the external call.
Dispatched timeout/connection failures are marked `UNKNOWN` and remain counted
because provider billing cannot be proven absent. Other failures remain
counted. Same-key requests do not repeat a call. A crashed/claimed plan query
stays held for manual reconciliation; it is never automatically redispatched.
No budget credit is released automatically. Search plans with no safe queries
are held. A failed LLM plan may yield a separate PHASE 12A DRY_RUN preview;
that preview does not run Tavily.

## Production activation gate

1. Preserve PHASE 12A worktree edits and integrate them with Migration 010
   before Migration 011; use a clean release worktree because the primary
   checkout has overlapping uncommitted files.
2. Review the exact SQL, test Migration 001-011 and RPC/RLS in Preview, then
   apply 010 and 011 only in an authorized release. Do not deploy the enabled
   flags before the migration.
3. Ensure all Cloud Run workers use the new shared reservation path before
   enabling AI planning; a mixed old/new image can race the legacy budget
   check. Keep both AI flags OFF during the rollout, and observe scheduled
   Source/Extraction regression and budget ledger agreement.
4. Validate real Supabase SDK RPC shape, OpenAI strict-output compatibility,
   Tavily provider behavior, 30-minute delay, and audit correlation in Preview
   with mock providers. Observe a shadow cycle with Auto Approval and Service
   Publish OFF. Enable planning and execution only under separate operational
   authorization, with an explicit stop/rollback plan.

The local PostgreSQL test uses only an ephemeral cluster and intentionally
generated data. It is not evidence that Migration 011 is applied or that live
providers work in Production.
