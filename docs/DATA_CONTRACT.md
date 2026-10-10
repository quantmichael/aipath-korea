# Agent Collect → Service DB Data Contract — PHASE 2

기준: `codex_tasks.md`, `supabase/schema.sql`, `supabase/seed.sql`, `api/opportunities.py`, `public/js/opportunities.js`, `public/js/opportunity.js`, `public/js/recommend.js`. `agent_collect/schemas/models.py`에 `PublishCandidate` 구조·검사를, `agent_collect/adapters/service_db/`에 게시 인터페이스와 PHASE 7 Supabase Adapter를 구현했다. 적격 실데이터가 없어 실제 서비스 게시 E2E는 아직 검증하지 않았다.

PHASE 7.3A에서 Agent 내부 temporal 정규화가 추가됐다. 검증된 추출 필드만 `DATE_ONLY/DATETIME_CONFIRMED/UNKNOWN` 및 timezone `CONFIRMED/DERIVED/UNKNOWN`으로 구분한다. `event_start_at/event_end_at/application_start_at/application_deadline_at`은 명시적 offset이 있는 시각에서만 생성한다. 날짜-only는 calendar date로 보존하고 Service `timestamptz`로 임의 변환하지 않는다. 상세는 `docs/NORMALIZATION_ENGINE.md`를 따른다.

## 1. 계약 경계

```text
Agent 내부 모델 + 증거 + 검증 결과
    → Agent staging → verification → publish gate
    → 버전 지정된 PublishCandidate
    → Service DB Adapter
    → 기존 sources / categories / opportunities / tags / opportunity_tags
    → 기존 API → 기존 Frontend
```

PublishCandidate는 Service DB의 컬럼을 그대로 복제한 내부 모델이 아니다. Adapter만 서비스의 FK, slug, CHECK 값과 쓰기 방식을 안다. 서비스 DB에는 gate를 통과한 결과만 쓰며, raw document·프롬프트·토큰 로그·Agent 판단 상태는 쓰지 않는다. 기존 `opportunity_candidates`와 `collection_runs`는 신규 계약의 staging/실행 이력 저장소가 아니다.

## 2. PublishCandidate v1 제안

`필수`는 publish gate 통과 조건이다. `선택`은 확인되지 않았으면 `null`로 남긴다. 임의의 기본값을 사실처럼 공개하지 않는다.

| Agent 계약 필드 | 필수 | 서비스 매핑 / 규칙 |
| --- | --- | --- |
| `contract_version` | 필수 | `1`; Adapter 호환성 검사, 서비스 테이블에는 저장하지 않음 |
| `agent_item_id`, `agent_source_id`, `run_id` | 필수 | 재시도·감사·중복 추적용 내부 ID; 서비스 DB 직접 FK로 사용하지 않음 |
| `vertical` | 필수 | v1은 `conference`; Adapter가 서비스 `categories.slug = conference`에 매핑 |
| `title` | 필수 | `opportunities.title`; 원문 근거가 있는 비어 있지 않은 문자열 |
| `summary` | 필수 | `opportunities.summary`; 직접 요약, 원문 전체 복제 금지 |
| `description` | 선택 | `opportunities.description`; 권리 확인 범위 내 작성 |
| `organizer` | 필수 | `opportunities.organizer`; 확인되지 않으면 publish 보류 |
| `target_audience` | 선택 | `opportunities.target_audience` |
| `difficulty` | 선택 | `beginner/intermediate/advanced/all` 또는 null |
| `format` | 선택 | `online/offline/hybrid` 또는 null |
| `region`, `venue` | 선택 | 각 서비스 컬럼 |
| `price_type` | 선택 | `free/paid/mixed/unknown` 또는 null; 불명확하면 `unknown` 가능 |
| `price_text` | 선택 | 참가비 표시 문자열; 공개 화면의 fallback을 고려해 근거 있을 때 제공 |
| `application_start_at`, `application_deadline_at` | 선택 | 별도 `timestamptz` 값. 시간대 있는 ISO 8601; 시작≤마감 |
| `event_start_at`, `event_end_at` | 선택 | 별도 `timestamptz` 값. 시간대 있는 ISO 8601; 시작≤종료 |
| `official_url` | 필수 | `opportunities.official_url`; 유효한 공식 원문 URL. 수집 URL과 다를 수 있음 |
| `source_name`, `source_homepage_url` | 필수/선택 | Adapter가 서비스 `sources`를 조회·해결해 `source_id` 설정. `source_name` 필수, 홈페이지 선택 |
| `external_id` | 선택 | 동일 출처 내 안정적인 외부 식별자; `(source_id, external_id)` 유일 제약 사용 |
| `canonical_url` | 선택 | Agent 중복 판단용; 현재 서비스 컬럼 없음 |
| `image_url` | 선택 | `opportunities.image_url`; 권리 확인된 경우만 사용 |
| `tag_slugs` | 선택 | 기존 서비스 `tags.slug`에 우선 매칭. 신규 태그 자동 생성은 PHASE 2 범위 밖 |
| `verified_at` | 필수 | `opportunities.last_verified_at`; 실제 확인 시각 |
| `verification_status`, `confidence`, `duplicate_result` | 필수 | gate 판단용. 서비스 DB에는 직접 대응 컬럼 없음; `VERIFIED`, 중복 아님, 설정 임계치 이상일 때만 진행 |
| `evidence_refs`, `discovered_at`, `collected_at` | 필수/선택 | Agent 측 출처·검증 근거; 서비스 DB에 원문 기록하지 않음. `evidence_refs` 필수, 시각은 가능한 경우 유지 |

서비스 DB의 `slug`는 영문 소문자·숫자·하이픈 형식이며 유일해야 한다. Agent 계약에는 고정 서비스 slug를 필수로 두지 않고 Adapter가 안정적으로 생성·충돌 해결한다. `status`, `is_featured`, `published_at`, `category_id`, `source_id`도 Adapter가 서비스 규칙에 따라 결정한다. 공개 상태는 LLM이 아닌 날짜·상태 기반의 결정적 규칙으로 계산한다. 정책 인터페이스 `PublicationStatusResolver`만 정의했으며 실제 규칙과 원자적 기록 방식은 이후 구현한다.

## 3. Service DB로의 최소 게시 조건

1. `verification_status = VERIFIED`, 중복 아님, 임계치 이상의 confidence, 필수 필드와 공식 URL 유효성 통과.
2. `vertical`이 서비스 카테고리 slug에 매핑됨. 현재 시드에는 `conference`가 있다.
3. 서비스 `sources`에서 출처를 결정할 수 있음. Agent Source Registry에는 새 출처 자동 등록을 허용하지만 서비스 `sources` 자동 생성·갱신 권한은 별도 결정 전까지 가정하지 않는다.
4. 날짜 역전이 없고 DB CHECK/NOT NULL/유일 제약을 만족함.
5. 기존 서비스 레코드와의 idempotent 매칭을 수행하고, 신규/갱신/충돌/보류를 구분함. 동일 제목만으로 덮어쓰지 않는다.
6. 기존 태그가 있으면 매칭하고, 없는 태그는 자동 생성하지 않는다. 처리 방식은 게시 정책으로 확정한다.
7. `opportunities` 및 `opportunity_tags` 기록 결과를 Agent publish queue에 남기고 재시도 시 중복 공개되지 않도록 함.

## 4. 현행 읽기 계약 (변경 금지)

| API | 반환 형태 / 조건 | Agent가 충족해야 할 것 |
| --- | --- | --- |
| `GET /api/opportunities` | `{count, opportunities:[...]}`; 상태 `scheduled/open/ongoing`; `is_featured` 및 신청 마감일 순 정렬 | 공개 상태·카테고리·표시 필드가 채워져야 목록에 나타남 |
| `GET /api/opportunities/{slug}` | `{opportunity:{...}}`; 동일 상태 필터, 없으면 404 | 안정적인 slug, 상세 일정·공식 URL·검증 시각 |
| `POST /api/recommend` | `{count, recommendations:[{opportunity,reason}], message}`; 공개 기회 최대 30건을 후보로 사용 | 후보 데이터의 제목·요약·대상·일정·태그가 추천 판단에 충분해야 함. `reason`은 이 API의 산출물 |

목록 화면의 필터 값은 `categories.slug`, `status`, `format`, `price_type`과 직접 비교된다. 상세 화면은 `sources.name`, `last_verified_at`, `official_url`을 사용자에게 보여준다. 태그는 중첩 관계 `opportunity_tags[].tags.name`으로 반환된다. 필드별 실제 조회 목록은 `api/opportunities.py`의 각 `.select(...)`가 기준이다.

## 5. Adapter 출력과 실패 계약

권장 출력: `agent_item_id`, `service_opportunity_id`, `service_slug`, `action`(`created/updated/held/conflict`), `published_status`, `published_at`, `error_code`, `error_detail`. 성공·실패 모두 Agent의 publish queue에 기록한다. DB 쓰기 실패는 Agent의 검증 성공을 서비스 게시 성공으로 간주하지 않는다. 재시도는 동일 `agent_item_id`와 서비스 측 유일성 규칙을 기준으로 멱등이어야 한다.

## Resolved Decisions — PHASE 2

1. 공식 단계 순서는 `STAGE → VERIFY → PUBLISH/REVIEW/REJECT`다. 원문은 `RAW_STORE`에서 먼저 보존한다.
2. Agent Source Registry는 새 출처를 Agent 테이블에 후보로 자동 등록할 수 있게 설계한다. 이는 서비스 `sources` 자동 생성과 다르다.
3. 기존 서비스 태그와 우선 매칭한다. 신규 태그 자동 생성은 PHASE 2에서 하지 않는다.
4. `PublishCandidate`만 Service DB Adapter의 `publish()` 입력이다. `status`는 Adapter 쪽 결정적 정책으로 산출한다.

## Open Issues / Decisions Required

PHASE 7 결정과 미해결 사항: `docs/SERVICE_PUBLISH.md`의 Adapter는 기존 Service Source/Category만 재사용하고 신규 Source/Tag를 자동 생성하지 않는다. `date` 전용 Service 컬럼이 없어 `DATE_ONLY`는 게시 보류하며, 행사 및 입력된 접수 기간 각각 명시적 시각과 timezone 근거가 확인된 `DATETIME_CONFIRMED/CONFIRMED`만 datetime 매핑한다. 기존 Tag는 읽어 매칭하지만 이번 단계의 단일 Opportunity INSERT에는 관계 행을 쓰지 않아 Dry Run에 생략을 명시한다. 운영에서 date-only 공개가 필요하면 Service schema와 Backend/API 표시 계약을 함께 승인해야 한다. 기존 5건은 READY queue가 없어 실제 Service 게시 경로가 검증되지 않았다.

1. Agent Registry와 서비스 `sources`의 매칭 키·서비스 출처 생성 권한·기존 메타데이터 갱신 범위.
2. 신규 Agent가 게시할 때 이미 존재하는 서비스 레코드의 갱신 허용 범위 및 충돌 해결 정책. 기존 Collect 병행 운영은 신규 아키텍처 설계 범위에 포함하지 않는다.
3. 핵심 날짜가 없으면 임의 추론하지 않는다. 모집 마감이 지난 콘퍼런스의 공개 상태·보류 세부 규칙은 후속 게시 단계에서 정해야 한다. 기본 publish threshold는 0.85이며 설정 가능하다.
4. 미등록 `tag_slugs`의 처리(무시 또는 검수), 서비스 DB에 없는 미래 vertical(`grant/research/job` 등)의 매핑.
5. `official_url`과 `canonical_url`이 다를 때 향후 원문 변경 추적·별도 provenance 노출 여부.
