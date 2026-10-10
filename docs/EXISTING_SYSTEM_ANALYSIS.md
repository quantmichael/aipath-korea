# Existing System Analysis — PHASE 1

기준: 루트 `CODEX_TASKS.md`. 분석 범위는 저장소의 소스와 SQL이며, 운영 중인 Supabase 프로젝트의 실제 스키마·데이터·설정은 조회하지 않았다. 이 문서는 현재 동작의 근거를 기록하고 신규 Agent Collect 설계를 위한 경계를 정한다. PHASE 2 결정 및 구현 상태는 아래에 반영했다.

## 1. 디렉터리 구조와 실행 경로

| 위치 | 현재 역할 | PHASE 1에서의 취급 |
| --- | --- | --- |
| `public/` | HTML/CSS/Vanilla JS 정적 서비스, 관리자 후보 화면, 디지털 명함 | 화면과 API 소비 계약 분석 |
| `api/opportunities.py` | 단일 FastAPI 앱: 공개 조회, 추천, 기존 수집·관리 API | 공개 API의 조회 조건과 응답 분석 |
| `supabase/schema.sql`, `seed.sql`, `migrations/` | 서비스 DB 선언, 초기 카테고리·태그·출처, 기존 수집용 마이그레이션 | 서비스 DB 계약 분석. 실행·수정하지 않음 |
| `collect/`, `scripts/` | 기존 수집, 검수, 승격 및 CLI | 신규 구현의 기반으로 사용하지 않음. 현재 API 연결 사실만 기록 |
| `docs/` | 서비스 기획, 수집 대상, 제출 자료 | PHASE 1 설계 문서 위치 |
| `tests/` | 현재 수집 관련 단위 테스트 1개 | 기존 테스트 범위 확인 |
| `requirements.txt`, `pyproject.toml`, `vercel.json`, `.env.example` | 의존성, Python 범위, 배포 설정, 환경변수 예시 | 운영 제약 확인 |

정적 페이지는 Vercel에서 제공하고, `pyproject.toml`의 엔트리포인트는 `api.opportunities:app`이다. `vercel.json`은 해당 함수의 최대 실행 시간을 60초로 설정하고 `/api/collect/run`을 하루 두 번 호출하는 cron을 선언한다. 이 cron은 현재 기존 Collect로 연결된다. 새 Agent의 배포·스케줄은 아직 정하지 않았다.

## 2. Frontend와 실제 데이터 소비

| 화면/코드 | 데이터 경로 | 실제 소비 필드 |
| --- | --- | --- |
| `public/index.html` | 정적 카테고리 링크 | `category` 쿼리값: `education`, `competition`, `hackathon`, `meetup` |
| `public/opportunities.html`, `public/js/opportunities.js` | `GET /api/opportunities` | `title`, `slug`, `summary`, `organizer`, `target_audience`, `application_deadline_at`, `format`, `price_text`, `price_type`, `status`, `categories.name/slug`, `opportunity_tags[].tags.name` |
| `public/opportunity.html`, `public/js/opportunity.js` | `GET /api/opportunities/{slug}` | 목록 필드 중 `title`, `summary`, `status`, `categories.name`, `organizer`, `format`, `price_text`, `opportunity_tags` 및 `description`, `target_audience`, `application_start_at`, `event_start_at`, `event_end_at`, `region`, `venue`, `difficulty`, `sources.name`, `last_verified_at`, `official_url` |
| `public/recommend.html`, `public/js/recommend.js` | `POST /api/recommend` | 입력: `level`, `interest`, `goal`, `format`, `price`, `available_period`; 출력: `count`, `message`, `recommendations[].reason`, `recommendations[].opportunity.{title,slug,summary,categories.name}` |
| `public/admin/candidates.html`, `public/js/admin-candidates.js` | 기존 `GET /api/collect/candidates`, `POST /api/collect/candidates/action` | 후보 `id`, `candidate_status`, `title`, `summary`, `organizer`, `target_audience`, `application_start_at`, `application_deadline_at`, `official_url`, `validation_errors`, `sources.name`, `categories.name`; `verify/hold/reject/publish` 요청 |
| `public/about.html`, `public/card/index.html` | 정적 콘텐츠 | 기회 DB 필드 사용 없음 |

목록의 검색·필터는 브라우저에서 실행된다. 카테고리, 상태, 참여 방식, 가격은 화면의 고정 옵션과 DB 값이 일치해야 한다. `closed`는 화면 필터 옵션이지만 공개 목록 API의 조회 상태에는 포함되지 않는다. `recommend`는 현재 공개 상태의 기회만 최대 30개 조회해 AI에 전달하고, AI가 반환한 slug를 후보 목록으로 다시 검증한다. 추천 이유는 Agent Collect의 publish 계약이 아니라 추천 API가 생성한다.

브라우저는 Supabase에 직접 접속하지 않는다. 관리자 화면은 현재 `X-Collect-Secret` 헤더를 보내며 비밀값을 브라우저 `localStorage`에 보관한다. 신규 Agent의 검수 UI와 인증 방식을 이 화면이 자동 제공한다고 가정할 수 없다.

## 3. Backend/API 구조

`api/opportunities.py`가 다음 경로를 제공한다.

| 메서드·경로 | 현재 동작 |
| --- | --- |
| `GET /api/opportunities` | `opportunities`에서 `scheduled`, `open`, `ongoing`만 읽고 카테고리·출처·태그 관계를 포함해 반환 |
| `GET /api/opportunities/{slug}` | 동일한 공개 상태 제한, slug 단건 조회; 없으면 404 |
| `POST /api/recommend` | 공개 상태 후보 최대 30건을 조회하고 OpenAI 구조화 응답을 slug 기준으로 검증해 최대 3건 반환 |
| `GET /api/collect/run` | 인증 후 기존 `collect.service` 실행 |
| `POST /api/collect/promote` | 인증 후 기존 후보 승격 로직 실행 |
| `GET /api/collect/candidates` | 인증 후 기존 `opportunity_candidates` 조회 |
| `POST /api/collect/candidates/action` | 인증 후 기존 후보 상태 변경/공개 |

API는 서버 측 `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `OPENAI_API_KEY`를 사용한다. 기존 수집 API는 `COLLECT_SECRET` 또는 `CRON_SECRET`으로 보호된다. `api/opportunities.py`가 기존 `collect` 모듈을 import한다는 사실은 현행 서비스 구성의 일부이지만, 새 `agent_collect`가 이 모듈이나 기존 `collect`를 import할 이유가 되지 않는다.

## 4. 현재 DB 모델과 관계

근거: `supabase/schema.sql` 및 초기 값 `supabase/seed.sql`. 아래의 `collection_runs`와 `opportunity_candidates`는 현재 서비스 DB 안의 **기존 Collect용 테이블**이다. 새 Agent가 요구하는 같은 이름의 개념과 구분한다.

| 테이블 | 핵심 역할·관계 |
| --- | --- |
| `sources` | 출처 표시와 서비스 측 FK 대상. `name` 유일. 수집 방식·이용조건·최근 수집 상태 포함 |
| `categories` | 공개 카테고리. `slug` 유일. `opportunities.category_id`가 필수 참조 |
| `opportunities` | 공개 서비스의 중심 테이블. `source_id`는 선택 FK, `category_id`는 필수 FK. `slug` 유일, `(source_id, external_id)`는 둘 다 존재할 때 유일 |
| `tags` | 태그 사전. `slug` 유일 |
| `opportunity_tags` | 기회와 태그의 다대다 연결. `(opportunity_id, tag_id)` PK |
| `collection_runs` | 기존 Collect 실행 이력. `sources`에 선택 FK |
| `opportunity_candidates` | 기존 Collect 후보. `sources`, `categories`, `collection_runs`에 선택 FK; `(source_id, external_id)`와 `official_url` 유일 인덱스 |

`opportunities`는 `title`, `slug`, `summary`, `organizer`, `official_url`, `category_id`, `status`가 필수다. 날짜 필드는 `timestamptz`이며 신청 시작≤마감, 행사 시작≤종료 제약이 있다. `difficulty`, `format`, `price_type`, `status`는 CHECK 값이 제한된다. `status`의 허용값은 `draft/scheduled/open/closed/ongoing/ended/cancelled`이나 공개 API는 앞의 공개 상태 세 개만 읽는다. `sources`와 `categories` 관계는 응답의 중첩 객체로 노출된다. `last_verified_at`은 상세 화면에 표시된다.

브라우저 역할(`anon`, `authenticated`)의 직접 테이블 접근은 철회되어 있고 RLS가 활성화되어 있다. 현재 DB 기록은 서버 측 서비스 권한이 필요하다. 서비스 DB가 Agent 전용 원문·판단 로그의 저장소라는 근거는 없다.

## 5. 새 시스템의 경계

- 새 Agent는 독립적인 루트 `agent_collect/`를 권장한다. `collect/`, `scripts/collect.py`, 기존 API 함수와 기존 DB 후보 테이블에 import/호출 의존성을 두지 않는다.
- Agent 내부 source registry, raw document, staging, verification, logs는 서비스의 `sources`, `opportunity_candidates`, `collection_runs`와 별도 소유로 설계한다. 동일한 이름이어도 동일 테이블로 간주하지 않는다.
- 서비스에 대한 결합점은 버전이 있는 publish 계약과 `Service DB Adapter` 하나다. Adapter가 카테고리·출처·태그를 서비스 식별자로 해석하고 `opportunities` 및 관계를 기록한다.
- 기존 공개 API가 읽는 스키마·상태·응답을 유지한다. 신규 Agent의 관측·검수 화면은 별도 설계가 필요하다.

## Resolved Decisions — PHASE 2

1. Agent 패키지는 루트 `agent_collect/`에 둔다. 기존 Frontend·Backend·Collect는 변경하지 않는다.
2. Agent 전용 데이터는 현재 Supabase 프로젝트의 별도 `agent_` 테이블로 논리적으로 분리한다. 마이그레이션 파일은 `agent_collect/migrations/001_agent_foundation.sql`이며 아직 운영 DB에는 적용하지 않았다.
3. 서비스 DB 결합은 `agent_collect/adapters/service_db/`로 제한한다. `PublishCandidate`만 이 경계를 넘는다.
4. 순서는 `DISCOVER → SOURCE_ANALYZE → COLLECT → RAW_STORE → EXTRACT → NORMALIZE → CLASSIFY → DEDUP → STAGE → VERIFY → PUBLISH/REVIEW/REJECT`로 확정했다.
5. 서비스 공개 상태는 날짜와 상태 정보의 결정적 규칙으로 계산한다. PHASE 2에는 정책 인터페이스만 정의했고 판정 함수·실제 게시 기능은 구현하지 않았다.

## Open Issues / Decisions Required

1. **운영 DB 확인**: SQL 파일은 선언일 뿐이다. 실제 Supabase 테이블, 시드 값, 권한, 기존 데이터 충돌 여부는 마이그레이션 적용 전에 읽기 검증이 필요하다.
2. **기존 데이터와의 충돌**: Agent Collect를 향후 메인 수집 시스템으로 설계하며 기존 Collect 병행 운영·마이그레이션은 다루지 않는다. 향후 서비스 DB 게시 때 이미 존재하는 기회와의 충돌 규칙은 필요하다.
3. **검수 UI**: 현재 관리자 화면은 기존 후보 테이블 전용이다. `REVIEW_REQUIRED`는 PHASE 3에서 저장만 하며 향후 검수 인터페이스는 미정이다.
4. **공개 시점의 세부 규칙**: 날짜 누락, 접수 마감, 행사 진행 중을 각각 어떤 공개 상태 또는 보류로 판정할지 결정해야 한다.
