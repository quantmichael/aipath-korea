# Discovery Runtime — PHASE 4.5 FINAL (2026-10-03)

## 003 적용 및 실DB 검증

로그인된 `aipath-korea` Supabase Dashboard SQL Editor에서 `003_discovery_foundation.sql`을 **한 번 적용**했다. 사전 확인에서 `public` CREATE 권한, `gen_random_uuid()`, 기존 Agent 7개 테이블을 확인했고 새 테이블·인덱스 이름 충돌은 없었다. 편집기에 입력한 SQL의 SHA-256은 로컬 003 파일과 일치했다. 결과는 `Success. No rows returned`였다. 사후 catalog에서 Discovery 테이블 3개, RLS 3개, FK 4개, unique 제약 4개, 인덱스 12개(기본·unique 포함)를 확인했다. 세 테이블 모두 REST HTTP 200이다. 서비스 테이블 7개/106개 컬럼의 스키마 기준 해시는 적용 전후 `38746c5ff5148c4c5d7e45682b219a9d`로 같았다. 001/002와 Service Table은 변경하지 않았다.

이전 단계에서 `SupabaseDiscoveryRepository`의 `runtime_discovery_smoke_test` Query(`a761e439-7f1c-4099-9cd8-39b30aca72a1`), SearchResult(`eef7dfdf-d8f7-4bfb-8bf3-759cda73a484`), SourceCandidate(`ccfb400e-c770-4792-9302-9ff054431807`) CRUD와 Query→Result→Candidate 관계를 검증했다. 이 3행은 감사용으로 유지한다. 당시에는 Fake Provider만 사용했고 Source 승격은 없었다. 기존 Service Data 쓰기·삭제는 없다.

## Tavily Provider와 비밀 설정

`TavilySearchProvider`는 [공식 Search API](https://docs.tavily.com/documentation/api-reference/endpoint/search)의 POST `/search`를 Bearer 인증으로 호출한다. `AGENT_TAVILY_API_KEY`는 환경에서만 주입하며 값은 출력·저장하지 않는다. 기본 요청은 `topic=general`, `search_depth=basic`, `max_results=5`, `timeout=10s`다. answer/raw content/images를 요청하지 않고, 응답 전체를 장기 저장하지 않는다. title, URL, 500자 이하 snippet, rank, provider, query ID, 발견 시각, canonical URL만 남긴다. 실제 페이지 원문은 후속 Generic Collection Engine의 책임이다. 인증, timeout, 429/Retry-After, HTTP, 응답 형식, 네트워크 오류는 안전한 코드로 표현한다. 빈 결과는 성공 0건이고 자동 재시도는 없다.

## 수동 Query·rate limit·승격

자동 질의 생성·Scheduler·Daemon은 없다. `AgentRuntime.discovery_runner(provider)`는 Runner를 조립하지만 생성만으로 검색하지 않는다. Tavily Runner는 호출 직전 DB 기반 Search Budget Guard를 반드시 검사한다. `SearchProvider.search(query, policy=...)` 기본값은 `max_results=5`, `timeout_seconds=10`이다. `AGENT_DISCOVERY_MAX_RESULTS`는 1~20, `AGENT_DISCOVERY_REQUEST_TIMEOUT_SECONDS`는 양수여야 한다. Runner도 반환 수 초과를 실패로 기록한다. Provider 예외 원문 대신 안전한 코드/타입만 Query에 기록한다.

같은 domain의 다른 URL은 SearchResult와 SourceCandidate로 보존하며 `same_domain_as_prior_result` 힌트를 남긴다. 한 실행에서 같은 domain의 Source를 두 번 승격하지 않는다. Query 간 domain 수준 Source 유일성은 아직 미해결이다. 자동 승격 기본 threshold는 **0.90**이며 후보 `ACCEPTED`, 평가 `ACCEPT`, canonical 유효성·유일성, 공식 출처와 AI/vertical 관련성 신호, 증거를 함께 요구한다. 기본 평가기는 운영자가 `AGENT_DISCOVERY_OFFICIAL_DOMAIN_HINTS`로 준 domain에만 공식 힌트를 부여한다. 힌트가 없으면 자동 ACCEPT하지 않는다. 승격 Source는 `CANDIDATE`, `is_active=false`이고 Collection은 시작되지 않는다. 실검색에서 Source 승격이 없었으므로 **실DB Source 승격 provenance는 여전히 미검증**이다.

## Search Budget Guard

기존 `agent_discovery_queries`에서 `started_at IS NOT NULL`인 Tavily 질의를 시도 1건으로 계산한다. 성공·실패·진행 중 모두 포함하며 차단 요청(`status=FAILED`, `started_at=NULL`, `provenance.budget_blocked=true`)은 사용량에서 제외한다. DB 조회 실패 시 fail-closed로 호출하지 않는다. 동일 질의는 공백 정리·casefold 후 비교한다. 일간/월간 경계는 설정 가능한 `Asia/Seoul` 기준이다. 003 제약상 실행 전 차단된 질의의 `completed_at`도 NULL이다.

| 정책 | 기본값 | 환경변수 |
| --- | ---: | --- |
| 일간 상한 | 30 | `AGENT_DISCOVERY_DAILY_SEARCH_LIMIT` |
| 월간 상한·HARD STOP | 1000 | `AGENT_DISCOVERY_MONTHLY_SEARCH_LIMIT` |
| 동일 질의 cooldown | 24시간 | `AGENT_DISCOVERY_QUERY_COOLDOWN_HOURS` |
| 월간 WARNING | 800 | `AGENT_DISCOVERY_MONTHLY_WARNING_THRESHOLD` |
| 월간 CRITICAL | 950 | `AGENT_DISCOVERY_MONTHLY_CRITICAL_THRESHOLD` |
| 날짜 경계 | Asia/Seoul | `AGENT_DISCOVERY_BUDGET_TIMEZONE` |

차단 사유는 `DAILY_LIMIT_REACHED`, `MONTHLY_LIMIT_REACHED`, `QUERY_COOLDOWN`, `PROVIDER_DISABLED`, `MISSING_CREDENTIAL`이다. 경고 수준은 Guard 반환값과 Query provenance의 `budget_level_before`(허용) 또는 `budget_level`(차단)에서 확인한다. 조회→판정→RUNNING 저장은 **원자적 분산 예약이 아니므로 동시 다중 워커의 절대 상한은 보장하지 않는다**. 단일 수동 워커만 현재의 안전한 운영 가정이다. 004 migration은 생성하지 않았다.

## 실제 Tavily → Supabase E2E

모의 테스트 69개 통과 후 `AI conference Korea`로 Tavily API를 **정확히 1회** 호출했다. Query `8713a7eb-4c9a-4465-aa69-342a8c60f86c`는 `SUCCEEDED`, Result 5건, Candidate 5건, Source 승격 0건이다. 후보 전부 공식성 근거 부족으로 `REVIEW_REQUIRED`, confidence 0.55였고 강제 승격하지 않았다. 실제 Supabase에서 각 Result의 `query_id`와 각 Candidate의 `query_id/search_result_id` 관계를 확인했다. `runtime_tavily_discovery_smoke_test` marker와 함께 감사용으로 유지한다. 동일 질의의 공백·대소문자 변형 Query `13c5572c-50c5-400a-9d09-a199b622e0d9`는 DB 이력을 읽어 `QUERY_COOLDOWN`으로 차단·저장됐고 Tavily 호출은 0회였다. 이후 일간/월간 사용량은 각각 1건, 수준은 NORMAL이었다.

## 운영 전 남은 문제

- Tavily 계정의 실제 크레딧 잔액·요금은 별도 대시보드에서 확인해야 한다. Guard의 시도 건수와 API 청구 크레딧을 동일시하지 않는다.
- 공식 도메인 힌트의 승인 근거와 수동 검토 책임자 결정. 병렬 워커 운영 전 원자적 예산 예약 필요 여부 결정.
- 여러 Query에 걸친 동일 사이트 Source 정체성·중복 정책, 병렬 승격 충돌·실패 재조정.
- Source 등록과 후보 `PROMOTED` 갱신은 단일 DB 트랜잭션이 아니다.
- 남겨 둔 이전 Fake smoke 3행과 이번 Tavily smoke Query/Result/Candidate 및 cooldown 차단 Query를 정리할 때는 marker와 정확한 ID를 재확인한 후 Agent 후보→결과→질의 순으로만 삭제한다. 지금은 provenance 검증용으로 유지한다.
