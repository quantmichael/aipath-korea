# Agent Collect (through PHASE 10 runtime validation)

PHASE 10A adds a separate Agent-only Cloud Run Jobs container foundation:
`Dockerfile.agent`, `requirements.agent.txt` and the one-shot
`python -m agent_collect.shadow_job` entrypoint. It calls the same
`AutomationRuntime.run_once()` as `automation-run --once` after explicitly
validating production/Supabase/Worker settings and both Publish flags OFF.
`defaults.toml` remains unchanged and disabled. The image build and Cloud Run
deployment are not yet verified or performed. See
[`docs/CLOUD_RUN_DEPLOYMENT.md`](../docs/CLOUD_RUN_DEPLOYMENT.md) for proposed
commands, Secret Manager mapping, exit policy and open checks.

PHASE 10 runtime update (2026-10-04): migration 008 is applied; the new
Discovery DB objects and restricted privileges, one guarded Tavily smoke call,
claim/lease/window and success reconciliation, four-worker dry run and global
lock were live-verified. Both smoke fixtures are disabled and all Automation
flags remain false. The 6h/24h failure-backoff stages and duplicate Query
guards were verified in rollback-only SQL Editor tests.
See `docs/DISCOVERY_SCHEDULING.md`; the earlier "008 unapplied" passage below
is historical.

PHASE 10 adds `discovery-due`, `discovery-worker --dry-run|--once`, and
Discovery as the first worker in `automation-run --once`. An Agent-only Query
Registry and atomic claim/lease/window contract are in migration 008, which is
**applied**. Its two initial Conference queries are disabled,
and all Automation/worker flags remain false by default. `automation-status`
supports optional bounded read-only `--live` diagnostics. Runtime validation
made one guarded Tavily call but no Source auto-approval or Service DB write. See
`docs/DISCOVERY_SCHEDULING.md`. The older 007/Processing status below is
historical; 007 is now applied and PHASE 8 real Processing E2E has completed.
The current offline suite passes 207/207 tests. Query registration/review is
explicit via `discovery-query-register` and `discovery-query-review`; neither
command changes Source approval status.

PHASE 9 adds `publish-due`, `publish-worker --dry-run|--once`,
`automation-status`, and `automation-run --dry-run|--once`. Production defaults
keep Automation, each worker flag, and Service publishing disabled. Migration
007 is **created only**, not applied; the live READY queue is empty and Service
INSERT remains unverified. See `docs/PUBLISH_WORKER.md` and
`docs/AUTOMATION_RUNTIME.md`.

Current status (2026-10-04): migrations 005 and 006 are applied. Private Supabase `agent-raw` Storage and a newly collected RawDocument passed cross-process SHA-256 readback. Processing DB claims/lease/failure/version checks passed; the single real Worker pass failed closed on `CROSS_RAW_CACHE` (same bytes already extracted from another RawDocument), and the daily LLM budget was 5/5. Candidate→Gate live E2E remains unverified. `AGENT_RAW_STORAGE_PROVIDER=supabase` selects persistent storage; `local` remains the default for development/tests. See `docs/RAW_STORAGE.md` and `docs/PROCESSING_WORKER.md`. Historical unapplied notes below describe earlier milestones, not current DB state.

Final PHASE 8 retry: `APP_ENV=development` selects Extraction Budget defaults 20/day and 300/month; production/unset/unknown retains 5/day and 100/month. The connected Supabase project is production, so the larger development profile was not used. A fresh AAAI registration-page RawDocument (`db9bcf87-0e5e-4a87-81c5-de94398748a0`) passed private Storage hash readback and new-fingerprint precheck, but production daily usage was 5/5. No new LLM call or ProcessingWorker pass was made for that document; Source was disabled. Live Candidate→Gate E2E is still pending.

`python3 -m agent_collect processing-due` previews bounded pending RawDocuments; `processing-worker --dry-run` performs no writes, while `processing-worker --once` runs one bounded pass. With local storage, pass `--raw-dir`; with private Supabase Storage, set `AGENT_RAW_STORAGE_PROVIDER=supabase` and `AGENT_RAW_STORAGE_BUCKET=agent-raw`. Neither command performs Service DB publishing. Production processing remains gated on a successful live Candidate→Gate E2E and budget availability.

PHASE 7.3C adds a one-shot `CollectionWorker` for approved ACTIVE known Sources, fixed `next_collection_at` scheduling, DB-level claim/lease RPC contract, bounded due reads, failure backoff, dry run and CLI. Agent-only migration 005 is **written but not applied**; DB-backed scheduling and live multi-worker behavior are not yet verified. No Service write, LLM call, or real HTTP request was made for this phase. See `docs/COLLECTION_SCHEDULING.md`.

Migration 005 security hardening removes caller time from production due/claim/completion RPCs, uses DB `clock_timestamp()`, derives scheduling windows deterministically from Source + due instant, and requires a canonical non-NULL window ID on scheduled Runs before the unique index. It remains **unapplied**; runtime validation has not restarted.

Completion reconciliation now treats a persisted `SUCCEEDED` scheduled Run as terminal, including when a late exception calls completion with a NULL Run ID. Agent-only completion records make success/failure completion idempotent; success scheduling is rebuilt from the Run finish time without HTTP. This remains offline-only until 005 is separately applied and verified.

Migration 005 lock-privilege hardening: scheduled Run INSERT/UPDATE guards now lock schedule rows inside `postgres`-owned `SECURITY DEFINER` trigger functions with fixed search paths. `service_role` retains schedule `SELECT` only; direct UPDATE/DELETE are not granted. This is an offline SQL change only—005 remains unapplied.

이 패키지는 기존 `collect/` 및 서비스 Backend와 분리된다. Source Registry, 범용 원문 수집, 수동 Discovery Runtime과 Conference 전용 구조화 Extraction을 제공한다. Extraction은 RawDocument 근거가 없는 값을 null/검토로 남기며 서비스 게시는 하지 않는다. PHASE 5.5부터 모델은 근거 문장을 작성하는 대신 결정적 Block/Link ID를 선택한다. 수집·발견·추출 계약은 각각 `docs/COLLECTION_ENGINE.md`, `docs/DISCOVERY_ENGINE.md`, `docs/EXTRACTION_ENGINE.md`에 있고 실제 전후 비교는 `docs/EVIDENCE_GROUNDING.md`에 있다.

PHASE 5.6은 코드·모델·Prompt 변경 없이 공식 Conference 5개 페이지의 고정 v2 품질을 측정했다. Core Precision 27/27, Recall 27/40, Evidence Pass 32/49이며 후보 5건 모두 `REVIEW_REQUIRED`다. 수동 Ground Truth, Source Missing/Extraction Miss 분리, 20-field matrix, 토큰·원문 저장 현황과 PHASE 6 준비 판단은 `docs/EXTRACTION_BENCHMARK.md`를 따른다.

PHASE 6은 수동 `QualityGateRunner`로 검증된 추출값 기반 Dedup→Verification→Publish Gate를 추가했다. 실제 5개 후보 최종 결과는 `UNIQUE` 4, `POSSIBLE_DUPLICATE` 1(같은 원문의 과거 추출 버전), `REVIEW_REQUIRED` 5, `PUBLISH_READY` 0이며 Agent 검증 행만 기록하고 Service DB는 중복 확인 SELECT만 했다. 실제 게시·새 LLM 호출은 없다. 정책과 smoke 이력은 `docs/QUALITY_GATE.md`를 따른다.

PHASE 7은 Service 게시 경계의 Dry Run, 날짜-only 보류, timezone 근거 구분, 결정적 공개 상태, Source/Category/Tag 매핑, 최종 충돌 검사, Agent queue claim·결과·audit과 Supabase Adapter를 구현했다. `AGENT_PUBLISH_ENABLED`는 기본 false다. 실제 5개 후보는 모두 Gate REVIEW/Queue 없음/Source 매핑 없음이라 Service INSERT 0건이었다. 계약·제약·실DB 사전 평가는 `docs/SERVICE_PUBLISH.md`를 따른다.

PHASE 7.3A는 검증된 추출값만 받아 날짜·시각·시간대 근거를 결정적으로 정규화하고 Agent Candidate에 감사 정보와 함께 저장하는 **명시적 수동 경계**를 추가했다. 범위가 확실한 날짜 범위와 근거 ID 오분류만 보수적으로 보정했다. 기존 5건은 읽기 전용 재평가만 했으며, DB 소급 변경·Service 게시·LLM 호출은 하지 않았다. 계약은 `docs/NORMALIZATION_ENGINE.md`를 따른다.

PHASE 7.3B는 사람의 Source 검토→승인→활성화 계약과 Agent 전용 감사 migration 004를 추가했다. 004는 2026-10-04 실DB에 적용·검증했으며 기존 5개 Source는 변경하지 않았다. `activate()` 및 `register-source --activate` 우회는 차단했다. 사용·운영 조건은 `docs/SOURCE_APPROVAL.md`를 따른다.

PHASE 7.3B 보안 보강으로 004의 신규 Source INSERT 가드와 감사 테이블 쓰기 권한을 강화했다. 새 Source는 비활성 CANDIDATE만 허용하며 리뷰 메타데이터 선주입은 거부한다. 기존 행의 일반 갱신은 별도 UPDATE로 처리하고, lifecycle 전환과 감사 기록은 권한이 제한된 단일 RPC에 묶는다. 실DB 역할별 차단·전환·감사 체인 검증은 테스트 Source 1건으로 완료했다.

현재 파일 구조:

```text
schemas/             domain models, validation, status
workflows/           AgentCollectState, AgentCollectNode
adapters/service_db/ ServiceDBAdapter, Dry Run, mapping/status/conflict/Supabase I/O
config/              defaults.toml, environment overrides
migrations/          001–006 applied; live Candidate→Gate processing still unverified
scheduling/          due/claim/lease policy, one-shot worker, Agent-only Supabase RPC port
discovery/           SearchProvider, Fake/Tavily Provider, Budget Guard, filter, evaluator, Promotion Gate, Runner
extraction/          Conference schema, numbered evidence blocks/links, Fake/OpenAI Provider, deterministic validator, budget, runner
normalization/       validated extraction → temporal payload + provenance, explicit runner
dedup/               URL/title/date 기반 보수적 중복 판정
verification/        필수 검증값·근거·provenance·Source Trust 검사
quality_gate/        결정적 PublishGate와 수동 QualityGateRunner
repositories/         interface, Supabase and in-memory implementations
registry/             URL canonicalization and source management
registry/approval.py  manual human approval/activation state contract
collectors/           common contract, planner, safe HTTP, HTML/RSS/API, runner
storage/              raw storage interface and local files
processing/           versioned processing policy, one-shot worker, DB RPC port
```

`agents/`, `classification/`, `observability/`는 후속 작업에서 실제 코드가 필요할 때 추가한다. PHASE 7.3A 정규화는 `AgentRuntime.normalization_runner().run(candidate_id)`로 명시적으로 호출한다. PHASE 7.3B 승인에는 `AgentRuntime.source_approval_service()`의 Python API를 사용한다. migration 004는 실DB에 적용됐다. 기존 후보 상태는 변경하지 않았다.

PHASE 3.5에서 `runtime.py`가 Agent 전용 Supabase Repository를 조립하고 `python3 -m agent_collect` CLI를 추가했다. `config-check`, `register-source`, `collect-source`, `show-run`, `show-document`를 제공한다. DB 명령은 Agent migration과 서버 자격증명·프로젝트 의존성 설치가 필요하다. 수집 명령은 영속적인 `--raw-dir`를 필수로 받는다. 실제 연결 감사·안전 검토·실행 방법과 미검증 항목은 `docs/RUNTIME_INTEGRATION.md`에 기록했다.

테스트: 저장소 루트에서 `python3 -m unittest discover -s tests -v`. Fake Provider 및 Tavily mock 테스트는 네트워크를 사용하지 않는다.

비밀키는 TOML에 두지 않는다. `AGENT_TAVILY_API_KEY`와 `OPENAI_API_KEY`는 환경에서만 주입한다. `AGENT_*` 환경변수로 정책을 덮어쓸 수 있으며 기본 feature flag는 모두 꺼져 있다. Agent DB migration 001/002/003/004는 적용됐고 005는 미적용이다. 동일 ICCAI RawDocument에 v1과 v2를 각각 한 번씩 실제 추출했으며, v1 검증 필드 0개에서 Grounded Evidence v2는 9개로 개선됐다. 두 후보 모두 `REVIEW_REQUIRED`이고 v1 기록을 덮어쓰지 않았다. 기본 추출 예산은 일 5·월 100, 입력 12,000자·근거 240자·출력 1,600토큰, Asia/Seoul 기준이다. 다중 워커의 원자적 예산 상한은 보장되지 않는다. 생산용 Scheduler·Daemon은 없고, Service DB 게시 Adapter는 구현됐으나 실제 게시 E2E는 적격 후보 부재로 미검증이다.
