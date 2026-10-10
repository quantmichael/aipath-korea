# Agent Collect Discovery Engine — PHASE 4

## 범위와 현재 상태

Discovery는 새 출처를 찾고 평가해 **Agent Source Registry의 비활성 후보**로 승격시키는 독립 계층이다. SearchResult는 검색 서비스가 반환한 URL과 메타데이터일 뿐 Source가 아니다. 자동 수집, 행사 추출, 서비스 DB 게시는 하지 않는다. 003은 이전 단계에서 적용됐다. PHASE 4.5 FINAL에서 Tavily Provider·DB 기반 예산 Guard를 구현하고 실제 검색 1회로 Query→Result→Candidate를 검증했다. Source 승격은 조건 미충족으로 0건이다. 자세한 결과는 `docs/DISCOVERY_RUNTIME.md`를 따른다.

PHASE 7.3B부터 승격 Source에는 `discovery_officiality_signals`와 `discovery_signal_is_approval=false`를 명시적으로 남긴다. 공식성 *신호*는 사람의 공식성·수집 허용 검토나 승인 기록을 대체하지 않는다. `CANDIDATE → REVIEW → APPROVED → ACTIVE`는 별도 수동 계약이며, 004 migration은 2026-10-04 적용·검증됐다. `docs/SOURCE_APPROVAL.md` 참조.

```text
DiscoveryQuery → SearchBudgetGuard → SearchProvider → SearchResult(최소 정보)
  → 결정적 URL/중복 필터 → SourceCandidate(근거 보존)
  → SourceEvaluator → PromotionGate → agent_sources(CANDIDATE, inactive)
                                             │
                                             └── 별도 Collection Runner가 이후 수집
```

## Query, Provider, 결과 정규화

`DiscoveryQuery`는 질의, provider 이름, vertical hint, 시각, 상태, 결과/후보/승격 수, 오류를 가진다. `SearchProvider.search(query)`는 공통 `SearchResult` 목록을 반환한다. `SearchHit → normalize_hit`은 title, original URL, 짧은 snippet, rank, provider, query ID, 발견 시각 및 가능한 경우 canonical URL/domain을 기록한다. 잘못된 URL도 SearchResult로 남겨 필터 사유를 추적한다. Provider가 반환한 query/provider가 실행과 다르면 Runner는 실패시킨다. `TavilySearchProvider`는 공식 API 응답을 이 최소 모델로 변환하며 응답 전체나 page raw content를 저장하지 않는다. `AGENT_TAVILY_API_KEY`만 환경에서 받는다. 검색 엔진 HTML scraping은 사용하지 않는다.

`FakeSearchProvider`는 주입된 `SearchHit`만 반환하고 네트워크를 사용하지 않는다. Tavily Runner는 Provider 직전에 영속 DB 시도 이력으로 일 30·월 1000·동일 질의 24시간 cooldown을 검사한다. 월 800은 WARNING, 950은 CRITICAL, 1000은 HARD STOP이며 날짜 경계는 Asia/Seoul이다. 차단·자격증명 누락·비활성 상태에서는 Tavily API를 호출하지 않는다. 설정과 동시성 한계는 `docs/DISCOVERY_RUNTIME.md`에 있다. 무제한 검색, 브라우저 자동화, Scheduler는 범위 밖이다.

## 필터, 후보, 평가

필터는 기존 `validate_target_url`과 `canonicalize_url`을 재사용한다. 절대 HTTP(S) URL, credential 금지, localhost·사설 IP literal 금지, 명백한 파일 확장자 제외, canonical URL 중복, 기존 Source 및 기존 후보 canonical 중복을 검사한다. **같은 domain의 다른 URL은 SearchResult·SourceCandidate에서 제거하지 않고 힌트만 남긴다.** 한 Runner 실행에서는 동일 domain의 Source 중복 승격을 막는다. 이는 네트워크 없는 사전 필터이며 실제 HTTP 수집 시 기존 `SafeHttpTransport`의 DNS/redirect 방어가 계속 필요하다. Query 간 도메인 유일성은 아직 운영 결정이 필요하다.

필터 통과 시 `SourceCandidate`를 생성한다. 후보 상태는 `EVALUATION_PENDING → ACCEPTED / REVIEW_REQUIRED / REJECTED → (승격 가능하면) PROMOTED`다. `SourceEvaluator` 계약은 `SourceEvaluation(decision, confidence, reasons, evidence, suggested_method)`를 반환한다. 기본 `RuleBasedEvaluator`는 AI/vertical 텍스트 신호와 **운영자가 설정한** 공식 도메인 힌트를 사용한다. 공식 힌트가 없으면 자동 ACCEPT하지 않고 REVIEW한다. 규칙 기반 confidence는 발견 우선순위 신호일 뿐 공식성의 확증이나 행사 검증 결과가 아니다. 향후 LLM 평가기를 같은 계약 뒤에 넣을 수 있지만, 그 결과 역시 근거·확신도를 기록하고 별도 정책을 거쳐야 한다.

## Promotion Gate와 provenance

Gate는 후보 `ACCEPTED`, 평가 `ACCEPT`, 후보/평가 ID·confidence 일치, 설정 임계치(`AGENT_DISCOVERY_PROMOTION_THRESHOLD`, 기본 0.90), 공식 출처·관련성 신호와 증거, 기존 canonical Source 부재를 요구한다. Registry의 `canonical_url` unique가 최종 중복 방어다. 통과 시 `SourceRegistryService.register`로만 저장하며 Source는 `CANDIDATE`, `is_active=false`, 기본 `http_html` 방법이다. **활성화와 HTTP 수집은 별도 작업**이다. Source provenance에는 query/result/candidate ID, 원본/후보/canonical URL, provider/rank/query, 평가 근거와 증거를 유지한다. SearchResult와 SourceCandidate는 각각 별도 Agent 테이블에 남긴다.

여러 테이블에 걸친 Runner 작업은 현재 단일 DB 트랜잭션이 아니다. Source 등록 직후 후보 갱신 실패, 동시 승격 시 unique 충돌, 다른 질의의 동일 도메인 후보가 남는 경우를 운영 전에 재조정해야 한다. 재시도 시 이미 등록된 canonical Source는 재등록하지 않으며, 이미 저장된 후보 canonical URL도 필터한다. 자동 Source 삭제/덮어쓰기는 하지 않는다.

## 저장소와 마이그레이션

`003_discovery_foundation.sql`은 Agent 전용 `agent_discovery_queries`, `agent_search_results`, `agent_source_candidates` 3개를 생성한다. Query→Result→Candidate 복합 FK, canonical 후보 unique, query/provider/rank unique, 상태/시각/CHECK/인덱스, Agent RLS 및 service_role 권한을 포함한다. 후보의 `promoted_source_id`만 기존 **Agent** `agent_sources`를 참조한다. 이전 단계에서 적용·확인됐고 이번 단계에는 migration 변경이 없다. 서비스 테이블·001/002는 변경하지 않았다.

## 운영 경계와 다음 연결점

Discovery Runner는 수동 호출 가능한 application service다. `AGENT_DISCOVERY_ENABLED`의 기본값은 false이며 현재 CLI에 Discovery 명령을 추가하지 않았다. Registry에 승격된 Source는 기존 Collection Planner가 이해하는 `http_html` 구조이지만, Collection Runner를 Discovery 내부에서 호출하지 않는다. 향후 Scheduler는 별도 계층에서 query 실행·Source 활성화·Collection 실행을 조정한다. 행사 추출·검증·Publish Gate와 서비스 DB 쓰기는 후속 단계다.
