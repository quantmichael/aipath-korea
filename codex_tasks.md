# AIPATH Agent Collect — Codex Implementation Tasks

## PHASE 10A — Cloud Run container foundation (2026-10-11)

Agent-only `Dockerfile.agent`, `.dockerignore`, pinned direct Agent dependencies,
and `agent_collect.shadow_job` one-shot entrypoint were added. The entrypoint
requires explicit production/Supabase/Worker settings, fails closed unless both
Publish flags are explicitly false, checks credential presence before DB access,
and returns non-zero for Worker failure counts that the manual CLI can report
with exit 0. JSON stdout includes run/worker status and counts without secrets.
`docs/CLOUD_RUN_DEPLOYMENT.md` contains proposed Project/Job/Registry/Secret/IAM
configuration and **unexecuted** deployment commands. Defaults and existing
Automation/Backend/Frontend behavior remain unchanged. Local Docker/gcloud are
unavailable, so image build, container smoke, Cloud API state and Cloud Run
execution are not verified. PHASE 10B deployment/Scheduler are not started.
Historical status notes below predate this container preparation.

## Production Readiness & Operations Design — 2026-10-04

PHASE 1–10 Foundation과 각 단계의 지정된 Runtime Validation은 완료됐다.
Migration 001–008 적용, PHASE 10 Discovery DB/Tavily 검증, private Raw
Storage·Processing E2E, 207/207 offline tests를 확인했다. **그러나 전체 무인
Production 운영은 NO-GO**다. 자연 PUBLISH_READY가 없어 Service INSERT →
readback → Backend API → Frontend 표시 E2E는 미검증이고, 외부 Scheduler,
Production secret/backup/alert, Candidate review 운영, legacy Service write
ownership도 미완료/미확인이다. 운영 설계와 단계별 GO gate는
`docs/PRODUCTION_OPERATIONS.md`, `docs/PRODUCTION_CHECKLIST.md`를 따른다.
이번 작업은 문서만 작성했고 코드·migration·DB·Worker flag·외부 Provider를
변경하지 않았다. 아래 PHASE별 상태 문구 중 오래된 "미적용/미검증" 표시는
작성 당시 기록이며 위 최신 상태로 대체한다.

## Current status — PHASE 10 runtime validation (2026-10-04)

PHASE 10 runtime update: migration 008 was applied once in Supabase SQL Editor
and catalog/RLS/privileges verified. A second unintended Run attempt was
rejected by existing-table error `42P07` and did not reapply. The two seed
queries remain disabled. One reviewed smoke Query produced one guarded Tavily
call, five SearchResults, three SourceCandidates, no Source promotion, a
successful 24h next schedule, atomic claim/lease recovery and terminal-success
reconciliation. Four-worker DB-backed dry run and global lock passed; both
test Query fixtures ended disabled. Failure backoff at 60m, 360m and 1440m,
duplicate failure idempotency, and DB-level duplicate/mismatched Query rejection
passed; later-stage checks used rollback-only SQL. Service
opportunities remained 17, LLM/HTTP Collection/Service writes zero, and
207/207 tests pass. Automation flags remain off. See
`docs/DISCOVERY_SCHEDULING.md`. Historical "008 unapplied" text below is
superseded by this update.

Conference Discovery Query Registry, DB-server-time due/atomic claim/lease,
deterministic window and terminal-outcome contract, bounded one-shot
DiscoveryWorker, and four-worker Automation ordering have been implemented.
The existing Tavily SearchBudgetGuard/cooldown and human Source Approval
boundary are reused. Production defaults remain disabled. Agent-only
`008_discovery_scheduling.sql` was subsequently applied and Discovery DB
behavior was runtime-verified; unattended four-worker execution remains
disabled and untested. One guarded Tavily call, but no OpenAI or Service DB
write, occurred during runtime validation. See
`docs/DISCOVERY_SCHEDULING.md`. The offline suite passes 207/207 tests.
The PHASE 9 `007` migration was applied and
runtime-verified before this phase; older "007 pending" notes below are
historical. Natural PUBLISH_READY remains zero and Service Publish E2E is open.

## Current status — PHASE 9 foundation (2026-10-04)

PHASE 8 live Processing E2E later completed for the private AAAI RawDocument:
one budget-guarded OpenAI call, Candidate → deterministic normalization →
Dedup UNIQUE → Verification/Gate REVIEW_REQUIRED. Same-version reprocessing
was blocked, Source DISABLED, Service writes zero, 180 tests passed. A
one-process 5→6 daily limit override was explicitly approved; audit attempts
were not reset and production defaults remain 5/100.

PHASE 9 now adds a claimed Publish Worker, fail-closed Service Adapter
preclaim path, one-shot Automation Runtime, disabled-by-default controls,
offline tests, and Agent-only migration 007. 007 is **created but unapplied**.
The complete offline regression suite is 196/196 passing; 007 SQL parses as
40 PostgreSQL statements but its PL/pgSQL runtime is not yet live-verified.
Live `agent_publish_queue` has zero rows/READY items, so no Service INSERT,
new-row readback, or Backend API E2E was performed. See
`docs/PUBLISH_WORKER.md` and `docs/AUTOMATION_RUNTIME.md`. Discovery Scheduler
and later phases remain out of scope.

## Current status — PHASE 8 (2026-10-04)

PHASE 7.3C migration 005 was applied **once** to live Supabase and verified: catalog/privileges, atomic claim/lease/recovery, canonical window/Run guards, success reconciliation, one public HTML GET → RawDocument, failure backoff, dry run and final DISABLED smoke Source. Existing five HOLD Sources stayed inactive; 166 baseline tests passed. Historical "005 unapplied" notes below describe earlier phases and are superseded here.

PHASE 8 adds a private `agent-raw` Supabase Storage bucket, selectable persistent RawContentStorage and ProcessingWorker. Migration 006 was applied on 2026-10-04. Live collection produced a private 22,590-byte RawDocument with matching SHA-256; Processing claim/lease/failure/version checks passed. The single real Worker pass failed closed at `CROSS_RAW_CACHE` because identical content already had a Candidate, and the daily LLM budget was 5/5. Candidate→Gate live E2E and success-terminal verification remain open; PHASE 8 final runtime completion is **not claimed**. Test Source ended DISABLED; original five remain HOLD. Service DB writes and new LLM calls in this runtime validation: zero. See `docs/RAW_STORAGE.md` and `docs/PROCESSING_WORKER.md`.

PHASE 8 final retry: Extraction Budget defaults now resolve by `APP_ENV` (explicit development 20/300, production/unset/unknown 5/100). The connected Supabase project is `main PRODUCTION`, with `APP_ENV` unset; it used 5/100, not development limits. A separate `runtime_processing_final_smoke_test` AAAI page was officially approved, collected once to private Storage, hash-verified, confirmed to have a new extraction fingerprint, and formally disabled. Processing dry run included it without mutation; actual Worker/LLM was not run because production daily usage remained 5/5. Existing five Sources and Service DB were not changed. Candidate→Gate E2E remains open.

## Current implementation status (PHASE 7.3B Source Review)

- PHASE 7.3B: 사람의 Source 검토→승인→활성화 서비스, source_type 검토 변경, 원자적 Agent DB 전이·append-only audit migration 004, Discovery 공식성 신호와 승인 분리, Registry/CLI 우회 차단, CollectionRunner fail-closed와 읽기 전용 Service Source identity preview를 구현했다. 004는 2026-10-04 **실DB에 적용·검증했다**. 기존 5 Source/Candidate/Service DB는 수정하지 않았다. 상세는 `docs/SOURCE_APPROVAL.md`. PHASE 8은 시작하지 않았다.

- PHASE 7.3A: 검증된 Conference 추출값만 사용하는 결정적 행사·접수 temporal normalization, Agent Candidate 저장 writer와 provenance, 조기 REVIEW에서도 보이는 Gate 진단, 명시적 날짜 범위 및 근거 ID 검증 결함 보정을 구현했다. 기존 5건은 읽기 전용으로만 재평가했다. 기존 DB/Service DB write·LLM 호출·migration 0건이며 Publish 정책/threshold는 그대로다. 상세는 `docs/NORMALIZATION_ENGINE.md`.

- PHASE 7: 기존 `PublishCandidate v1`을 Service DB에 매핑하는 Dry Run/상태·날짜·timezone 정책, Source/Category/Tag 조회, 최종 충돌 검사, Supabase ServiceDBAdapter, Agent queue 결과·audit 기록과 멱등 재조정 경계를 구현했다. 기본 게시 플래그는 비활성이다. 기존 5개 후보의 실DB 읽기 전용 평가 결과는 적격 0건, Service INSERT 0건이다. 실제 READY queue/Service INSERT/readback/Backend API 새 행 검증은 미실행이며 성공으로 간주하지 않는다. 상세는 `docs/SERVICE_PUBLISH.md`. PHASE 8은 시작하지 않았다.

- PHASE 6: Agent 검증값 기반 결정적 Dedup→Verification→PublishGate와 Service DB Opportunity SELECT-only 중복 조회, `agent_verifications` 저장, `agent_publish_queue.candidate_id UNIQUE` 멱등 처리를 구현했다. 기존 5건 최종 실DB 결과는 `UNIQUE` 4/`POSSIBLE_DUPLICATE` 1(같은 RawDocument의 구버전)/`REVIEW_REQUIRED` 5/`PUBLISH_READY` 0, Agent verification 이력 21행, queue 0개다. 새 LLM·Service write·migration 0개. 상세는 `docs/QUALITY_GATE.md`. PHASE 7은 시작하지 않았다.

- PHASE 5.6: `conference-grounded-v2`·`gpt-5.4-nano`·Prompt·validator를 변경하지 않고 ICCAI 기존 1건과 신규 공식 Conference 4건을 벤치마크했다. 신규 4건은 기존 Generic Collection Engine→Agent RawDocument→예산 Guard→실제 LLM 각 1회로 처리했다. Core Precision 27/27, Recall 27/40, Evidence Pass 32/49, `REVIEW_REQUIRED` 5/5였다. Source Missing 25칸과 Extraction Miss 13칸을 분리했다. 실제 호출은 이번 Phase 4회, 추가 retry·다른 모델 호출 0회. 상세 Ground Truth/20-field matrix/원인 분류는 `docs/EXTRACTION_BENCHMARK.md`. PHASE 6는 시작하지 않았다.

- PHASE 5.5: 기존 Conference schema/Provider/Budget 구조를 유지하고 결정적 Evidence Block·Link ID, 모델의 ID 선택형 strict output, 코드의 ID 해결·텍스트/날짜/URL 대조, 버전 분리 캐시를 구현했다. 동일 ICCAI RawDocument에 `gpt-5.4-nano` 실제 호출 1회로 기존 0/20 → 신규 9/20 검증 필드가 됐다. 두 후보는 모두 보존된 `REVIEW_REQUIRED` 기록이며 Service DB 게시는 없다. migration 없음, 전체 테스트 87개 통과. `docs/EVIDENCE_GROUNDING.md` 참조. PHASE 6는 시작하지 않았다.

- PHASE 5 기준선: Conference 전용 nullable schema·필드별 evidence/confidence, HTML 전처리, OpenAI strict structured output Provider, 날짜/URL/근거 검증, `agent_candidates`/`agent_logs` 저장, 실DB 예산 Guard와 중복 캐시를 구현했다. 공개 ICCAI 페이지의 v1 실제 LLM 1회 결과는 근거 인용 불일치로 검증값 0/20, `REVIEW_REQUIRED`였다. 해당 기록은 v2와 별도로 보존한다. `docs/EXTRACTION_ENGINE.md` 참조.

- PHASE 4.5: 003을 실제 Supabase에 적용해 Discovery 3개 테이블·REST 접근을 확인했다. 이후 Tavily Provider·검색 예산 Guard를 구현하고 실제 검색 1회에서 Query→Result→Candidate 5건을 검증했다. 보수적 승격 조건 때문에 Source 승격은 0건이었으며 그 실DB 경로는 미검증이다. 상세는 `docs/DISCOVERY_RUNTIME.md`.

- PHASE 4: SearchResult와 Source를 분리한 DiscoveryQuery/SearchResult/SourceCandidate/SourceEvaluation, SearchProvider·FakeSearchProvider, 결정적 필터, 규칙 평가기, Promotion Gate 및 수동 DiscoveryRunner를 구현했다. 당시 003은 작성만 하고 적용하지 않았다. 상세 계약은 `docs/DISCOVERY_ENGINE.md`; 현재 적용 상태는 위 PHASE 4.5 항목을 따른다.
- PHASE 3.6 최종 Runtime DB E2E: 실제 Supabase Agent Source/Run/RawDocument Repository CRUD, 공개 HTML→Raw Content→RawDocument, ID/FK provenance 및 `FAILED` Run 확인을 완료했다. Smoke 행은 Agent 테이블에서 정리했다. 기존 Service Table 변경 없음.

- PHASE 1: 기존 시스템 분석·데이터 계약·아키텍처 문서 작성 완료.
- PHASE 2: 독립 패키지, 도메인/상태/Workflow State, Agent 테이블 마이그레이션, Service DB Adapter 계약 완료. 당시 Agent DB 마이그레이션은 미적용이었다.
- PHASE 3: Source Registry, Agent Repository(주입형 Supabase/메모리), 결정적 Collection Planner, 범용 HTML/RSS/REST API 수집기, URL/DNS 검사, Local Raw Storage, Collection Runner 및 오프라인 테스트 구현. Agent 전용 확장 마이그레이션은 `agent_collect/migrations/002_collection_foundation.sql`; 상세 계약은 `docs/COLLECTION_ENGINE.md`.
- PHASE 3.5: 독립 runtime 조립 코드와 수동 CLI, config/CLI/migration 범위 테스트를 추가했다. 기존 `.env`의 Supabase 변수 이름과 읽기 연결을 확인했으나 Agent 7개 테이블은 REST HTTP 404여서 migration 및 실제 Agent Repository/DB provenance 검증은 하지 않았다. 공개 HTML 1회 GET·로컬 raw 보존/재조회·메모리 Repository 흐름은 확인했다. 세부 결과와 후속 조건은 `docs/RUNTIME_INTEGRATION.md`.
- PHASE 3.6 초기 감사: 당시 DDL 경로가 없어 001/002는 미적용, DB E2E는 미완료였다. 신규 기능·003 migration은 만들지 않았다. Agent 테이블 REST 재조회는 당시 7개 모두 HTTP 404였다. 이후 적용 결과는 다음 항목을 따른다.
- PHASE 3.6 후속: 로그인된 `aipath-korea` Supabase Dashboard SQL Editor에서 001→002를 적용했다. Agent 테이블 7개·RLS·002 컬럼/CHECK 및 REST 접근(모두 HTTP 200)을 확인했다. 서비스 테이블 7개의 컬럼 기준값은 전후 동일했다. 이후 Runtime DB E2E도 확인했다. 상세 결과는 `docs/RUNTIME_INTEGRATION.md`.
- 기본 정책: raw content 보존 90일, verification confidence 0.75, publish confidence 0.85. 모두 설정으로 변경 가능.
- 자율 Discovery Agent, Conference 이외 vertical 추출, 행사·접수 temporal 이외 Opportunity 정규화/분류, 실제 서비스 DB 게시 E2E는 아직 구현·검증하지 않음. Conference 중복·검증·Publish Gate는 PHASE 6에, Service 게시 Adapter 경계는 PHASE 7에 구현됐다. Tavily Search Provider와 Conference Extraction Provider는 구현됐다. PHASE 4의 Source Promotion Gate는 Service Publish Gate와 다르다.

## 0. Project Principles

- 기존 Frontend 유지
- 기존 Backend 유지
- 기존 Service DB 최대한 유지
- 기존 Collect는 신규 설계에서 무시
- 기존 Collect 수정/마이그레이션 금지
- Agent Collect는 신규 구축
- 기존 Collect는 필요한 경우 Source 목록 참고만 가능
- Agent와 서비스 Backend를 느슨하게 결합
- Agent 내부 표준 Schema와 Service DB Schema 분리
- Agent가 판단하고 deterministic code가 실행하는 구조 우선
- Source별 전용 Collector 생성을 최소화
- Config-driven + Agent-managed Collection 지향


## 1. Existing System Analysis

목적:
현재 Frontend / Backend / DB에서 실제로 필요한 데이터 구조를 파악한다.

작업:
- 프로젝트 전체 구조 분석
- Backend API 구조 분석
- DB 모델 분석
- Frontend가 사용하는 데이터 필드 분석
- 기존 Collect 코드는 구현 참고 대상으로 사용하지 않음
- 신규 Agent가 최종적으로 제공해야 하는 데이터 계약 정의

산출물:
docs/EXISTING_SYSTEM_ANALYSIS.md
docs/DATA_CONTRACT.md

완료 조건:
Agent Collect가 최종적으로 어떤 데이터를 Service DB에 제공해야 하는지 명확할 것.


## 2. Agent Collect Architecture

신규 agent_collect 모듈 설계.

핵심 흐름:

DISCOVER
→ SOURCE_ANALYZE
→ COLLECT
→ RAW_STORE
→ EXTRACT
→ NORMALIZE
→ CLASSIFY
→ DEDUP
→ STAGE
→ VERIFY
→ PUBLISH / REVIEW / REJECT

산출물:
docs/AGENT_COLLECT_ARCHITECTURE.md


## 3. Database Design

Agent 전용 데이터 모델 설계.

필수 개념:

source_registry
collection_runs
raw_documents
extracted_items
verification_results
publish_queue
agent_logs

각 데이터에는 가능한 경우:

source
source_url
source_type
discovered_at
collected_at
verified_at
confidence
status

등 provenance 정보를 유지한다.

Service DB와 Agent 내부 DB 모델은 직접 결합하지 않는다.

산출물:
docs/AGENT_DATABASE.md


## 4. Source Registry

Source를 코드가 아니라 데이터로 관리한다.

Source Registry 역할:

- Source URL
- Source 이름
- Source 유형
- 수집 방식
- 카테고리
- 국가
- 언어
- 신뢰도
- 수집 주기
- 최근 성공/실패
- 활성 상태
- 발견 방법
- Agent 판단 정보

목표:
새 Source 추가 시 Python Collector 파일을 새로 만들지 않아도 되는 구조.


## 5. Discovery Agent

인터넷에서 새로운 AI 관련 Source를 발견한다.

대상 예:

- Conference
- Competition
- Hackathon
- Education
- Meetup

향후 확장:

- Grant
- Research
- AI Job
- Government Program
- Startup Program

Discovery 결과는 즉시 Service DB에 넣지 않는다.

Source Registry Candidate로 저장한다.


## 6. Generic Collection Engine

범용 Collector 구현.

지원 방식:

- HTTP
- REST API
- RSS
- HTML
- Sitemap
- Search-based discovery

가능한 경우 LLM을 사용하지 않는다.

원칙:

Agent = 판단
Code = 실행

예:

Agent가 RSS 존재 판단
→ Registry 저장
→ 이후 Generic RSS Collector 실행


## 7. Extraction

Raw Document에서 구조화 데이터를 추출한다.

공통 필드 + Vertical별 Schema 구조 사용.

예:

EventBase
Conference
Competition
Hackathon
Education

새 Vertical 추가 시 전체 Pipeline 변경을 최소화한다.


## 8. Normalization

정규화 대상:

- 날짜
- 시간대
- 국가
- 언어
- URL
- 기관명
- 카테고리
- 온라인/오프라인
- 가격
- 통화
- 접수 기간
- 행사 기간

LLM이 필요하지 않은 항목은 deterministic code 사용.


## 9. Classification

수집 데이터를 자동 분류.

예:

conference
competition
hackathon
education
meetup
grant
research
job
unknown

분류 confidence 저장.


## 10. Deduplication

중복 판단을 여러 단계로 수행.

1. Exact URL
2. Canonical URL
3. Normalized title
4. Organizer + Date
5. Similarity
6. 필요 시 LLM 판단

LLM부터 사용하지 않는다.


## 11. Verification Agent

검증 항목:

- Source 신뢰도
- 공식 사이트 여부
- 행사 존재 여부
- 날짜
- 마감일
- 필수 필드
- URL 유효성
- 교차 출처 확인

결과:

VERIFIED
REVIEW_REQUIRED
REJECTED

confidence score 저장.


## 12. Staging & Publish Gate

Agent 데이터는 Service DB에 바로 저장하지 않는다.

Agent
→ Raw Store
→ Staging
→ Verification
→ Publish Gate
→ Service DB Adapter
→ Service DB

Publish 조건은 설정 가능하도록 구현.

예:

verified = true
duplicate = false
required_fields = valid
confidence >= threshold


## 13. Service DB Adapter

Agent 내부 Schema를 기존 AIPATH Service DB Schema로 변환한다.

Agent Collect는 Backend 내부 구현에 직접 의존하지 않는다.

Agent Schema
→ Adapter
→ Service DB

향후 Backend가 변경되어도 Agent Pipeline은 유지 가능해야 한다.


## 14. LangGraph State

Pipeline 상태 정의.

예:

run_id
source
raw_document
extracted_item
normalized_item
classification
duplicate_result
verification_result
confidence
errors
retry_count
publish_status

각 Node가 State를 받아 State를 갱신하는 구조.


## 15. LangGraph Workflow

기본:

START
 ↓
DISCOVER
 ↓
SOURCE_ANALYZE
 ↓
COLLECT
 ↓
RAW_STORE
 ↓
EXTRACT
 ↓
NORMALIZE
 ↓
CLASSIFY
 ↓
DEDUP
 ↓
STAGE
 ↓
VERIFY
 ↓
 ┌───────────────┬───────────────┐
 │               │               │
PUBLISH       REVIEW          REJECT
 │               │               │
END             HOLD             END

실패 시 retry / hold / reject 분기 구현.


## 16. Observability

반드시 추적 가능해야 함.

기록:

- Agent 실행
- Source 발견
- Collection 성공/실패
- Token 사용
- LLM 호출
- 처리 시간
- Verification 결과
- Publish 결과
- 오류

향후 Admin Dashboard에서 사용할 수 있는 구조로 설계.


## 17. Conference E2E

첫 Vertical은 Conference.

목표:

Internet
→ Discovery
→ Collection
→ Raw Store
→ Extraction
→ Normalize
→ Classify
→ Dedup
→ Staging
→ Verify
→ Publish

전체 흐름을 실제 데이터로 완성.

Conference가 완성되기 전 다른 Vertical 구현을 과도하게 확장하지 않는다.


## 18. Testing

필수 테스트:

Unit Test
Integration Test
Workflow Test
Schema Validation
Dedup Test
Verification Test
Publish Test

외부 API/LLM 의존 테스트는 Mock 가능하게 설계.


## 19. Expansion

Conference E2E 안정화 후:

Competition
→ Hackathon
→ Education
→ Meetup

순차 확장.

공통 Engine 변경을 최소화한다.


## 20. Definition of Done

Agent Collect v1 완료 조건:

- 새로운 Source 발견 가능
- Source Registry 자동 등록 가능
- 범용 수집 가능
- 구조화 추출 가능
- 정규화 가능
- 자동 분류 가능
- 중복 제거 가능
- 신뢰도 검증 가능
- Staging 가능
- Publish Gate 작동
- 기존 Service DB로 Publish 가능
- Conference E2E 작동
- 실행 과정 추적 가능
- 기존 Frontend 정상 작동
- 기존 Backend 정상 작동
- 기존 Collect에 의존하지 않음

## PHASE 4.5 FINAL 구현 현황 (2026-10-03)

- `agent_collect/discovery/tavily.py`: Tavily SearchProvider, 최소 SearchResult 정규화, 안전한 오류 코드. API Key는 `AGENT_TAVILY_API_KEY` 환경에서만 주입한다.
- `agent_collect/discovery/budget.py`: `agent_discovery_queries` 실DB 기록 기준 일 30·월 1000·동일 정규화 질의 24시간 cooldown. 월간 800 WARNING, 950 CRITICAL, 1000 HARD STOP, Asia/Seoul 경계. 모든 값은 config에서 변경 가능하다.
- 실제 `AI conference Korea` 검색 API 1회 → Supabase Query 1건, Result 5건, Candidate 5건. Source 승격 0건(공식성 근거 부족으로 REVIEW_REQUIRED). 동일 질의 변형은 DB cooldown으로 차단돼 추가 API 호출 0회. 전체 unit test 통과.
- 001/002/003 변경 없음, 004 생성 없음. 기존 Frontend/Backend/Collect/Service Table 변경 없음. 병렬 워커에서 검색 예산의 원자적 상한은 아직 보장하지 않는다. 이 항목은 PHASE 4.5 종료 시점의 기록이며, 현재 PHASE 5.5 상태는 문서 상단을 따른다.

## PHASE 7.3B Migration 004 Security Hardening (2026-10-04)

- 004가 Supabase에 미적용(`agent_source_review_events` 및 `agent_review_source` 부재)인 것을 읽기 전용으로 재확인한 뒤 **기존 004 파일만 수정**했다. 005는 만들지 않았다.
- Agent Source 신규 INSERT는 DB trigger에서 비활성 `CANDIDATE` 및 승인 메타데이터 부재를 강제한다. 기존 `HOLD` 행은 변환하지 않는다. Repository 신규 INSERT/기존 UPDATE를 분리했다.
- lifecycle 직접 UPDATE는 승인 RPC 소유자 실행 문맥과 전환 표식을 함께 요구한다. 감사 테이블은 `service_role`에 SELECT만 부여하며, 단일 `SECURITY DEFINER` RPC가 Source 변경과 감사 INSERT를 원자적으로 수행한다. RPC의 search path는 고정하고 EXECUTE는 `service_role`만 허용한다.
- 오프라인 보안 및 전체 회귀 테스트는 실행하되, 실제 PostgreSQL 역할별 enforcement는 이번 단계에서 주장하지 않는다. 수정된 004의 적용과 live permission/RPC 검증은 별도 Runtime Validation으로 남긴다. Service DB write, LLM 호출, 기존 Source 승인/활성화는 없다.

## PHASE 7.3B FINAL Runtime Validation (2026-10-04)

- 수정된 004를 Supabase SQL Editor에서 **정확히 1회 적용**했다. 사전 조회에서 Agent Source 5건 모두 HOLD/inactive, 004 객체 없음, Agent 기본 테이블 7개 존재를 확인했다. 001/002/003 재실행, 005 생성, Service Table 변경은 없다.
- 실제 catalog에서 lifecycle CHECK 2개, Source INSERT/UPDATE trigger, Agent 감사 테이블과 UPDATE/DELETE trigger, FK·인덱스·RLS, `postgres` 소유 `SECURITY DEFINER` RPC의 `search_path=pg_catalog`와 `service_role` 전용 EXECUTE를 확인했다. 감사 테이블은 `service_role` SELECT만 가능하다. ACTIVE snapshot CHECK는 기존 행 호환을 위해 `NOT VALID`이며 신규/갱신 행에 적용된다.
- 정상 Repository 등록으로 합성 테스트 Source `87c7eb04-095a-4cbe-855b-ca7e3e81e439`를 비활성 CANDIDATE로 생성했다. service_role REST의 직접 ACTIVE/APPROVED INSERT, 승인 metadata 주입, CANDIDATE 직접 전환, 감사 INSERT/UPDATE/DELETE는 모두 실패했다. 정식 RPC로 REVIEW→APPROVED→ACTIVE→DISABLED를 수행했고 감사 이벤트 4건을 readback했다. snapshot mismatch activation은 트랜잭션 내 임시 변경·거부 확인·전체 ROLLBACK으로 검증했다. 테스트 Source 최종 DISABLED/inactive, 기존 5건 HOLD/inactive 유지.
- CollectionRunner는 실제 APPROVED 상태를 차단하고 실제 ACTIVE 상태에서 가짜 Planner까지 진행했다(HTTP 0회). CANDIDATE/REVIEW는 메모리 재현으로 같은 Runner 경로를 검증했다. Service Source 29건 읽기 전용 비교 결과 매핑 NOT_FOUND, Service DB write 0회. LLM/Tavily 호출 0회. 당시 전체 145 tests 통과. 이 기록 시점에는 PHASE 7.3C/8 미착수였으며 아래에 7.3C 후속 상태를 기록한다.
# PHASE 7.3C status (2026-10-04)

Known ACTIVE Source collection scheduling foundation has been added: fixed interval/next time, bounded due read, Agent-only atomic claim/lease RPC contract, failure backoff/review flag, one-shot worker, dry run, CLI, scheduled Run provenance and offline tests. `agent_collect/migrations/005_collection_scheduling.sql` is **created only, not applied**. No production scheduler, Discovery scheduler, processing worker or Service publish automation is part of this phase. Live Supabase scheduling validation remains a separate next step; details in `docs/COLLECTION_SCHEDULING.md`.

PHASE 7.3C migration 005 security hardening: production due/claim/completion RPCs no longer accept caller time; PostgreSQL server `clock_timestamp()` controls due, lease and schedule transitions. Window IDs derive deterministically from Source ID + UTC due microsecond; scheduled Runs require a canonical non-NULL UUID window ID and retain the DB unique guarantee. 005 remains **unapplied**. Runtime validation and later automation are not started.

PHASE 7.3C completion reconciliation fix: a persisted SUCCEEDED Run wins over NULL/late failure completion for its claim/window. Agent-only completion records make repeated completion idempotent, and crash recovery derives the next schedule from the successful Run's finish time. The worker reports the DB terminal outcome. 005 remains **unapplied**; no runtime validation or later automation was started.

PHASE 7.3C 005 lock-privilege fix: scheduled Run INSERT/UPDATE trigger functions now use an explicit `postgres`-owned `SECURITY DEFINER` boundary for schedule-row locks, with fixed `pg_catalog` search path and revoked direct EXECUTE. `service_role` remains SELECT-only on schedules, with mutation limited to scheduling RPCs. This is offline hardening only; 005 is **not applied** and live privilege behavior remains to be validated.
