# Agent Collect Production GO / NO-GO 체크리스트

기준일: 2026-10-04. 현재 값은 [운영 설계](PRODUCTION_OPERATIONS.md)와 코드·기존 Runtime 기록의 **증거 수준**이다. `PASS`는 확인한 범위만, `FAIL`은 필요한 운영 조건이 아직 없거나 명백히 미충족, `NOT VERIFIED`는 증거가 없음을 뜻한다. 이 문서 작성으로 DB·flag·scheduler는 변경되지 않았다. 운영자는 재검증일·담당자·증거 링크를 각 항목에 추가하고 별도 승인한다.

| # | Production 전 확인 항목 | 현재 | 통과 증거 / 남은 조치 | 담당·일자 |
| --- | --- | --- | --- | --- |
| 1 | Agent migration 001–008 실제 적용·catalog/RLS/RPC 확인 | PASS | 각 PHASE Runtime 기록, 특히 [008](DISCOVERY_SCHEDULING.md); 배포 대상 프로젝트 식별 재확인 | — |
| 2 | 전체 offline 회귀 테스트 | PASS | 207/207. 배포 이미지에서 동일 suite 재실행 필요 | — |
| 3 | DB 서버 시각 claim·lease·window·terminal 원칙 | PASS | 005–008 실제 경쟁/복구 검증. 운영 인스턴스 재검증 계획 필요 | — |
| 4 | private `agent-raw`·프로세스 간 readback·SHA-256 | PASS | [Raw storage](RAW_STORAGE.md); Production provider 설정은 별도 #5 | — |
| 5 | Production `AGENT_RAW_STORAGE_PROVIDER=supabase` 및 bucket/5MiB 일치 | NOT VERIFIED | 코드 기본 `local`; 실제 Job 환경·bucket 정책·접근권한 확인 | — |
| 6 | Production `APP_ENV=production`, 개발/운영 Supabase·key 분리 | NOT VERIFIED | 코드 fail-safe 5/100 확인. 배포 환경·credential 소유권은 미설정 | — |
| 7 | Tavily 30/일·1000/월·24h cooldown, OpenAI 5/일·100/월 설정·현재 usage 확인 | NOT VERIFIED | 코드 기본 정책은 확인됐으나 운영 월간 예상량·배포 사용량·예산 경보는 미검증 | — |
| 8 | Global/4 Worker/Provider/Publish flags 기본 off | PASS | [defaults.toml](../agent_collect/config/defaults.toml), PHASE 10 dry run. 배포 환경 override는 확인 필요 | — |
| 9 | Kill switch 연습: Scheduler pause + `AGENT_AUTOMATION_ENABLED=false` + in-flight 관찰 | NOT VERIFIED | 코드 disabled 경로 존재; 실제 배포·중단 훈련 없음 | — |
| 10 | Scheduler/Job 이미지·Python 의존성·10분 tick·Secret Manager·로그·재시작 | FAIL | 선택/구축/배포 없음. [권장안](PRODUCTION_OPERATIONS.md#4-호출-주기와-실행-환경-권고) 승인 필요 | — |
| 11 | Job runtime < 3600초 global lease, Worker lease와 timeout 관계 실측 | NOT VERIFIED | 코드 validation 존재, Production 부하/네트워크 시간 미측정. heartbeat 없음 | — |
| 12 | Monitoring/Alerting 담당자와 last-run·backlog·budget·DB·Storage 경보 | FAIL | 지표·임계값 제안만 존재; 알림 경로 미구축 | — |
| 13 | Supabase DB backup/PITR·Storage 백업 및 격리 복원 rehearsal | NOT VERIFIED | 현재 plan·복원점·RPO/RTO 확인 필요. 과거 조직 화면은 FREE 표시 | — |
| 14 | Raw 90일 보존 정책 승인, orphan/missing/hash 점검, 안전 cleanup | FAIL | 만료 provenance만 존재; 자동 정리/운영 승인 정책 없음. 검증 전 삭제 금지 | — |
| 15 | Service·Agent DB/Storage 용량·quota·비용 예산 | NOT VERIFIED | Source 수·본문 평균·로그/인덱스 크기 실측 필요 | — |
| 16 | 신규 Source의 공식성·수집 허용·type/trust·interval 사람 승인 | FAIL | 004 DB lifecycle guard는 검증됐지만 인증된 운영자 절차/Admin UI와 담당 체계 없음 | — |
| 17 | REVIEW_REQUIRED Candidate의 인증·감사 가능한 판정 Workflow | FAIL | 현재 Admin UI/정식 사람 판정 API 없음. 직접 상태 변경 금지 | — |
| 18 | Legacy Collect trigger와 Agent의 Service write ownership 단일화 | NOT VERIFIED | Backend legacy endpoint 존재; 실제 배포 트리거·중복 경로 read-only 감사 필요 | — |
| 19 | `PUBLISH_READY` 자연 발생 1건 및 READY queue 정합성 | FAIL | 현재 0건. 검증/threshold/Source 조작 금지 | — |
| 20 | 첫 Service Publish 1건: dry run 승인→INSERT→readback→API→Frontend→재실행 | FAIL | 자연 READY 부재로 미검증; 첫 1건은 통제된 수동 승인 후 수행 | — |
| 21 | Service Source/Category mapping·날짜/timezone·충돌 scan에서 누락/모호성 0 | NOT VERIFIED | 실제 첫 적격 Candidate 기준 평가 필요. DATE_ONLY는 현재 hold | — |
| 22 | Service role/Provider key 최소권한·회전·로그 비밀 차단 | NOT VERIFIED | DB RPC 일부 검증; 배포 identity, secret store, key rotation 미정 | — |
| 23 | Publish 실패/부분 INSERT·Storage/DB outage·stuck lease 복구 훈련 | NOT VERIFIED | offline/DB primitive 증거는 있으나 첫 Service 게시·Production disaster drill 없음 | — |

## 단계별 개방 게이트

| 단계 | 필수 통과 | 승인자·결정 | 현재 |
| --- | --- | --- | --- |
| Stage 0 플랫폼 | #5–15, #18, #22–23 중 관련 항목; scheduler dry run·복원/kill drill | 운영/보안 책임자 | **NO-GO** |
| Stage 1 Collection 파일럿 | Stage 0 + #16, 사람 승인 ACTIVE Source 1개, 1–2일 Raw/hash/오류율 관찰 | 데이터 책임자 | **미개시** |
| Stage 2 Processing 파일럿 | Stage 1 + OpenAI 월간 용량/usage·REVIEW 인력 확보 | 데이터/비용 책임자 | **미개시** |
| Stage 3 Discovery 파일럿 | Stage 2 + reviewed Query 소수, Tavily budget/cooldown, Source 승인 처리능력 | 데이터 책임자 | **미개시** |
| Stage 4 자동 Publish | #17, #19–21 전부, 첫 자연 READY 1건 통제 게시·API/UI/readback·idempotency 서명 | 제품/데이터/운영 책임자 | **NO-GO** |

## 첫 Service Publish 관찰 기록 양식

운영자가 실제 자연 READY 발생 후 별도 변경 승인을 받아 작성한다. Synthetic READY나 기존 Candidate 강제 변경을 사용하지 않는다.

| 필드 | 기록 |
| --- | --- |
| Candidate ID / Verification ID / Queue ID·상태 | NOT VERIFIED |
| 사람 승인자·시간·근거 링크, dry-run payload·conflict | NOT VERIFIED |
| Service Source/Category FK, time precision/timezone, 공개 status | NOT VERIFIED |
| INSERT 요청 수 / 새 Service Record ID | NOT VERIFIED |
| DB readback 일치·Agent publish ledger | NOT VERIFIED |
| Backend 상세/목록 API 및 Frontend 실제 표시 | NOT VERIFIED |
| 동일 Candidate 재실행 INSERT 0·기존 Opportunity 무변경 | NOT VERIFIED |
| Service `opportunities` 전후 count/기존 row 영향 | NOT VERIFIED |
| 이상 시 kill switch·복구 결과 | NOT VERIFIED |

## 최종 판정

**현재 전체 무인 운영 및 Service 자동 게시: NO-GO.** 기술적 Foundation/PHASE 10 Discovery DB 런타임 검증은 완료됐지만 Scheduler/운영 보안/backup/alert와 첫 실제 Publish 경로가 열려 있지 않다. `FAIL` 또는 중요 `NOT VERIFIED`를 `PASS`로 바꿀 때에는 실행 로그·설정 스냅샷·담당자·시각을 첨부한다. `PASS`를 추정으로 채우지 않는다. Stage별 별도 GO를 받은 뒤 한 단계씩 enable하고 다음 단계 전 rollback/kill drill을 완료한다.

승인 기록: 날짜 `__________` / 운영 `__________` / 데이터 `__________` / 보안 `__________` / 결정 `GO / NO-GO` / 근거 `__________`.
