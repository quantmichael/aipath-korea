# AIPATH Agent Collect — Production 운영 설계

기준일: 2026-10-04. 이 문서는 **설계 및 현상 감사**다. 배포, Worker 활성화, DB·Storage 변경, 외부 Provider 호출을 수행하지 않았다. 증거 우선순위는 현재 코드·적용된 001–008의 런타임 기록, 그다음 단계별 문서다. 오래된 단계 문서의 “migration 미적용/Processing 미완료” 문구는 [codex_tasks.md](../codex_tasks.md)의 최신 상태로 대체한다. `PASS`는 해당 범위의 검증만 의미하며 무인 Production 승인은 아니다.

## 1. 전체 Pipeline 감사

| 단계 | 구현 | 실제 Runtime 검증 | 무인 Production 준비 | 남은 위험 / 사람 개입 |
| --- | --- | --- | --- | --- |
| Discovery / Tavily | 예 | 검색 1회, 008 스케줄·claim·lease·window 검증 | 조건부 | Budget은 비원자적 ledger; Query 등록·enable은 사람 검토. 운영 Query 2개는 disabled. |
| Source Approval | 예 | 004의 CANDIDATE→REVIEW→APPROVED→ACTIVE와 DB guard 검증 | 부분 | 승인 API의 `reviewer` 문자열은 자체 인증이 아님. 사람·인증된 호출 경계 필요. 기존 5 Source는 HOLD/inactive. |
| Collection | 예 | 005, ACTIVE Source HTTP→Run→RawDocument 검증 | 조건부 | 승인 Source만 가능. 응답 제한/도메인 예절/요청 허가를 운영자가 확인. |
| Persistent Raw Storage | 예 | private `agent-raw` 업로드·재조회·SHA-256 검증 | 부분 | 운영 설정의 기본 provider는 `local`; Production에 `supabase` 명시 필요. 90일 자동 cleanup 없음. |
| Processing / Extraction | 예 | 006, 실제 RawDocument→OpenAI 1회→Candidate 검증 | 부분 | Production 5/일·100/월 예산, `CROSS_RAW_CACHE`, 원자적 LLM 예산 예약 부재. |
| Normalization | 예 | 실제 Processing E2E에서 실행 | 조건부 | 근거 없는 date/timezone은 생성하지 않음; DATE_ONLY는 Service에 직접 게시 불가. |
| Dedup / Verification | 예 | 실제 E2E에서 UNIQUE·REVIEW_REQUIRED | 부분 | 검증된 정보 부족 시 보류; 불완전한 Service lookup과 500행 경계는 fail-closed. |
| Publish Gate | 예 | 실제 E2E 결과 REVIEW_REQUIRED; offline READY 계약 테스트 | 부분 | 자연스러운 PUBLISH_READY 0건. 사람이 REVIEW_REQUIRED를 강제 승격하지 않음. |
| Publish Worker / Adapter | 예 | 007 claim·lease·권한·빈 queue·dry run 검증 | **아니오** | 실제 Service INSERT→readback→Backend API→Frontend E2E 미검증. |
| Service DB | Adapter 경계 구현 | 기존 데이터 read-only 매핑·count 확인 | **아니오** | 신규 Opportunity 게시 0건. Source·Category·시간 표현·충돌 정책 충족한 첫 자연 READY 필요. |
| Automation Runtime | 4 Worker one-shot | global lock 및 DB-backed 4 Worker dry run | **아니오** | 실무 scheduler/secret/alert/복구 운영 미배포; unattended 4 Worker 실주행 없음. |

관련 증거: [Discovery scheduling](DISCOVERY_SCHEDULING.md), [Source approval](SOURCE_APPROVAL.md), [Collection scheduling](COLLECTION_SCHEDULING.md), [Raw storage](RAW_STORAGE.md), [Processing](PROCESSING_WORKER.md), [Quality Gate](QUALITY_GATE.md), [Publish worker](PUBLISH_WORKER.md), [Automation](AUTOMATION_RUNTIME.md). PHASE 8의 최종 실제 E2E는 [codex_tasks.md](../codex_tasks.md) 상단의 최신 추가 기록을 따른다. 최신 검증 기준 전체 offline suite 207/207 통과. 이는 Production 부하·복구 시험을 대체하지 않는다.

## 2. Worker inventory

| Worker | 입력 → 출력 / Queue | DB claim·lease / 멱등성 | 실패·예산·격리 / 외부 의존성 |
| --- | --- | --- | --- |
| `DiscoveryWorker` | 검토·enabled·due인 Conference Query → DiscoveryQuery/SearchResult/SourceCandidate, 조건부 inactive Source / `agent_discovery_schedules` | 008 원자적 claim, DB clock, 기본 120초 lease, deterministic query/window; 성공 terminal, 같은 window Query 중복 거부 | 1h→6h→24h, budget block 24h; Tavily 30/일·1000/월·동일 Query 24h cooldown; Tavily·Agent DB. 한 Query 실패가 다른 Queue를 중단시키지 않음. |
| `CollectionWorker` | 승인 ACTIVE due Source → CollectionRun/RawDocument/private object / `agent_source_schedules` | 005 DB claim, 기본 1800초 lease, source/window unique, 성공 reconcile | 30m→2h→6h→24h, 4회 이상 review flag; HTTP·Supabase DB/Storage. 1회 최대 5 Source, 순차 실행, domain 동시 1. |
| `ProcessingWorker` | 성공한 RawDocument·version → Candidate/Verification/Gate/processing completion / `agent_processing_jobs` | 006 `(raw_document_id, version)` unique, claim/ledger, 기본 300초 lease; checkpoint·terminal reconciliation | 15m→1h→6h, 4회 후 REVIEW_REQUIRED; budget block 15m; Supabase private Storage·OpenAI·Agent DB, Service duplicate SELECT. 1회 최대 1건. |
| `PublishWorker` | READY queue의 VERIFIED/PUBLISH_READY snapshot → Service Opportunity/readback 또는 HELD/FAILED / `agent_publish_queue` | 007 DB claim·publish singleton lease 기본 300초, completion ledger, stable slug/external ID 재조회 | FAILED 15m 후 재시도, duplicate/ambiguous는 HELD; Service DB·Agent DB. 1회 최대 1건. 두 개의 enable(`publish_worker_enabled`, `publish_enabled`)과 eligibility 필요. 실제 INSERT 경로 미검증. |
| `AutomationRuntime` | 위 네 Worker의 독립 due Queue → run summary / `agent_runtime_leases` + Agent log | DB global lease 기본 3600초; 중복 cycle은 `LOCKED`; `finally` release | Worker exception은 `PARTIAL`에 격리하고 다음 Worker 진행. 별도 Budget 없음; Python runtime·Agent DB·활성 Worker별 의존성. `run_once`는 daemon이 아님. |

`AutomationRuntime`은 Discovery→Collection→Processing→Publish 순서지만 각 Queue는 독립이다. 신규 Discovery Source는 같은 cycle에서 자동 승인·Collection되지 않는다. Worker summary의 실패 count가 있어도 `AutomationRuntime.errors`에 exception이 없으면 CLI exit 0일 수 있으므로 **프로세스 종료코드만 성공 지표로 쓰지 않는다**. 기본 max jobs와 lease는 [defaults.toml](../agent_collect/config/defaults.toml)을 기준으로 하며 운영에서 임의 증량하지 않는다. 자동화 lease heartbeat가 없고 외부 Scheduler는 at-least-once일 수 있어, 실주행 최대 시간이 lease 이내인지 관찰해야 한다.

## 3. 사람 개입과 자동 게시 경계

| 사람이 결정·승인 | 자동 코드가 수행할 일 | 금지 |
| --- | --- | --- |
| 신규 Source의 공식성, URL/domain, `source_type`, 신뢰도, robots/약관·수집 허용, method/config, 12h 등 interval | 승인된 ACTIVE만 next_collection_at에 따라 수집 | Discovery 신호만으로 APPROVED/ACTIVE 전환 |
| `REVIEW_REQUIRED`의 누락·오탐·근거·중복·날짜 검토 | 정상 버전 재처리/Quality Gate 재평가, 검증된 경우에만 READY 생성 | Candidate/Verification/Queue를 직접 수정해 READY 강제 |
| Query 등록·enable, 환경/예산/Worker flag, 첫 게시 승인, 장애 대응 | Due/claim/lease/retry와 deterministic Normalize/Status | 운영자가 매 Run마다 DB 시간을 수정하거나 임의 데이터 보정 |

현재 Source 승인 Service와 감사 이벤트는 있으나 운영자 인증을 자체 제공하지 않고, Source 승인용 Admin UI/완성된 CLI도 없다. Candidate `REVIEW_REQUIRED`를 사람이 판정해 재처리·재검증하는 **정식 감사 Workflow/Admin UI는 없음**. 단기에는 접근 통제된 운영 담당자 2인이 Agent 조회와 증거 검토 결과를 티켓에 남기고, 수정 가능한 원인만 승인된 코드·Source 설정·새 processing version으로 재실행한다. 수동 DB UPDATE로 승격하지 않는다. 장기 Admin UI에는 인증/역할, Source 승인 snapshot·diff·결정 ID, Candidate 근거/Raw hash, 중복 비교, 사유·검토자·시간, 2인 승인, immutable audit, replay/dry-run이 필요하다.

자동 Publish 계약은 `VERIFIED + UNIQUE + PUBLISH_READY + READY queue` 및 Adapter의 Source/Category/timezone/공개상태/최종 충돌 검사를 모두 만족할 때만 `PublishWorker`가 게시할 수 있다는 것이다. `REVIEW_REQUIRED`는 사람 검토, `REJECTED`는 자동 게시 금지. 현재 자연 READY는 0건이고 신규 Service INSERT E2E는 미검증이다. **첫 자연 READY 1건은 무인 게시하지 말고**, 운영자가 read-only dry run과 증거·매핑·충돌을 승인한 뒤, 별도 통제된 1회 `PublishWorker` 실행을 권장한다. 그 1건에서 INSERT ID, DB readback 필드/FK/status/date/URL, Backend 상세·목록 API, Frontend 표시, 같은 Candidate 재실행 무중복, Agent completion ledger를 확인하고 서명한다. 기존 Opportunity UPDATE/MERGE/DELETE 금지. 이후에만 별도 결정으로 자동 Publish enable을 검토한다.

## 4. 호출 주기와 실행 환경 권고

권장 외부 tick은 **10분마다 1회 one-shot**(UTC 기준, 정각 분산)이다. 이는 6회/시간·144회/일의 **최대 호출 시도**이지 144회 Tavily/OpenAI 호출이 아니다. 내부 `next_run_at`은 Discovery 기본 24h, Source `next_collection_at` 기본 12h(승인 시 6h/24h/72h 가능), Processing due/재시도, Publish due/15m retry가 결정한다. 10분 tick은 DB 부하와 반응 지연의 절충안이다. 운영 실측에서 cycle 시간·backlog가 안정적이고 필요한 경우에만 5분으로 단축한다. `automation_lease_seconds=3600` 동안 중복 cycle은 잠기지만 heartbeat가 없으므로 60분 초과 실행, scheduler 중복 dispatch, 실패한 claim 복구를 모니터링한다. 하나의 Source에 여러 cron을 만들지 않는다.

| 외부 실행 선택지 | 24/7·재시작 | 비용·secret·로그 | Python/Supabase 적합성 / 판단 |
| --- | --- | --- | --- |
| 단일 호스트 `cron` | 호스트 가동·관리 필요; missed run 보완 별도 | 저렴하나 OS patch, secret 파일, 로그 회전 수동 | one-shot 직접 실행 가능; 초기 실험용. |
| 작은 VPS/container + `systemd timer` | 호스트 운영 필요, 서비스 실패/재시작·journal 구성 가능 | 고정 인프라비와 운영 부담; secret 파일 권한 관리 | Python 실행 쉬움; 팀이 이미 VPS를 운영한다면 대안. |
| GitHub Actions schedule | 호스트 불필요하나 정시성 보장 낮음 | Actions secret/로그는 편리하나 CI 권한 경계 주의 | 공식 문서상 고부하 시 지연·드롭 가능; 핵심 무인 수집의 주 스케줄러로 비권장. [GitHub schedule](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows) |
| Supabase Cron/Edge Functions | DB cron 가용 | Vault/pg_net 관리 추가 | 공식 경로는 Edge Function/HTTP 호출; Python one-shot을 직접 실행하지 못해 bridge가 필요. [Supabase scheduling](https://supabase.com/docs/guides/functions/schedule-functions) |
| **Cloud Scheduler → Cloud Run Job** | 관리형 scheduled job, 실행 로그·재시도 | 사용량 기반 비용 + Secret Manager/Cloud Logging 비용 검토 | Python 컨테이너의 one-shot에 직접 부합; 인증된 Job trigger. [Job scheduling](https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule), [Job secrets](https://docs.cloud.google.com/run/docs/configuring/jobs/secrets) |

**권장안:** 별도 Python 컨테이너 이미지의 Cloud Run Job 1 task를 Cloud Scheduler가 10분마다 호출하고, Secret Manager에 Supabase service key·Tavily·OpenAI key를 보관한다. Job과 Scheduler의 service account는 최소 권한, 리전은 Supabase와 네트워크 지연을 검토해 선택한다. Scheduler는 at-least-once이므로 기존 Agent global lease/claim에 의존하되 `LOCKED`를 별도 관측한다. [Cloud Scheduler의 전달 의미](https://docs.cloud.google.com/scheduler/docs/overview). 코드에는 아직 Dockerfile/배포 파이프라인이 없으므로 이것은 **선택 권고이지 배포 완료 사실이 아니다**. 계정·리전·견적은 운영자가 승인해야 하며 [Cloud Run](https://cloud.google.com/run/pricing)과 [Scheduler](https://cloud.google.com/scheduler/pricing) 비용은 실제 CPU·시간·로그·네트워크에 따라 산정한다. 기존 관리형 호스트/운영자가 있다면 VPS+systemd가 비용 측면 대안이다.

## 5. 환경·비밀·예산

| 영역 | Development | Production 요구 |
| --- | --- | --- |
| `APP_ENV` | 명시적 `development`일 때만 LLM 20/일·300/월 | `production` 명시; 미설정/알 수 없는 값은 코드상 5/일·100/월 fail-safe지만 운영에서 누락을 허용하지 않음 |
| Supabase | 개발 프로젝트/키 분리 권장 | `AGENT_SUPABASE_URL`, `AGENT_SUPABASE_SERVICE_KEY`를 secret manager에서 주입; 기존 `SUPABASE_*` fallback은 분리 전환의 임시 경로. 키 값을 로그·코드·CLI 인자에 남기지 않음 |
| Provider | 개발 key/usage 분리 권장 | `AGENT_TAVILY_API_KEY`, `OPENAI_API_KEY`; 각 Guard enable 전 사용량 확인 |
| Storage | `local` 기본, offline 전용 | `AGENT_RAW_STORAGE_PROVIDER=supabase`, private `agent-raw`, 5MiB collection/bucket limit 일치, 별도 접근 확인 |
| Flags | 필요 작업만 선택 | 초기 전부 false; `AGENT_AUTOMATION_ENABLED`, 네 Worker flag, `AGENT_DISCOVERY_ENABLED`, `AGENT_LLM_ENABLED`, `AGENT_PUBLISH_ENABLED`를 단계적으로 승인 |

Tavily 현재 guard는 30/일, 1000/월, 동일 Query 24h cooldown, 월 warning 800/critical 950, hard stop이다. OpenAI는 Production 5/일·100/월, Development 20/일·300/월이다. Asia/Seoul 일·월 경계를 사용한다. 하루 5건까지 가능해도 30일 동안 5건/일이면 150건으로 **월 100을 초과**한다. 지속 가능한 평균은 약 3.3건/일 이하(월 30일 가정)이며 retry/수동 추출 여유를 남겨야 한다. 시작은 활성 Source 1개·Query 소수·Worker max jobs 기본값으로 측정하고, raw 수집량과 LLM 대상량을 분리 추산한다. Guard는 호출 전 ledger 확인이나 manual Extraction과 경합할 수 있어 원자적 전역 예산 상한 보장은 미완성이다. Budget 기록 초기화나 Production을 `development`로 표기하는 방법을 사용하지 않는다.

## 6. Raw Storage 및 DB 보존

Private `agent-raw`의 content-addressed `raw/sha256/...` 객체는 hash 전·후·재조회 SHA-256을 확인한다. URL/민감정보를 key에 넣지 않고 공개 URL을 생성하지 않는다. service-role은 RLS를 우회할 수 있으므로 키는 신뢰된 서버 전용이다. [Supabase private bucket](https://supabase.com/docs/guides/storage/buckets/fundamentals), [service key 주의](https://supabase.com/docs/guides/storage/security/access-control). 현재 RawDocument provenance는 기본 90일 만료를 표현하지만 자동 삭제기는 없다. 90일은 재처리·감사·분쟁 해결 기간이 90일 이내라는 **운영/법무 확인 전에는 삭제 명령이 아니다**. Shared hash object를 쓰므로 삭제 전 모든 live `content_ref` 참조, 진행 중 claim, Candidate/Service lineage를 확인하고 manifest→dry run→2인 승인→정확한 object만 삭제한다. orphan은 Storage에 있으나 RawDocument 참조가 없는 key; missing은 metadata 참조가 있으나 객체가 없는 key다. 양쪽을 주기적으로 대조하고 hash 샘플 검증한다. 본 단계에서는 cleanup을 구현·실행하지 않았다.

증가량 계획: 실제 raw 평균 크기 `B`, 하루 새로 저장되는 고유 hash 수 `U`일 때 90일 Storage 용량 ≈ `90×U×B`(metadata/복제 제외). 예시로 100 Source×12h당 1회×200KiB, 매번 다른 본문이면 약 40MiB/일·3.6GiB/90일이다. 5MiB 객체 상한에서 같은 가정의 극단값은 약 1GiB/일·90GiB/90일; 이는 **예측이 아닌 용량 시나리오**다. 가격과 quota는 실제 Supabase plan·계약에서 확인한다.

| Agent 데이터 | 증가 원인 | 운영 보존/아카이브 제안(미구현) |
| --- | --- | --- |
| Discovery queries/results/candidates/windows | 매 검색의 결과·근거·window | 최근 90일 검색 결과 온라인, 오래된 실패/중복은 집계·archive; Source 승격 provenance는 유지 |
| Collection runs/raw documents/source schedules | Source 주기, 실패 재시도 | Raw bytes 기본 90일 검토; Run/metadata는 게시 lineage·사고 조사 기간 별도 보존 |
| Processing jobs/completions·Candidates·Verifications | 버전별 재처리, 검증 이력 | 게시/검토 감사에 필요한 버전·근거·결정 보존; 중복 trial 데이터만 승인 후 archive |
| Publish queue/completions·Source review events | 게시 시도, 사람 승인 | Service ID·결정·검토자·time·snapshot은 장기 감사 보존; 임의 purge 금지 |
| Agent logs | 매 Worker/오류 | 민감 payload 없이 지표/오류를 중앙 로그로 전송; 검색 가능한 온라인 기간과 비용 상한 설정 |

DB는 테이블별 일별 row/size, index bloat, queue age, Storage bytes를 계측한 뒤 보존 기간을 확정한다. Service DB와 Agent DB가 같은 Supabase 프로젝트이므로 용량·백업·장애 영역이 공유된다.

## 7. Monitoring·Alerting·운영 대응

제안 지표와 초기 임계값은 **운영 설정 제안**이지 현재 구현된 알림이 아니다.

| 영역 | 최소 지표 | 초기 alert 제안 / 대응 |
| --- | --- | --- |
| Automation | 마지막 `AUTOMATION_RUN`, status, duration, lock age | enable 후 30분 무실행(10분 tick 3회), `PARTIAL` 1회, 45분 초과 cycle·lock 지속 → scheduler/worker/log 점검 |
| Discovery | enabled/due Query, success/failure, results/candidates, budget/cooldown block | due가 48h 이상 지연, provider 오류 3회, HARD STOP 즉시 → Tavily key/usage 검토; 수동 검색 중지 |
| Collection | due/claimed/success/failure, Source 연속 실패, Raw bytes | ACTIVE Source 2회 연속 실패 또는 due age 2×interval, 4회 review flag → 사이트 변화·약관 확인 후 HOLD 판단 |
| Processing | pending age, result별 수, retry, LLM 사용량·오류 | oldest due >1h, daily/monthly hard stop, hash mismatch 1건 → provider/Storage/후보 점검; 실패 재실행 전 근거 확인 |
| Publish | READY age, published/HELD/FAILED, claim age, Service readback | READY >1h, publish 실패·모호한 conflict 1건, readback mismatch 즉시 → publish flag off, Service ID 대조 |
| Storage/DB | 객체 수/bytes, orphan/missing/hash, RPC permission/error, DB size | missing/hash 1건, upload 실패 1건, RPC 42501/5xx 급증 → 관련 Worker off, 원본 보호·복구 |

메트릭 출처는 `agent_logs`, 각 Queue/Schedule/Completion, Automation status, Cloud job logs다. 알림은 별도 구현·전송 경로와 당직자/에스컬레이션 정책이 필요하다. 현재 `automation-status --live`는 bounded read-only 요약이지만 자세한 backlog age/스토리지 orphan 탐지 대시보드는 아니다. 운영자는 매일 READY/REVIEW backlog, last success, 예산 잔량, 보안 감사 기록을 확인한다.

## 8. Enable, 정지, 장애 복구

1. **Stage 0 — 환경 안전**: production 분리·secret·backup/restore·로그/알림·scheduler dry run·legacy Service write ownership을 확정하고 [체크리스트](PRODUCTION_CHECKLIST.md)를 승인한다. Source/Query/Worker는 아직 비활성.
2. **Stage 1 — Collection only**: 사람 승인된 Source **1개**를 ACTIVE로 전환한 뒤 `automation_enabled`와 `collection_worker_enabled`만 enable. Production Supabase Storage를 명시하고 1–2일 Raw/hash/lease/예절을 관찰.
3. **Stage 2 — Processing**: OpenAI key/`llm_enabled`와 `processing_worker_enabled` enable. 하루·월 예산, backlog, REVIEW_REQUIRED, Cache를 관찰; 기존 backlog 일괄 방출 금지.
4. **Stage 3 — Discovery**: 검토된 Query 소수만 reviewed enable 후 `discovery_enabled`와 `discovery_worker_enabled` enable. 신규 Source는 계속 inactive; 사람 Approval staffing 확보.
5. **Stage 4 — Publish**: 자연 READY 첫 1건을 사람 승인 후 별도 통제 Worker로 INSERT/readback/API/UI E2E. 서명 완료 전 `publish_worker_enabled=false`, `publish_enabled=false`. 이후 별도 변경 승인으로 자동 게시 허용 여부 결정.

즉시 중단: 배포된 비밀 설정의 `AGENT_AUTOMATION_ENABLED=false`로 **다음** `automation-run`의 모든 Worker를 차단하고 외부 Scheduler도 pause한다. 개별 `AGENT_*_WORKER_ENABLED=false`; Discovery/LLM/Publish는 해당 Provider·Adapter flag도 끈다. 이것은 실행 중 Python 프로세스를 강제 중단하지 않으며, 직접 `collection-worker/processing-worker/... --once`나 기존 Backend Collect endpoint까지 막는 전역 시스템 kill switch가 **아니다**. 따라서 권한 있는 수동 실행·legacy trigger도 운영 통제에 포함한다. 상태 변경 후 `automation-status`와 Agent lease/Queue를 확인하고, in-flight 완료 또는 lease 만료를 기다려 recovery한다. Queue/Raw/Service row를 지우지 않는다.

복구 원칙: Worker crash는 lease expiry와 DB reconciliation을 먼저 확인하고 재호출한다; 이미 성공 Run/processing checkpoint/publish Service ID가 있으면 외부 호출 없이 reconcile한다. DB outage는 모든 Worker 정지, 권한·연결 복구 후 due backlog를 소량으로 재개한다. Storage outage/missing/hash mismatch는 Processing·Collection을 정지하고 private object/metadata/hash를 대조한다. Tavily/OpenAI outage는 각각 해당 Worker를 끄고 예산·retry에 맡기며 한꺼번에 backlog를 풀지 않는다. Partial Service INSERT는 **기존 Service 행을 stable slug/external ID로 read-only 조회**한 뒤 Agent ledger와 재조정한다; 신규 INSERT 재시도 전 기존 ID를 확정한다. Stuck lease는 DB 서버 clock·실제 실행 상태를 확인한 후 정상 expiry/RPC 경로로 복구하고 직접 claim 필드 조작을 상시 절차로 삼지 않는다. Backlog 증가는 예산/Source 수/오류율을 먼저 보며 max jobs 증량으로 덮지 않는다.

백업은 DB와 Storage를 별도로 다룬다. 실제 Supabase plan의 DB 자동 backup/PITR, retention, 복원 가능한 최신 시점과 Storage 원본 백업·복구 경로를 운영자가 확인해야 한다. Supabase 공식 문서상 자동 일일 DB backup은 유료 plan 제공 사항이고 Free plan은 백업 다운로드가 제한된다. [Supabase Production Checklist](https://supabase.com/docs/guides/deployment/going-into-prod), [DB backups](https://supabase.com/features/database-backups). 과거 Dashboard는 조직 `FREE`로 표시됐으나 현재 청구·backup 상태를 이번 작업에서 재조회하지 않았으므로 **NOT VERIFIED**로 둔다. DB 복원은 Service와 Agent가 같은 프로젝트에 있어 양쪽 영향을 평가해야 하며, 가능하면 격리된 새 프로젝트에 복원 rehearsal 후 전환한다.

## 9. Legacy Collect·Security·결정 사항

기존 Backend의 `/api/collect/run`, `/promote`, 후보 관리 endpoint와 `collect/` 코드는 여전히 존재한다. 이 저장소에서 현재 배포된 cron/외부 trigger 설정은 확인되지 않았다. **동시 Service write 여부는 NOT VERIFIED**다. Agent가 Service 게시 소유권을 갖기 전 legacy endpoint의 실제 스케줄·secret·쓰기 권한·기존 17 Opportunity의 출처를 읽기 전용으로 감사한다. 첫 게시 전에는 **하나의 Service write owner만** 승인해야 한다. Legacy는 즉시 삭제/마이그레이션하지 않고 rollback 참고로 보존하되, 이중 자동 수집·게시를 허용하지 않는다. 전환 완료 후 별도 변경관리로 legacy trigger 중지와 회귀/rollback 계획을 승인한다.

보안 점검: service-role은 서버/Job에서만, 키 회전과 Secret Manager IAM 분리; `AGENT_*` fallback이 기존 Backend key를 공유하는지 확인; RPC별 SECURITY DEFINER owner·고정 search_path·PUBLIC/anon/authenticated EXECUTE 차단·RLS·최소 테이블 권한을 배포 후 재검증; private Storage bucket과 signed/public URL 금지; Source 승인 호출자는 인증된 사람 ID와 2인 승인; Candidate·Service publish audit는 불변 이력과 Service ID 연결; 로그에 raw body/키/모델 전체 출력 금지. 현재 코드의 reviewer string은 신원 증명이 아니므로 운영 접근 경계가 확정되기 전 무인 Source 승인 절차를 열지 않는다.

현재 **NO-GO: 전체 무인 Production 및 자동 Service Publish**. 001–008 적용, DB primitive, 207 tests, private Storage, dry-run은 PASS이나 실제 Publish E2E·운영 scheduler·secrets/backup/alert·review 운영·legacy write ownership은 미충족 또는 미검증이다. 제한된 Collection/Processing 파일럿은 Stage 0 증거가 갖춰지고 별도 사람이 승인하면 가능하다. 다음 실제 작업은 [체크리스트](PRODUCTION_CHECKLIST.md)의 FAIL/NOT VERIFIED를 닫는 것이며, 이번 문서는 어떠한 flag나 Worker도 활성화하지 않았다.

## 2026-10-05 — existing PUBLISH_READY/HELD read-only audit

Classification: **TEST_ARTIFACT**, not a naturally eligible Publish result.
No Candidate, Verification, Queue or Source was changed by this audit.

| Audit field | Read-only evidence |
| --- | --- |
| Candidate | `90000000-0000-4000-8000-000000000061` |
| RawDocument | `b89f68e5-afd2-4206-846f-d0337c41a3c1` (ICCAI original local RawDocument) |
| Source / CollectionRun | `d0162f63-a840-41bf-9a51-a4d246eae0af` / `48156a62-8324-44a0-bcd3-6c5dd3bd12da` |
| Created | 2026-10-04 21:40:53.796640 Asia/Seoul |
| Origin marker | `provenance.runtime_publish_smoke_test=true`, synthetic URL `https://example.org/runtime_publish_smoke_test/independent` |
| Extraction / processing version | Absent; fingerprint NULL; no associated Processing job |
| Structured extraction / normalization / duplicate result | Empty objects |
| Required fields / evidence | All required extracted fields absent; no grounded evidence |
| Verification | `90000000-0000-4000-8000-000000000062`, stored VERIFIED/confidence=1, checks only a PUBLISH_READY label, evidence_refs empty |
| Queue | `90000000-0000-4000-8000-000000000063`, created 2026-10-04 21:40:54.174032 Asia/Seoul |
| Queue state | HELD, attempt_count=1, last_error_code=RUNTIME_SMOKE_TEST, no Service ID or published_at |

Current policy rejects this as publish-eligible input before a normal Gate
assessment: the repository parser lacks extraction_status and the structured
candidate has no fields/schema/version. Stored VERIFIED/PUBLISH_READY labels
are insufficient. Title, summary, organizer, official_url, event_start,
confidence grounded in extraction, evidence and normalized aware event time
are missing. No replacement judgment was written.

Associated Run logs describe real earlier ICCAI v1/v2 extraction Candidates,
not the synthetic Candidate. In particular the grounded artifact
`ec29ff04-9a4e-4b0c-8d85-93a0c2f22f3f` remains REVIEW_REQUIRED. The synthetic
Candidate URL also differs from its referenced RawDocument URL. The origin
marker, fixed fixture IDs and HELD reason identify a Publish Runtime smoke
fixture. Association with PHASE 9/migration 007 runtime validation is an
inference; exact creating operator/SQL statement is not recorded in these
rows and is NOT VERIFIED.

Earlier PUBLISH_READY=0 documents described natural extraction/Gate outcomes;
the persisted test fixture explains why a later literal status count is 1.
Do not treat that count as the first natural READY or as Publish E2E success.
This audit does not authorize cleanup, promotion, publication or Service writes.
The second Shadow run confirmed these Candidate/Queue rows unchanged and
Service opportunities 17→17 with every row identical. See the cache contract
and remaining live validation limits in [PROCESSING_WORKER.md](PROCESSING_WORKER.md).

## PHASE 10A container foundation update (2026-10-11)

The Agent-only Cloud Run Jobs image/entrypoint and deployment commands are
prepared in [CLOUD_RUN_DEPLOYMENT.md](CLOUD_RUN_DEPLOYMENT.md). This does not
change the operational **NO-GO** for unattended Production or Service Publish.
The container requires explicit `AGENT_PUBLISH_ENABLED=false` and
`AGENT_PUBLISH_WORKER_ENABLED=false` before DB connection, and exits non-zero
when a Worker reports failed items. Docker/gcloud are unavailable here, so
build, Cloud API/resource state and execution remain unverified. No Job,
Scheduler, service account, secret or Registry resource was created in 10A.
