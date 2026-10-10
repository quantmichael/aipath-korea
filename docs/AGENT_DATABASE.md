# Agent Collect Database — through PHASE 10 foundation

Migration 008 runtime update (2026-10-04): **applied**. The two new tables
were observed in `pg_catalog` with RLS; three indexes, six fixed-search-path
postgres-owned SECURITY DEFINER functions and the query guard trigger were
present. `service_role` direct schedule UPDATE/DELETE returned `42501`.
Seed queries and final smoke fixtures are disabled. The historical
"unapplied" design description below is superseded by this update. Rollback-only
SQL tests also verified 6h/24h later failure backoff, duplicate Query rejection,
and wrong worker/claim/window rejection without persistent fixture changes.

PHASE 10 added Agent-only migration 008. It adds
`agent_discovery_schedules` (reviewed, disabled-by-default Conference Query
Registry with next/last run, interval, failures, and live claim/lease) and
`agent_discovery_windows` (unique schedule/window terminal ledger), plus
schedule/claim/window identity columns on `agent_discovery_queries`. A partial
unique index prevents duplicate scheduled Query attempts per window. The
postgres-owned, fixed-search-path RPCs list due, claim, finish, and record a
reviewed enable/disable decision using the DB clock. The scheduled Query
trigger checks a live matching claim. `service_role` has SELECT-only access
to the new tables and EXECUTE-only mutation through RPCs; anon/authenticated
and PUBLIC have no EXECUTE. No Service object is changed. Catalog, privilege,
and runtime behavior were verified after application. See
`DISCOVERY_SCHEDULING.md`.

Migration 007 was subsequently applied and runtime-verified; the PHASE 9
"007 not applied" passage below is retained only as historical context.

PHASE 9 proposed migration `007_publish_automation.sql` is **not applied**.
It adds claim/lease/next-attempt columns and an index to `agent_publish_queue`,
plus Agent-only `agent_publish_completions` and `agent_runtime_leases` tables.
DB-server-clock SECURITY DEFINER RPCs provide bounded due lookup, atomic
publish claim/completion, and Automation lease acquire/release. Service tables
and 001-006 are unchanged. Direct queue UPDATE/DELETE is revoked from
`service_role`; verified READY enqueue INSERT remains available. See
`docs/PUBLISH_WORKER.md`.

Current status (2026-10-04): 005 and 006 are **applied**. Migration 006 added `agent_processing_jobs` and `agent_processing_completions` (15 applied Agent tables total), plus bounded preview/seed/due/claim/checkpoint/finish RPCs. Live catalog checks found both tables, six RPCs, RLS and SELECT-only `service_role` table access; direct UPDATE/DELETE returned 42501. Claim/lease/failure/version runtime checks passed, while successful Candidate→Gate processing remains unverified because the smoke RawDocument matched an existing extraction cache and the daily LLM budget was full. Existing `agent_raw_documents.content_ref` points to private Supabase Storage; no Service table was changed. Historical unapplied notes below are superseded. See `docs/COLLECTION_SCHEDULING.md`, `docs/RAW_STORAGE.md` and `docs/PROCESSING_WORKER.md`.

## 범위와 적용 상태

Agent 전용 데이터는 현재 Supabase 프로젝트 안의 `public.agent_*` 테이블 **10개**로 논리 분리한다. [기본 001](../agent_collect/migrations/001_agent_foundation.sql)과 [수집 확장 002](../agent_collect/migrations/002_collection_foundation.sql)은 PHASE 3.6에 실제 적용·E2E 검증했다. PHASE 4.5에 [Discovery 003](../agent_collect/migrations/003_discovery_foundation.sql)을 SQL Editor에서 한 번 적용해 Discovery 테이블 3개와 RLS/FK/인덱스, REST HTTP 200 및 Repository CRUD를 확인했다. 기존 서비스 테이블 7개/106개 컬럼의 스키마 해시는 003 적용 전후 같았고 서비스 데이터를 쓰거나 삭제하지 않았다. Agent 테이블에는 서비스 테이블을 향한 FK가 없다. 상세 결과는 `docs/DISCOVERY_RUNTIME.md`에 있다.

PHASE 7.3B의 [004 Source Review](../agent_collect/migrations/004_source_review_foundation.sql)는 2026-10-04 **실제 Supabase에 적용·검증했다**. Agent 전용 `agent_source_review_events`가 추가되어 총 11개 Agent 테이블이 됐다. `agent_sources`의 상태 CHECK를 확장하고 ACTIVE 승인 스냅샷 CHECK·전이 guard/RPC를 추가했다. 기존 5개 Source와 Service Table은 변경하지 않았다. 테스트 Source의 실제 role 권한·RPC·감사 체인 검증 결과는 `docs/SOURCE_APPROVAL.md`에 있다.

`public` 스키마의 접두사 분리는 테이블 소유권을 구분하지만 물리적 격리는 아니다. Agent와 서비스의 자격증명·운영 권한을 더 엄격히 나눌 필요가 있으면 적용 전에 별도 역할 또는 스키마 전략을 결정해야 한다.

## PHASE 4.5 Discovery 확장 — 003 적용

`agent_discovery_queries`는 질의·provider·vertical hint·실행 상태·시각·집계를 보존한다. Tavily 예산 Guard는 이 테이블의 `started_at IS NOT NULL` 행을 검색 시도로 계산한다. 차단은 `FAILED`, `started_at=NULL`, `provenance.budget_blocked/block_reason`으로 구분한다. 미실행 차단의 `completed_at`은 003 시간 제약에 따라 NULL이다. `agent_search_results`는 검색 결과의 원본 URL/title/최소 snippet/rank와 정규화 URL, 필터 상태·사유를 보존하며 `query_id`로 연결된다. Tavily 응답 전체나 raw page content는 저장하지 않는다. `agent_source_candidates`는 필터 통과 결과만 저장하며 `(search_result_id, query_id)` 복합 FK, 전역 `canonical_url` unique, 평가 decision/confidence/reasons/evidence, 선택적 `promoted_source_id → agent_sources`를 가진다. 평가를 별도 테이블로 분리하지 않고 후보의 현재 평가 스냅샷으로 보존한다. 재평가 이력이 필요하면 후속 migration에서 별도 테이블을 검토해야 한다. 003은 Agent 3개 테이블에만 RLS/grant를 설정하고 Service Table을 참조·변경하지 않는다. 실DB CRUD와 실제 Tavily Query→Result→Candidate FK는 검증했으나 실검색 Source 승격은 0건이었다. 이번 단계에 004는 생성·적용하지 않았다.

## 테이블과 관계

| 테이블 | 주 역할 | 핵심 FK·유일성·상태 |
| --- | --- | --- |
| `agent_sources` | Registry 후보와 활성 Source, 수집 설정·신뢰도·발견 근거·최근 수집 시각 | `canonical_url` 유일; 004 적용 전은 `CANDIDATE/ACTIVE/HOLD/REJECTED`, 적용 후 `REVIEW/APPROVED/DISABLED` 추가. `last_collected_at`은 002에서 추가 |
| `agent_source_review_events` (004 적용 후) | 사람의 검토·type 변경·승인·활성화 append-only 감사 | `source_id → agent_sources`; decision ID PK, Source/시간 인덱스 |
| `agent_collection_runs` | Source별 실행과 설정 스냅샷·결과 집계 | `source_id → agent_sources`; `RUNNING/SUCCEEDED/PARTIAL/FAILED`; `(source_id, started_at)` 인덱스 |
| `agent_raw_documents` | 수집 URL, 본문 참조, SHA-256, 수집 시각 | `(run_id, source_id) → agent_collection_runs`; 내용은 `content_ref`로 참조 |
| `agent_candidates` | 추출·정규화 payload, 분류·중복 결과, staging 상태 | `(raw_document_id, run_id, source_id) → agent_raw_documents`; `(raw_document_id, item_fingerprint)` 조건부 유일 |
| `agent_verifications` | 후보별 검증 판정과 증거·사유 | `candidate_id → agent_candidates`; `VERIFIED/REVIEW_REQUIRED/REJECTED`; 여러 차례 검증 이력 허용 |
| `agent_publish_queue` | 버전 있는 PublishCandidate 스냅샷, 게시 시도·서비스 상태·결과 | `candidate_id` 유일; `(verification_id, candidate_id) → agent_verifications`; `READY/PUBLISHING/PUBLISHED/HELD/FAILED` |
| `agent_logs` | 실행·출처·후보별 구조화 이벤트, 토큰·처리 시간·오류 | Agent 내부 FK만 사용; 이벤트 시간 인덱스 |

관계 흐름: `agent_sources → agent_collection_runs → agent_raw_documents → agent_candidates → agent_verifications → agent_publish_queue`. `agent_logs`는 실행·출처·후보를 선택적으로 참조한다. 복합 FK는 서로 다른 Source나 Run의 원문·후보·검증을 잘못 연결하지 않도록 한다.

`agent_raw_documents`의 `content_ref`는 실제 원문 저장 위치를 가리킨다. PHASE 3의 `LocalRawContentStorage`가 이를 구현하며 기본 보존 기간은 90일이다. 원문 본문을 테이블에 직접 넣지 않는다. 운영 저장소를 Supabase Storage 등으로 바꿀 수 있는 포트가 있다. JSONB payload는 Agent 내부 형식이며 Service DB 컬럼 계약이 아니다. `agent_publish_queue.publish_candidate`만 버전 지정된 게시 스냅샷이다. `agent_candidates`는 원문 저장과 추출 후 생성되므로 앞 단계의 상태는 Source/Run/RawDocument와 로그에서 추적한다.

## 상태와 제약

PHASE 7은 신규 Agent/Service DDL 없이 기존 `agent_publish_queue`의 `READY→PUBLISHING→PUBLISHED/FAILED` 상태·시도 수·Service ID/slug/status/게시 시각을 사용하는 Adapter와 Agent `agent_logs` 감사 기록을 구현했다. 실제 5건에는 READY queue가 없어 이 전이를 실DB에서 실행하지 않았고 Service INSERT도 0건이다. 계약·복구 한계는 `docs/SERVICE_PUBLISH.md`.

PHASE 6 Quality Gate는 기존 `agent_verifications`의 `checks` JSONB에 `conference-quality-v1` 정책 버전·필수값 검사·Source Trust·중복 결과·Gate 최종 상태/사유를 보존하고 `reasons/evidence_refs/verified_at`을 사용한다. `agent_publish_queue.candidate_id UNIQUE`가 큐 멱등성의 DB 최종 방어다. 기존 `agent_candidates.normalized_payload`에서 명시적 timezone-aware 행사 시각만 읽으며 이번 단계에 정규화값을 쓰지 않는다. 실제 Conference 후보 5건에 대해 재실행 포함 verification 21행을 Agent DB에 기록했고 Queue는 0행이었다. Service DB는 중복 SELECT만 했으며 신규 004 migration은 생성·적용하지 않았다. 상세는 `docs/QUALITY_GATE.md`.

PHASE 5 Conference Extraction은 신규 DDL 없이 기존 `agent_candidates.extracted_payload`에 schema version, nullable 필드별 검증값·근거·confidence·검토 사유를 저장하고 `provenance`에 Provider/모델/prompt version/input hash/원문 hash/추출 시각을 기록한다. 기존 `(raw_document_id,item_fingerprint)` unique가 동일 문서의 동일 버전 중복 후보를 막는다. `agent_logs`의 `EXTRACTION_LLM_ATTEMPT`는 Provider 호출 전에 남겨 일/월 사용량 기준이 된다. 실패·차단은 후보를 만들지 않고 로그/Runner outcome에 남긴다. 실제 Source→Run→RawDocument→Candidate ID/FK와 LLM 시도 로그 1건을 확인했다. 004 migration은 생성·적용하지 않았다. 상세는 `docs/EXTRACTION_ENGINE.md`에 있다.

코드의 `CandidateStatus`는 `DISCOVERED → SOURCE_ANALYZED → COLLECTED → RAW_STORED → EXTRACTED → NORMALIZED → CLASSIFIED → DEDUP_CHECKED → STAGED → VERIFYING → VERIFIED → PUBLISH_READY → PUBLISHED` 흐름을 표현한다. 중복·검수·거절·실패는 분기 상태다. SQL CHECK는 허용 상태 문자열을 제한하고, 전이의 순서 검사는 `agent_collect/schemas/status.py`에서 한다. DB 자체는 모든 전이를 강제하지 않으므로 향후 저장소 구현이 `require_transition()`을 호출해야 한다.

confidence는 0~1 범위, 실행 집계/시도/토큰 수는 음수가 아니어야 한다. `agent_publish_queue.status = PUBLISHED`에는 서비스 ID·slug·공개 상태·게시 시각이 모두 필요하다. `updated_at`은 현재 기본값만 있고 자동 갱신 트리거를 만들지 않았다. 향후 Repository가 갱신하거나 Agent 전용 트리거를 추가해야 한다.

모든 Agent 테이블에 RLS를 활성화하고 `anon`, `authenticated` 테이블 권한을 철회하며 `service_role`에 권한을 부여한다. 브라우저가 이 테이블에 직접 접근하지 않는다는 전제다. 실제 권한·정책은 적용 후 검증해야 한다.

## Model과 DB의 분리

`agent_collect/schemas/models.py`는 Source, CollectionRun, RawDocument, ExtractedItem, NormalizedItem, ClassificationResult, DuplicateResult, VerificationResult, PublishCandidate의 도메인 모델이다. 테이블 JSONB 표현이나 Supabase 응답 형태를 직접 모델에 넣지 않았다. 향후 `repositories/`가 도메인↔Agent row 변환을 담당하고, `adapters/service_db/`만 서비스 스키마를 이해한다.

## 적용 전 확인 및 남은 결정

1. 운영 Supabase의 PostgreSQL 버전, `gen_random_uuid()` 사용 가능 여부, 객체명 충돌, 권한과 기존 데이터 확인.
2. `content_ref`가 가리킬 원문 저장소·보존 기간·접근 권한.
3. Agent 실행용 자격증명과 서비스 게시용 권한을 같은 `service_role`로 운영할지, 분리된 DB 역할을 둘지.
4. `updated_at` 갱신 주체와 Repository의 동시성 제어 방식.
5. 현재 SQL은 파일 단위의 단일 `CREATE TABLE` 마이그레이션이다. 운영 적용 시 백업·적용 도구·롤백 계획 필요.
# PHASE 7.3C scheduling addendum

Agent-only migration `005_collection_scheduling.sql` is **created, not applied**. It adds `agent_source_schedules` (Source FK, fixed interval, timezone-aware next/last times, failure count/review flag, claim ID/worker/lease), bounded due/atomic claim/finish RPCs, an activation schedule trigger, interval guard and unique scheduled CollectionRun window index. It neither changes a Service table nor backfills or activates existing Sources. Schedule mutation privileges are RPC-only; Source lifecycle remains owned by 004. Live Supabase behavior is not yet verified. See [COLLECTION_SCHEDULING.md](COLLECTION_SCHEDULING.md).
