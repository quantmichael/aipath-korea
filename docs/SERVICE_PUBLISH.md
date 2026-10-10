# Agent Collect Service Publish Boundary — PHASE 7 (2026-10-04)

PHASE 9 addendum: `PublishWorker` now consumes a bounded, DB-claimed READY
queue and passes only `PublishCandidate` to this Adapter. Migration 007 (not
applied) replaces the old direct conditional queue update with Agent RPC
claim/completion and a publish lease; the preclaimed Adapter path uses a
claim-scoped ledger. An approved ACTIVE Agent Source is rechecked at publish
time. The live queue remains empty and no Service INSERT/readback/API new-row
test has occurred. See `docs/PUBLISH_WORKER.md`. Historical PHASE 7 direct
claim descriptions below are superseded when 007 is applied.

## 범위와 실제 결과

`PublishCandidate v1 → SupabaseServiceDBAdapter.dry_run() → queue authorization → Service mapping/status/conflict → Service Opportunity INSERT → readback → Agent queue/result/audit` 경계를 구현했다. 기존 Frontend, Backend, Collect, Service schema는 변경하지 않았다. 기본 `AGENT_PUBLISH_ENABLED=false`이므로 실제 쓰기는 명시적 활성화와 모든 데이터 조건을 함께 요구한다. 이번 실제 Conference 후보 5건은 모두 `REVIEW_REQUIRED`, `agent_publish_queue` 행 0건이라 **Service INSERT 0건**으로 종료했다. 실DB Source/Category 사전 조회 2회 외에 Service 쓰기는 없었다. 적격 후보가 없어 실제 Service INSERT·readback·Backend API 게시 확인은 수행하지 않았다.

## Agent → Service Mapping Contract

`agent_collect/schemas/models.py`의 `PublishCandidate`는 Agent→Service 경계 객체다. Agent 내부 Extraction/Verification 모델은 Service 컬럼을 알지 못한다. Service 전용 매핑은 모두 `agent_collect/adapters/service_db/`에 있다. Adapter는 `agent_publish_queue.READY`의 버전 있는 스냅샷이 전달된 Candidate와 정확히 같고, 연결된 `agent_verifications.result=VERIFIED` 및 `checks.gate_decision.status=PUBLISH_READY`일 때만 INSERT를 검토한다. `confidence >= AGENT_PUBLISH_THRESHOLD`도 다시 검사한다. 큐가 없거나 스냅샷이 다르면 fail-closed다. Service `sources/categories/opportunities/tags` 접근은 Adapter의 테이블 구현만 수행한다.

INSERT payload의 필수값: 기존 Source/Category FK, `external_id`, 안정적 `slug`, 제목·요약·주최·공식 URL, 결정적 공개 상태, `published_at`, `last_verified_at`, `is_featured=false`. 선택값은 실제 확인된 경우에만 넣는다. Agent raw 문서·프롬프트·토큰·판단 JSON은 Service DB에 복제하지 않는다. 이번 단계의 실제 INSERT가 있다면 `external_id=runtime_agent_publish_smoke_test:<agent_item_id>`로 식별한다. `slug`에도 Agent UUID 전체를 포함해 재실행 충돌을 방지한다. 기존 Opportunity를 UPDATE/MERGE하지 않는다.

## Date / Datetime Policy

현재 `public.opportunities`의 `application_start_at/application_deadline_at/event_start_at/event_end_at`은 모두 `timestamptz`; 별도 `date` 컬럼이나 정밀도 표시는 없다. Backend의 목록·상세 API는 이 값을 그대로 반환하고, 목록은 신청 마감일로 정렬한다. 따라서 `DATE_ONLY`를 `00:00` 또는 `09:00`으로 바꿔 저장하면 실제 시각으로 오인되고 의미가 손실된다. 행사와 접수 기간 각각에 대해 `DATE_ONLY`, `DATETIME_CONFIRMED`, `UNKNOWN`을 구분한다. 접수 timestamp가 없는 경우 접수 시간 근거는 불필요하지만, 하나라도 있으면 접수 시각·시간대가 모두 확인돼야 한다. 이번 Adapter는 **DATETIME_CONFIRMED만** Service datetime으로 매핑하고, 입력된 날짜가 `DATE_ONLY/UNKNOWN`이면 Dry Run에서 보류하며 임의 시간을 만들지 않는다. 추후 날짜-only를 게시하려면 Service에 별도 `date` 컬럼·정밀도 필드와 API 표시 정책을 함께 검토해야 한다. 이 변경안은 제안일 뿐 migration 파일은 만들거나 적용하지 않았다.

Timezone도 행사와 접수 기간 각각 `CONFIRMED/DERIVED/UNKNOWN`으로 분리한다. 명시된 timezone이 확인된 `CONFIRMED`만 해당 datetime 게시 가능하다. location에서 안전하게 추론한 `DERIVED` 값은 표시·검토 힌트로만 사용하고 행사에서 공표한 timezone으로 둔갑시키지 않는다. 도시/국가가 불명확하면 `UNKNOWN`이다. 현재 `PublishCandidate`는 `event_time_kind/timezone_basis`와 `application_time_kind/application_timezone_basis`를 운반한다. 필요한 근거가 없는 과거 스냅샷은 안전하게 보류한다.

## Publication Status

`DeterministicPublicationStatusResolver`는 LLM을 사용하지 않는다. 명시적 등록 시작 전은 `scheduled`, 등록 중은 `open`, 확인된 행사 시작~종료 사이는 `ongoing`, 마감 후 행사 전은 `closed`, 행사 종료 후는 `ended`다. 등록 기간 정보 없이 미래 행사 날짜만 있으면 모집 상태를 추정하지 않아 `None`이다. 기존 API는 `scheduled/open/ongoing`만 목록·상세에 노출하므로 Adapter는 그 셋일 때만 INSERT를 허용한다. `closed/ended/None`은 Dry Run 보류다. 행사 시작만 있고 종료가 없는 경우에도 `ongoing` 기간을 추정하지 않는다.

## Source / Category / Tag Mapping

Service Source는 활성 `sources.homepage_url`의 canonical exact match를 먼저 찾는다. 없으면 동일 domain과 정규화된 Source 이름이 **유일하게** 맞을 때만 재사용한다. 모호하거나 없으면 `REVIEW_REQUIRED`다. 새 Service Source 자동 생성은 하지 않는다. 수동 생성 검토 자격은 Agent Source ID 일치, `official`, `ACTIVE/is_active`, confidence 임계치 이상, homepage domain 일치일 때만 있다. 자격을 충족해도 별도 승인 전에는 생성하지 않는다. 이번 PHASE의 1건 이내 Service INSERT 경계를 유지한다.

PHASE 7.3B에는 승인된 Agent Source의 UUID·canonical URL·normalized domain·이름으로 기존 활성 Service Source를 읽기 전용 사전 매칭하는 `preview_approved_source_mapping()`을 추가했다. 명시적 Service Source ID가 있어도 URL 또는 domain+name 일치 검사를 우회할 수 없다. 매칭 실패·모호함은 REVIEW이며 자동 생성하지 않는다. 기존 PHASE 7 게시 정책과 Service 테이블은 변경하지 않았다. 004 적용 전에는 실DB 승인·매핑 E2E를 주장하지 않는다.

Category는 활성 `categories.slug=conference`를 조회해 재사용한다. 없거나 비활성이면 보류하고 자동 생성하지 않는다. Tag는 기존 slug를 조회해 매칭 결과를 Dry Run에 보여준다. 미등록 Tag는 만들지 않고 optional 값으로 생략한다. 이번 한 Opportunity INSERT 경계에서는 일치한 Tag도 `opportunity_tags` 관계를 쓰지 않으므로 `omitted_fields`에 명시한다. Tag 관계의 원자적 게시 정책은 후속 결정 사항이다.

## Final Conflict / Idempotency

Service 쓰기 직전 `opportunities`를 완전히 조회한다(현재 최대 500행, 불완전하면 중단). 안정적 slug 또는 `(source_id,external_id)` 일치, canonical official URL, 정규화 title+event date, organizer+event date를 확인한다. 결과는 `INSERT_ALLOWED/DUPLICATE/CONFLICT/REVIEW_REQUIRED`다. 동일 URL의 다른 연도는 무조건 같은 행사로 덮어쓰지 않고 `CONFLICT`로 보류한다. 기존 레코드가 있으면 새 INSERT는 하지 않는다. Dry Run 뒤 queue를 `READY→PUBLISHING` 조건부로 claim하고 충돌을 **다시** 확인한다. 안정적 slug와 `external_id`의 기존 DB unique 제약이 같은 Candidate의 재실행·동시 삽입에 대한 최종 방어다. 서로 다른 Candidate의 동일 URL 동시 INSERT는 현 스키마에 unique 제약이 없어 완전한 보장을 할 수 없으므로 다중 워커 운영 전 별도 해결이 필요하다.

## Dry Run / Adapter / Audit

`dry_run(PublishCandidate)`는 Candidate ID, Source/Category 매핑, matched Tag ID, 공개 상태, date/timezone 정책, 충돌 결과, 생략 필드, 보류 사유와 실제 INSERT 예정 payload를 반환하며 쓰기는 하지 않는다. 적격이 아니면 `publish()`도 Service에 쓰지 않는다. `publish()`는 성공·중복 건너뜀·검토·실패를 `PublishResult`로 반환한다. INSERT 성공 뒤 같은 Service ID를 readback하여 slug, external ID, source FK, 공식 URL을 확인한 후 Agent queue를 `PUBLISHED`로 갱신한다. 이전 INSERT는 성공했지만 Agent 기록 갱신이 실패한 경우 안정적 키로 기존 Service 행을 찾고 queue만 재조정한다. 완전한 분산 트랜잭션은 아니며 재조정 실패는 `FAILED`로 보고한다. Agent `agent_logs`에는 시도·결과·Service ID·adapter·시각을 남기되 비밀값은 저장하지 않는다.

이미 `PUBLISHED`인 큐를 재실행할 때도 큐의 Service ID와 실제 행의 핵심 값을 다시 확인하며, 불일치하면 성공 또는 중복 건너뜀으로 보고하지 않는다. 실제 게시가 생기면 Service DB readback 후 기존 `GET /api/opportunities/{slug}` 및 목록 API의 공개 상태 필터에서 조회되는지 확인해야 한다. 이번에는 INSERT 0건이므로 해당 확인을 성공으로 주장하지 않는다. 실제 smoke 행을 지워야 할 때는 위 `external_id`와 정확한 ID를 재확인하고 해당 행만 대상으로 하며, 의미 있는 정상 Conference라면 임의 삭제하지 않는다. 이번에는 정리할 Service smoke 행이 없다.

## 기존 Conference 후보 5건 — 실DB Read-only 사전 평가

실제 Service Source 29건과 활성 `conference` Category를 읽었다. 다섯 Agent Source는 현 Service Source에 안전한 canonical homepage/domain+name 매칭이 없었다. 최신 Agent verification Gate는 모두 `REVIEW_REQUIRED`, queue는 모두 비어 있다. 따라서 `PublishCandidate`를 강제로 만들지 않았고 전체 Service Dry Run payload도 생성하지 않았다.

| 후보 | Event start 표현 | Service Source | Category | Gate / Queue | 게시 가능 |
| --- | --- | --- | --- | --- | --- |
| ICCAI | DATE_ONLY | 불일치 | conference 존재 | REVIEW_REQUIRED / 없음 | 아니오 |
| AAAI | DATE_ONLY | 불일치 | conference 존재 | REVIEW_REQUIRED / 없음 | 아니오 |
| ECAI | UNKNOWN | 불일치 | conference 존재 | REVIEW_REQUIRED / 없음 | 아니오 |
| IJCAI | UNKNOWN | 불일치 | conference 존재 | REVIEW_REQUIRED / 없음 | 아니오 |
| IEEE CAI | UNKNOWN | 불일치 | conference 존재 | REVIEW_REQUIRED / 없음 | 아니오 |

Service 실제 INSERT 0, 실제 Service readback 없음, Backend API 새 게시 확인 없음, 추가 LLM 호출 0. 오프라인 Fake DB는 성공·실패·중복·재실행·Dry Run 경로를 검증한다. 실제 READY queue/INSERT 경로는 아직 실DB에서 검증되지 않았다.

## Backlog / Decisions Required

- Advanced extraction recall tuning, replacement LLM benchmark, Supabase Raw Storage, multi-worker atomic budget, 500행 초과 대규모 dedup 전략, Admin review UI는 미구현이다.
- 날짜-only Service 표현을 위한 최소 schema/API 변경안과 UI 표기, source 생성 승인 주체, matched Tag 관계 쓰기 정책을 결정해야 한다.
- 운영 전 Service 역할을 Agent 읽기/게시 쓰기 권한으로 분리할지, 다중 워커의 최종 충돌 원자성을 어떻게 보장할지 결정해야 한다.

## PHASE 7.1 Controlled E2E Verification (2026-10-04)

실제 Supabase를 읽기 전용으로 재검사했다. `agent_publish_queue`는 0행이고, 최신 Candidate 6행(실질 Conference 5개)의 최신 Verification은 모두 `REVIEW_REQUIRED`, confidence `0.49`였다. 모든 Candidate의 `normalized_payload`가 비어 있어 날짜·시간대 정책과 공개 상태 계산을 통과할 수 없다. Agent Source 5개는 모두 `HOLD/is_active=false`이며, 활성 Agent Source는 0개다. 기존 활성 Service Source와 안전하게 매핑된 Agent Source도 0개다. 활성 `conference` Category는 존재한다.

따라서 기존 Candidate를 변경하거나 Source를 임의 활성화하고 값을 보정하지 않았다. 새 LLM 호출도 수행하지 않았다. 적격 Candidate와 READY queue가 없으므로 Adapter의 실제 publish 호출, Service INSERT, readback, Backend API 조회, live idempotency 재실행은 수행하지 않았다. 검사 전후 Service Opportunity 수는 17건으로 유지됐고 Service DB write는 0건이다. PHASE 7.1은 fail-closed 검증으로 종료하며, 실제 publish E2E는 자연스럽게 생성된 VERIFIED/PUBLISH_READY/READY 후보가 생긴 뒤 완료해야 한다.
