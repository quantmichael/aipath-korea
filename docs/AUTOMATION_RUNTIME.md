# PHASE 10 — Automation Control Plane (008 applied)

`AutomationRuntime.run_once()` coordinates DiscoveryWorker, CollectionWorker,
ProcessingWorker, and PublishWorker in that order. The queues remain
independent; a failure in one worker is recorded by exception class and does
not hide or prevent due work in later enabled workers. This is not a daemon,
OS scheduler, or LangGraph graph. Discovery may create only inactive Source
Candidates; human approval remains required before Collection.

Production-safe defaults are `automation_enabled=false` and all four worker
flags false. Discovery additionally requires `discovery_enabled=true`, the
existing budget/cooldown guard, and Tavily credentials; publishing additionally
requires `publish_enabled=true`. `automation-status` prints effective flags
without connecting to DB; `--live` requests bounded read-only queue/budget,
Discovery last-result, and global-lease snapshots.
The global kill switch returns `DISABLED` without constructing or invoking a
worker. `automation-run --dry-run` calls only enabled workers' dry-run paths,
without an Automation lease or log write. `automation-run --once` acquires the
Agent DB `automation` lease before any worker and releases it in `finally`;
a live lease returns `LOCKED`. The DB server clock controls its 3600-second
default expiry. There is no lease heartbeat, so runtime duration must remain
below that bound or the operator must increase it. A crashed process can be
replaced after lease expiry.

An `AUTOMATION_RUN` Agent log includes run/worker ID, start/end, status,
worker counts, and isolated errors. Each worker also retains its own summary
log and provenance. No Service write occurs unless both Automation and
Publish worker flags are enabled, the Adapter publish flag is enabled,
migration 007 is installed, and a naturally eligible queue item survives
all final checks. No automatic human approval or REVIEW_REQUIRED promotion
exists. `automation-run --once` is a one-shot external-scheduler entrypoint;
no platform scheduler is deployed here.

Migration 007's Agent-only publish/automation leases were applied and
runtime-verified in PHASE 9. Migration 008 was applied on 2026-10-04. A
DB-backed four-worker Automation **dry run** completed in Discovery →
Collection → Processing → Publish order with no claims or external calls.
The global lock was verified with two independent clients and finally
released. Unattended real four-worker Automation remains disabled and has not
run. See `DISCOVERY_SCHEDULING.md`. The Service schema is untouched.

Production 운영 전환은 별도 결정이다. 10분 one-shot tick, 외부 실행 환경,
secret·backup·alert, 단계별 enable, 첫 자연 READY의 통제 게시 및 GO/NO-GO
체크리스트는 [PRODUCTION_OPERATIONS.md](PRODUCTION_OPERATIONS.md)와
[PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md)에 정의한다. 현재 어떤
external scheduler도 배포되지 않았고 기본 flag는 모두 false다.
