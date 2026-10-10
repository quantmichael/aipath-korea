# Conference Extraction Engine — PHASE 5 / 5.5 (2026-10-04)

현재 구현은 ID 기반 Grounded Evidence v2다. PHASE 5의 인용문 기반 v1 결과와 PHASE 5.5의 분석·비교표는 [EVIDENCE_GROUNDING.md](EVIDENCE_GROUNDING.md)에 기록했다. 아래 PHASE 5 실제 Smoke 설명은 v1의 역사적 결과다.

PHASE 7.3A 보완: 현재 validator는 **연도·양 끝점이 명시되고 순서가 유효한** 동일 월/월 경계 날짜 범위만 해석한다. 실제 `L` ID 하나가 URL 필드의 block ID에 잘못 들어간 경우에도 나머지 ID를 확인한 뒤 실제 링크를 검증한다. 원래 모델 `document_confidence`와 검증 후 점수·0.49 clamp 여부를 provenance에 각각 보존한다. 이전 benchmark·기존 Candidate는 소급 수정하지 않았다. 세부 범위와 테스트는 [NORMALIZATION_ENGINE.md](NORMALIZATION_ENGINE.md).

PHASE 8 Budget 환경 구분: 명시적 `APP_ENV=development`는 기본 일 20·월 300, `production`·미설정·알 수 없는 값은 보수적 기본 일 5·월 100을 선택한다. 이는 기존 `ExtractionBudgetGuard`를 제거하거나 사용 기록을 초기화하지 않는다. 현재 연결된 Supabase `main PRODUCTION`에는 development 예산을 적용하지 않았다. [PROCESSING_WORKER.md](PROCESSING_WORKER.md)에 최종 런타임 검증 상태를 기록한다.

## 현재 Grounded Evidence v2

흐름은 `RawDocument → HTML 전처리·B/L ID 부여 → 중요 블록 선택 → 기존 Budget Guard → strict structured output → ID 해결 → 결정적 값/날짜/URL 검증 → agent_candidates`다. `ConferenceCandidate`의 20개 nullable 필드에 `evidence_block_ids`, `link_ids`, 코드가 생성한 `evidence_text`, `validation_status`를 추가했다. 모델은 근거 문장을 작성하지 않고 제공된 ID를 선택한다. 텍스트 값은 인용 블록 내 보수적 정규화 비교, 날짜는 인용 범위의 실제 연월일 대조, URL은 링크 ID의 안전한 href 해결을 통과해야 한다. Prompt는 `conference-grounded-2026-10-04-v2`, schema/extraction은 `conference-grounded-v2`다. 기존 v1 후보는 보존되며 새 fingerprint로만 v2를 생성한다. ICCAI 동일 RawDocument의 실제 1회 재호출에서 검증 필드가 0/20→9/20으로 개선됐지만 결과는 여전히 `REVIEW_REQUIRED`다. 자세한 근거와 한계는 연결 문서를 따른다.

## PHASE 5 v1 기준선 (역사적 기록)

### Extraction Architecture

`RawDocument → LocalRawContentStorage.get → deterministic HTML preprocessor → size/budget guard → ExtractionProvider → strict structured output → domain validation → field evidence/date/URL validation → agent_candidates` 순서다. Discovery·Collection과 독립적인 수동 `ExtractionRunner`이며 Service DB, Publish Gate, Verification Agent 또는 다른 vertical에는 접근하지 않는다. `agent_collect/runtime.py`가 Agent 전용 Repository와 Storage를 조립한다. Provider 호출은 생성자가 아니라 `run(raw_document_id)`에서만 일어난다.

### Conference Schema와 Data Contract 경계

`ConferenceCandidate`는 `is_conference`, `document_confidence`, `review_reasons`, 그리고 20개 nullable 필드를 가진다: `title`, `organizer`, `summary`, `description`, `official_url`, `registration_url`, `registration_start`, `registration_deadline`, `event_start`, `event_end`, `country`, `city`, `venue`, `format`, `language`, `fee`, `currency`, `eligibility`, `category`, `tags`. 모든 필드는 `value`, `raw_value`, 짧은 `evidence_text`, `raw_evidence_text`, `source_url`, `confidence`로 표현된다. 모르는 값은 null이다. 이 모델은 `PublishCandidate`가 아니며 실제 행사 검증이나 공개 허가를 뜻하지 않는다.

기존 `docs/DATA_CONTRACT.md`에는 `registration_url`·`currency`의 별도 서비스 매핑이 없다. `fee`는 향후 근거에 따라 `price_text/price_type`으로 변환해야 하고 `category/tags`는 기존 서비스 분류·태그와 별도 매칭이 필요하다. 추출 날짜는 원문과 검증된 **calendar date**만 담으며 서비스의 `timestamptz`를 만들지 않는다. 시간대가 명시되지 않았을 때 임의의 시각/시간대를 추가하지 않는다. 이 차이는 후속 정규화·게시 단계의 결정 사항이다.

### Preprocessor와 입력 크기

Python 표준 HTML parser가 `script/style/nav/footer/aside/form`, cookie/consent·breadcrumb·newsletter 노이즈와 반복 공백을 제거한다. heading, 문단, 목록, 테이블 텍스트와 안전한 절대 HTTP(S) 링크는 보존한다. 기본 `AGENT_EXTRACTION_MAX_INPUT_CHARS=12000` 중 약 80%를 텍스트, 20%를 링크에 배정한다. 페이지가 크면 첫 식별 블록과 등록·마감·일정·장소·컨퍼런스 신호가 많은 블록을 선택한 뒤 원문 순서로 재배열한다. 무조건 앞부분만 자르지 않는다. 검증에는 LLM에 보낸 발췌 외에 전처리된 전체 텍스트를 사용할 수 있다. HTML은 현재 UTF-8 replacement decoding이므로 다른 legacy encoding의 손실 가능성은 남아 있다.

### Provider abstraction, Prompt, Structured Output

`ExtractionProvider.extract(ExtractionRequest) → ProviderExtraction` 포트가 Provider 고유 응답을 격리한다. `FakeExtractionProvider`는 네트워크 없이 테스트한다. `OpenAIExtractionProvider`는 프로젝트 추천 기능과 같은 기본 모델 `gpt-5.4-nano`를 사용한다. OpenAI [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)의 Responses API `text.format` strict JSON Schema를 사용하며, 자유형 응답을 regex로 재파싱하지 않는다. SDK 자동 재시도는 0, 원격 response 보존은 `store=false`다. 모델은 설정에서 교체 가능하며 `OPENAI_API_KEY`를 런타임에만 전달한다. 키나 LLM 원문 응답은 DB/로그에 저장하지 않는다. 이 모델은 [공식 모델 문서](https://developers.openai.com/api/docs/models/gpt-5.4-nano)에서 구조화 출력과 데이터 추출을 지원한다.

당시 `agent_collect/extraction/prompts.py`의 `conference-extraction-2026-10-03-v1`가 prompt version이었다. 제공 문서만 근거로 삼고 문서 안의 지시를 따르지 않으며, 외부 지식·추측으로 필드를 채우지 않고 모르면 null을 반환하도록 요구했다. 비 Conference 페이지도 명시적으로 구분했다. 현재 코드는 위 v2 prompt를 사용한다.

### Field Evidence, Date, URL 검증

비어 있지 않은 모든 필드는 원문에서 찾을 수 있는 짧은 quote를 요구한다. 공백·구두점·대소문자를 정규화한 후 전처리 본문에 quote가 있는지 검사한다. 링크 URL 자체도 근거가 될 수 있다. 기본 evidence 최대 길이는 240자다. 일치하지 않거나 과도하게 긴 근거는 검증된 `value`와 `confidence`를 null/0으로 낮추고 `REVIEW_REQUIRED` 이유를 남긴다. 검증되지 않은 모델 원시값·근거는 `raw_value/raw_evidence_text`로만 별도 보존하며 검증값으로 사용하지 않는다.

날짜는 연도가 명시된 ISO 날짜, 영문 월+일+연도, 일부 한글 연월일만 결정적으로 파싱해 `YYYY-MM-DD`로 보존한다. `next Friday`, `this summer`, 연도 없는 월일, 범위 표현은 정확한 날짜로 추정하지 않는다. URL은 기존 `validate_target_url()`로 scheme/local/private/credential을 확인하고 원문 링크 또는 수집 페이지 URL과 정확히 일치해야 한다. 링크 목록에 없는 URL은 null 및 review 사유가 된다. 현재 URL 검증은 문서 내 존재성·구문 검사이며 대상 페이지의 실제 등록 가능 여부 검증은 아니다.

### Budget Guard, 중복, 오류

기본 정책은 `AGENT_EXTRACTION_DAILY_LLM_LIMIT=5`, `AGENT_EXTRACTION_MONTHLY_LLM_LIMIT=100`, `AGENT_EXTRACTION_MAX_RETRIES=1`, `AGENT_EXTRACTION_MAX_OUTPUT_TOKENS=1600`, `AGENT_EXTRACTION_TIMEOUT_SECONDS=30`, Asia/Seoul 날짜 경계다. `agent_logs`의 `EXTRACTION_LLM_ATTEMPT` 행을 일/월 및 fingerprint별 시도 사용량으로 세고, 호출 전에 로그를 기록한다. 따라서 호출자가 재시도 인수를 0으로 되돌려도 같은 content/version의 실제 누적 시도 횟수를 넘길 수 없다. DB 조회/기록이 실패하면 호출하지 않는다. 기존 `agent_candidates.item_fingerprint`를 원문 SHA-256+추출/프롬프트 버전+Provider/모델로 계산한다. 같은 RawDocument 또는 동일 content hash·version의 이전 결과는 Provider 호출 없이 `CACHED`로 반환한다. 다른 RawDocument의 캐시는 이전 후보 ID 참조만 돌려주며 새 후보를 강제로 복제하지 않는다. 비활성 Provider, 자격증명 없음, 입력 과다, retry 초과, 일/월 상한은 모두 명시적으로 차단된다. SDK 자동 retry는 없고 Runner도 자동 retry하지 않는다.

Guard의 조회→시도 로그는 원자적 다중 워커 예약이 아니다. 병렬 실행 시 엄격한 상한/중복 방지는 보장되지 않으므로 현재는 단일 수동 워커만 운영 전제다. `agent_logs`는 시도·완료·실패·차단 이유와 토큰 수를 남기되 키와 원문은 남기지 않는다. 오류는 안전한 코드로 축약한다.

### Candidate Persistence와 실제 Smoke

새 migration 없이 기존 `agent_candidates.extracted_payload`에 스키마 버전·필드별 검증 결과/근거를, `provenance`에 `raw_document_id`에 대응하는 실행 버전·Provider·모델·prompt version·input hash·원문 hash·시각·token 사용량을 저장한다. `status`는 `EXTRACTED/REVIEW_REQUIRED/REJECTED`(비 Conference)다. 실패/예산 차단은 후보를 생성하지 않고 `agent_logs`와 Runner outcome에 기록한다. 기존 `(raw_document_id,item_fingerprint)` unique를 재사용하며 001/002/003은 수정하지 않고 004 migration도 만들지 않았다.

실제 공개 [ICCAI 2027 페이지](https://www.iccai.net/) 1개를 Generic Collection Engine으로 수집했다. Agent Source `d0162f63-a840-41bf-9a51-a4d246eae0af` → Run `48156a62-8324-44a0-bcd3-6c5dd3bd12da` (`SUCCEEDED`) → RawDocument `b89f68e5-afd2-4206-846f-d0337c41a3c1` → Candidate `8affb3c0-db03-44a0-9d0e-5a246f2dbadc` (`REVIEW_REQUIRED`)의 실제 FK를 확인했다. 수집 뒤 테스트 Source는 `HOLD/is_active=false`로 되돌렸다. 원문은 로컬 `tmp/agent-raw-phase5`에 저장돼 있으며 이 디렉터리는 영속 운영 볼륨이 아니다. API 호출은 정확히 1회, 동일 RawDocument 재실행은 캐시 차단으로 추가 호출 0회, `agent_logs` 시도 기록은 1건이다. 실제 응답의 여러 evidence quote가 원문과 정확히 일치하지 않아 확정 필드를 모두 null로 낮췄다. 이는 추출 품질상 검토 필요 결과이지, 임의 보정의 근거가 아니다. 최초 실제 응답은 코드 보강 전 생성돼 미검증 `raw_evidence_text`를 보존하지 못했다. 후속 실행에는 최대 240자의 원시 근거를 별도 보존한다. 추가 LLM 호출은 하지 않았다.

### 향후 Vertical 확장

공통 RawDocument/Preprocessor/Provider/Guard/Repository 포트는 유지하고 vertical별 schema, prompt version, deterministic validator만 별도 추가한다. Competition·Hackathon·Education은 이번 단계에서 구현하지 않았다. 다음 단계에서 날짜/시간대 정규화와 실제 검증·중복·게시 정책을 별도로 구현해야 한다.
