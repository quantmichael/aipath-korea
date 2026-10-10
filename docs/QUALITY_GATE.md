# Agent Collect Quality Gate — PHASE 6 (2026-10-04)

## 범위와 경계

수동 `QualityGateRunner`는 저장된 Conference Extraction Candidate를 다시 읽어 `Deduplication → Verification → PublishGate → agent_verifications → (PUBLISH_READY만) agent_publish_queue`를 실행한다. 새 LLM·검색·HTTP 수집은 없다. 기존 `agent_candidates`와 서비스 테이블은 수정하지 않는다. Service DB 조회 구현은 `agent_collect/adapters/service_db/duplicate_lookup.py`의 SELECT 전용 포트로만 존재하고, 게시 포트 `ServiceDBAdapter.publish()`는 호출하지 않는다. `agent_publish_queue.READY`는 실제 서비스 게시가 아니다.

PHASE 7.3A 이후 정규화는 별도 `NormalizationRunner`가 명시적으로 수행한다. Gate는 저장된 정규화 입력 hash를 추출값과 대조하며, Verification이 REVIEW로 조기 반환해도 `checks.normalization`에 `MISSING/STALE/NORMALIZED/REVIEW_REQUIRED`와 시간 정밀도·시간대 근거를 남긴다. Gate 결정 순서/threshold는 그대로다. 아래 실제 5건의 verification 이력은 이전 단계의 역사적 기록이며 재실행하거나 수정하지 않았다.

## Dedup Architecture와 정책

Agent 후보의 **검증된 값**과 기존 서비스 `opportunities`의 읽기 전용 스냅샷을 `DuplicateRecord`로 투영한다. Agent/Service 전체 조회는 각 500행 상한이며 응답의 exact count가 없거나 상한을 넘으면 fail-closed `REVIEW_REQUIRED`다. 운영 데이터가 커지면 페이지 기반 완전 조회 또는 인덱스 기반 검색으로 교체해야 한다. 현재 Service 조회는 `id,title,organizer,official_url,event_start_at`만 요청한다. 서비스의 registration URL은 현 스키마에 없어 비교하지 않는다.

우선순위: canonical URL → official URL → registration URL → 정규화 title+같은 date → 동일 RawDocument의 추출 버전 → organizer+같은 date → www/scheme 차이 URL 힌트 → 문자열 similarity. Registry의 URL canonicalization을 재사용하여 trailing slash, fragment, `utm_*`, `fbclid`, `gclid`를 정리한다. www/scheme 차이와 같은 원문의 구·신 추출 버전은 **확정 중복이 아닌** `POSSIBLE_DUPLICATE`다. Unicode NFKC·casefold·구두점·공백 정규화를 쓰되 도메인만 같다는 이유로 합치지 않는다. 두 명확한 행사 연도가 다르면 별개로 본다. ICCAI/IJCAI 같은 서로 다른 짧은 약칭은 높은 문자열 점수만으로도 묶지 않는다. Exact URL/같은 title+date는 `DUPLICATE`; 다른 단서는 `POSSIBLE_DUPLICATE`; 조회가 불완전하면 `REVIEW_REQUIRED`; 일치가 없으면 `UNIQUE`다. match type, similarity, matched Agent/Service ID, 이유와 시각을 검증 레코드에 저장한다. 확정 중복도 이번 단계에서 기존 레코드를 갱신하거나 삭제하지 않는다.

## Verification와 Required Fields

`docs/DATA_CONTRACT.md`의 필수 `title`, `summary`, `organizer`, `official_url`, 출처 이름·provenance·근거를 지킨다. 초기 Conference 자동 게시 정책은 여기에 검증된 `event_start`를 추가한다. 나머지 20개 추출 필드를 모두 필수로 만들지 않는다. 추출 `fields.*.value`가 있더라도 `validation_status=VALID`가 아니면 `MISSING/UNVERIFIED/INVALID` 계열로 취급하며 게시 필드로 쓰지 않는다. 필요한 각 필드에 코드가 확인한 evidence ID와 `evidence_text`를 요구한다. URL은 기존 안전 검사, 날짜는 유효한 ISO calendar date 및 순서로 검사한다. Source→Run→RawDocument→Candidate ID 관계와 Source Registry 유형/상태도 확인한다.

Source Trust는 `OFFICIAL/TRUSTED/UNKNOWN/LOW_TRUST`다. Registry의 `official`은 OFFICIAL, confidence 0.8 이상 aggregator는 TRUSTED, 그 외 불확실한 출처는 UNKNOWN, community/거절 Source는 LOW_TRUST다. TRUST는 보조 조건일 뿐 혼자서 VERIFIED를 만들지 못한다. `VerificationResult`는 `VERIFIED/REVIEW_REQUIRED/REJECTED`, confidence, 검사별 결과, 근거 ID, reason code, 시각을 갖는다. 불명확·값 누락·가능 중복·낮은 신뢰도는 검토, 비 Conference/명시적으로 거절된 Source는 거절이다. 확정 중복은 새 정보 가치 판단이 없어 우선 검토로 보류한다.

주요 reason code: `MISSING_REQUIRED_FIELD:<field>`, `INSUFFICIENT_EVIDENCE:<field>`, `INVALID_EVIDENCE:<field>`, `INVALID_DATE`, `INVALID_URL`, `MISSING_PROVENANCE`, `MISSING_RAW_DOCUMENT`, `POSSIBLE_DUPLICATE`, `DUPLICATE`, `UNKNOWN_SOURCE_TRUST`, `LOW_SOURCE_TRUST`, `LOW_CONFIDENCE`, `UNNORMALIZED_EVENT_TIME`, `SERVICE_LOOKUP_FAILED`, `AGENT_LOOKUP_FAILED`, `NOT_CONFERENCE`, `INVALID_SOURCE`. Score만으로 hard requirement를 우회하지 않는다.

## Publish Gate, Queue, 멱등성

`PUBLISH_READY`는 Conference, `UNIQUE`, `VERIFIED`, 모든 필수 검증값·근거, publish confidence(기본 0.85), 그리고 `PublishCandidate v1`을 만들 수 있는 **명시적 시간대가 있는** `event_start_at`을 모두 만족해야 한다. Runner는 Agent `normalized_payload.event_start_at/event_end_at`의 timezone-aware ISO 값을 읽되, 정규화나 추정을 수행하지 않는다. 추출 단계의 `YYYY-MM-DD`만으로 임의 시각/시간대를 만들지 않는다. 현재 5개 후보는 이 정규화가 없어서 단독으로도 `UNNORMALIZED_EVENT_TIME`에 해당하며, 다른 필수값도 빠져 있다. Gate는 검증 결과가 REVIEW/REJECT이면 queue를 건드리지 않는다.

Agent `agent_verifications`의 기존 `checks` JSONB에 policy version, duplicate summary, 최종 gate decision/status/reasons를, `reasons`/`evidence_refs`/`verified_at`에 검증 상세를 보존한다. 기존 `agent_publish_queue.candidate_id UNIQUE`를 사용해 같은 Candidate의 중복 queue 삽입을 막는다. 동시 삽입 충돌 시 기존 queue 행을 재조회하며 기존 행을 덮어쓰지 않는다. 재검증은 새 verification 이력 행을 만들지만 queue 행은 한 개다. 이번 단계에서 migration 004는 필요 없어 생성·적용하지 않았다. 검증·큐 삽입은 서로 다른 REST 호출이므로 원자적 트랜잭션은 아니다. Queue 삽입 실패는 성공으로 숨기지 않고 재실행으로 복구할 수 있다.

## 실제 5건 Smoke

오프라인 테스트 통과 후 기존 v2 후보 5건을 실 Supabase에서 재조회했다. 사전 조회에서 Agent 비교 후보 6건, Service Opportunity 17건이 확인됐다. 첫 실행은 ICCAI와 IJCAI를 title similarity 0.9로 `POSSIBLE_DUPLICATE`로 보냈으나 서로 다른 약칭의 오탐이었다. 코드를 보수적으로 보강한 뒤 5건을 재실행했고, `normalized_payload` 연결을 추가해 5건을 다시 검증했다. 그 후 ICCAI의 과거 v1과 같은 RawDocument 관계를 발견하여 버전 충돌을 검토 대상으로 처리하고 ICCAI만 재실행했다. 마지막으로 Gate 사유 저장을 추가한 최종 코드로 5건을 재검증했다. 총 21개 verification 행은 감사 이력으로 남아 있고 최종 5건의 Candidate FK 및 `checks.gate_decision`을 실제 DB에서 확인했다. 최종 오프라인 전체 테스트는 105개 통과했다.

| Candidate | 최종 Dedup | Verification | Gate | 최종 verification ID |
| --- | --- | --- | --- | --- |
| ICCAI | POSSIBLE_DUPLICATE (`same_raw_document`, v1 후보 `8affb3c0-db03-44a0-9d0e-5a246f2dbadc`) | REVIEW_REQUIRED | REVIEW_REQUIRED | `c370a548-13b6-451e-985e-3af8686cf1c4` |
| AAAI | UNIQUE | REVIEW_REQUIRED | REVIEW_REQUIRED | `6d2d6796-04a6-455d-a412-2cef123f4a65` |
| ECAI | UNIQUE | REVIEW_REQUIRED | REVIEW_REQUIRED | `f623127a-f10a-4d3b-bb2d-5cbc05499a01` |
| IJCAI | UNIQUE | REVIEW_REQUIRED | REVIEW_REQUIRED | `5b49f8e2-35aa-475f-b478-ebd981d3fe95` |
| IEEE CAI | UNIQUE | REVIEW_REQUIRED | REVIEW_REQUIRED | `f627d9b1-0657-442e-920b-5ea7243df94a` |

최종 결과는 `UNIQUE` 4, `POSSIBLE_DUPLICATE` 1, `VERIFIED` 0, `REVIEW_REQUIRED` 5, `REJECTED` 0, `PUBLISH_READY` 0이다. 핵심 보류 원인은 validated summary/organizer/event_start/URL 누락, 낮은 candidate confidence, ICCAI의 미확정 Source trust와 같은 RawDocument의 v1 후보 존재다. 다섯 Candidate의 queue 행 0개를 실DB에서 재조회했다. Service duplicate SELECT는 사전 확인 1회+4회 실행×5건+ICCAI 중간 1회로 **22회**, 요청 컬럼은 위 5개이며 Service 쓰기 호출은 0회다. Agent verification 쓰기는 총 21행이다. 새로운 LLM 호출 0회. 서비스 테이블의 전후 데이터 해시를 찍지는 않았으므로 외부 변경까지 증명하는 감사 결과로 해석하지 않는다.

## Backlog / Decisions Required

- PHASE 5.7 제안: advanced date range parser, preprocessor recall tuning, organizer multi-block tuning, link preservation tuning, replacement LLM benchmark, Recall improvement research. 이번 단계에서는 모두 미구현.
- Extraction의 달력 날짜를 서비스 `timestamptz`로 바꾸는 시간대·시각 정책과 책임자를 확정해야 한다. 임의 자정 UTC를 게시하지 않는다.
- Service `sources` 매핑, 기존 Opportunity 충돌/갱신, 태그 미등록 처리, 공개 상태/slug 정책은 실제 게시 전에 결정한다.
- Agent/Service 조회 500행 제한을 넘는 운영 규모에서는 완전한 페이징/검색 전략과 인덱스를 설계한다.
- LLM-assisted dedup은 `DuplicateRecord` 비교 이후의 선택적 REVIEW 판단 포트로만 확장할 수 있으며 이번 단계는 LLM을 호출하지 않는다.
- 현재 Raw Content는 로컬 임시 경로여서 영속 운영 저장소로 이관하기 전 provenance 재검증이 불안정하다.
