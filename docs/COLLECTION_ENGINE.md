# Agent Collect Collection Engine — PHASE 3

PHASE 8 addendum (2026-10-04): `LocalRawContentStorage` remains the default for development, while `AGENT_RAW_STORAGE_PROVIDER=supabase` selects private persistent `agent-raw` Storage. New RawDocuments record a retention expiry in Agent provenance; existing rows are not rewritten. The private bucket and cross-process readback were live-tested. This addendum supersedes local-only operational statements below; see [RAW_STORAGE.md](RAW_STORAGE.md).

이 문서는 현재 구현된 **LLM 없는 원문 수집 단계**를 설명한다. 처리 범위는 Source Registry → Repository → Collection Planner → Generic Collector → Raw Content Storage → Run/RawDocument 기록이다. Opportunity 추출·검증·게시 기능은 아직 없다.

## Source Registry

`SourceRegistryService`는 `SourceRepository`를 통해 후보 출처를 등록·조회·수정하고 활성화/비활성화한다. 등록 시 URL의 scheme·host·기본 포트·경로·query를 정규화하고 fragment 및 일반 추적 파라미터를 제거한다. `canonical_url`로 중복을 찾으며 DB에도 유일 제약이 있다. 동일 URL의 재등록은 기존 Source와 `created=false`를 반환한다. 활성화에는 collection method가 필요하다.

Source의 설정은 `collection_config` 데이터다. 현재 Runner가 사용하는 키는 `request_url`, `headers`, `header_env`, `query_params`다. `header_env`는 헤더 이름에서 환경변수 이름으로의 매핑이며 실제 값은 실행 시 읽는다. Registry 설정에는 비밀값을 저장하지 않는 것을 전제로 한다. Source별 Python Collector 파일은 만들지 않는다. 향후 자동 Discovery는 후보 Source를 같은 `register()` 경로로 등록할 수 있다.

Repository 포트는 `SourceRepository`, `CollectionRunRepository`, `RawDocumentRepository`다. 구현은 `InMemory*Repository`와 주입형 `Supabase*Repository`가 있다. Supabase 구현은 Agent 전용 `agent_sources`, `agent_collection_runs`, `agent_raw_documents`만 사용한다. PHASE 3.5에서 `agent_collect/runtime.py`가 클라이언트/자격증명 조립을 담당한다. 단위 테스트는 인터넷·실제 DB 없이 실행된다. PHASE 3.6에서 Agent 마이그레이션 001/002를 실제 적용하고 7개 테이블의 REST 조회(HTTP 200)를 확인했다. 실제 Repository CRUD와 DB E2E는 아직 미검증이다. 자세한 결과는 `docs/RUNTIME_INTEGRATION.md`에 있다.

## Collection Method와 Planner

초기 방법은 `HTTP_HTML`, `RSS`, `REST_API`다. PHASE 2의 `HTTP`·`HTML` 값은 Planner에서 같은 `HtmlCollector`로 해석하며 Registry의 신규 설정은 `HTTP_HTML`로 정규화한다. `SITEMAP`, `SEARCH`, `BROWSER`는 enum·DB 값만 예약하고 선택 시 `UnsupportedCollectionMethodError`를 반환한다. Planner는 Source 메타데이터만 읽어 Collector를 고르며 LLM 호출이 없다.

| 방법 | Collector | 출력 메타데이터 |
| --- | --- | --- |
| `HTTP_HTML` | `HtmlCollector` | 페이지 제목 |
| `RSS` | `RssCollector` | RSS/Atom feed 제목·링크, item 수, 최대 100개 item의 기본 식별 정보 |
| `REST_API` | `ApiCollector` | JSON 최상위 자료형·키 또는 배열 길이 |

RSS item이나 JSON 응답은 Opportunity로 변환하지 않는다. 원문 bytes를 보존하고 메타데이터만 기록한다.

## 공통 Collector 계약

입력은 `Source`와 `CollectionRequest`(URL, GET query, 헤더, timeout, 최대 응답 크기, redirect/retry 제한, user agent)다. 출력 `CollectionResult`는 `source_id`, `requested_url`, `final_url`, HTTP 상태, content type, raw bytes, SHA-256, 수집 시각, 경과 시간, 메타데이터, 구조화 오류를 담는다. 실패 시 `error.code/message`가 있으며 결과를 성공으로 취급하지 않는다.

수집은 GET만 사용한다. timeout·DNS/HTTP 연결 오류 및 429/5xx에는 설정된 횟수만큼 지수 간격으로 재시도한다. 4xx, 잘못된 content type·본문·URL, 과도한 응답은 재시도하지 않는다. HTML은 Content-Type/charset과 `<meta charset>`을 고려해 제목만 분석한다. RSS/Atom은 XML 구조를 확인하며 DTD/entity 선언은 거부한다. JSON은 구문 확인 후 자료형 메타데이터를 남긴다.

## Raw Storage와 Provenance

`RawContentStorage`는 `put/get/delete/purge_expired` 포트다. `LocalRawContentStorage`는 날짜 디렉터리 아래 `SHA256-UUID.raw` 파일로 bytes를 저장하고 `local:...` 참조를 반환한다. 같은 본문이라도 파일명 충돌이 없으며 읽을 때 hash를 다시 확인한다. 기본 보존 기간은 90일이고 `AGENT_RAW_RETENTION_DAYS`로 변경할 수 있다. `purge_expired()`는 호출될 때만 관리 대상 파일을 삭제한다. 자동 정리 스케줄은 아직 없다.

DB에는 raw bytes를 넣지 않고 `agent_raw_documents.content_ref`·hash·수집 URL·HTTP 상태·시각·provenance를 저장한다. 연결은 `Agent Source → CollectionRun → RawDocument → content_ref`다. `agent_collection_runs.source_config_snapshot`에는 요청 URL과 헤더/query **이름**만 기록한다. 실제 secret header 값은 기록하지 않는다. `RawDocument.provenance`에는 요청/최종 URL, 방법, 경과 시간, Collector 메타데이터를 남긴다.

## Runner와 오류 처리

`CollectionRunner.run_source(source_id)`는 Source를 찾은 뒤 RUNNING 기록을 먼저 저장한다. 이후 활성 여부 확인 → Planner → Collector → Storage → RawDocument Repository → Source 최근 성공 시각 → SUCCEEDED Run 순서로 수행한다. 수집·계획·원문·메타데이터 단계 실패는 Run을 FAILED로 마감하고 사유를 저장한다. RawDocument DB 저장 실패 전에 생긴 로컬 파일은 삭제한다. RawDocument 저장 후의 실패에는 참조 가능한 bytes와 메타데이터를 보존하며 Run에 실패 사유를 남긴다. DB 자체가 쓰기 불가인 경우까지 기록 성공을 보장할 수는 없다.

수집 결과 하나는 RawDocument 하나로 저장된다. RSS feed의 개별 item 저장, 페이지네이션, 여러 링크 순회는 후속 단계다. Runner는 Source ID가 없는 경우 Run을 만들 수 없으므로 `KeyError`를 반환한다.

## HTTP 안전 경계

`validate_target_url()`은 HTTP(S) 외 scheme, malformed URL, URL 내 자격증명, localhost 계열, 사설·loopback·link-local IP를 거부한다. DNS 질의 결과에 공개 IP와 비공개 IP가 섞여 있으면 요청을 거부한다. 실제 연결은 확인한 IP로 고정하면서 원래 hostname을 Host/SNI와 TLS 인증에 사용한다. Redirect마다 URL과 DNS를 재검사하고 횟수를 제한한다. 출처가 바뀌는 Redirect에서는 인증·사용자 지정 헤더를 전달하지 않는다. 응답 크기와 압축 방식도 제한한다.

이는 수집기 기본 방어다. 배포 환경의 네트워크 egress 정책, 허용 출처 범위, robots/이용조건 검토는 별도 운영 설정으로 다뤄야 한다. 테스트는 가짜 DNS·dialer·HTTP transport를 사용해 인터넷 없이 실행한다.

## 설정과 사용 형태

비밀이 아닌 기본 정책은 `agent_collect/config/defaults.toml`에 있다. 검증 임계치 0.75, 게시 임계치 0.85, 원문 보존 90일을 포함하며 `AGENT_*` 환경변수로 덮어쓴다. LLM·Discovery·Publish flag는 기본 비활성이다. Collector/Runner는 실제 자격증명과 저장소 경로를 생성하지 않으며 호출자가 Repository·Storage·Planner를 주입한다.

```python
settings = load_settings()
client = create_client(agent_supabase_url, agent_service_key)  # 서버 환경에서만
runner = CollectionRunner(
    sources=SupabaseSourceRepository(client),
    runs=SupabaseCollectionRunRepository(client),
    documents=SupabaseRawDocumentRepository(client),
    storage=LocalRawContentStorage(raw_root, retention_days=settings.raw_retention_days),
    planner=CollectionPlanner(),
    settings=settings,
)
outcome = runner.run_source(source_id)
```

이 예시는 직접 조립 방식이다. PHASE 3.5부터 `python3 -m agent_collect` 수동 CLI가 있으며 사용법은 `docs/RUNTIME_INTEGRATION.md`에 있다. 스케줄러는 아직 없다.

## 향후 확장

Sitemap/Search/Browser는 Planner에 새 범용 Collector를 등록하고 공통 `CollectionRequest`/`CollectionResult`를 유지한다. 인증은 `header_env`에서 시작하되 OAuth 등이 필요하면 Runner의 요청 설정/credential provider를 확장한다. Raw 저장소는 같은 포트를 구현하는 Supabase Storage 또는 S3 호환 저장소로 교체한다. RSS item 분리·페이지네이션·발견·추출은 후속 PHASE에서 처리한다.
