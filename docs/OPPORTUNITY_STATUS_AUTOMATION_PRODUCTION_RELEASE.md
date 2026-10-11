# Opportunity Status Automation — Production release

검증일: 2026-10-11 KST. 운영 DB와 HTTP API는 필요한 읽기 요청만 사용했고,
시간 근거 등록 외에는 운영 데이터·Cron 설정을 변경하지 않았다.

## 배포

- 원격 `main`을 Preview 검증 브랜치의 `e96ecbc010e5221b5e2355880e87cb68bcd155dc`로
  fast-forward push했다. 로컬 `main`의 미커밋 파일은 건드리지 않았다.
- Vercel Production 자동 배포 `Ho7M5PALPz4eGdatA43iqQHQe2uY`가
  `Ready`와 `Latest`로 표시됐다. 운영 URL은
  [www.aipath.kr](https://www.aipath.kr/opportunities.html)와
  [Vercel 기본 도메인](https://aipath-korea.vercel.app/opportunities.html)이다.
- Production은 `main`을 추적한다. Preview 전용 합성 seed·읽기 권한 SQL은
  main에 포함하지 않았다. Vercel의 Production 전용 Supabase URL·Secret 설정과
  Preview의 분리된 환경을 확인했다. Preview 읽기 전용 보호는 변경하지 않았다.
- 운영에 이미 적용된 Migration 002와 Cron 정의를 소스 이력에 추가했지만
  Migration을 재적용하거나 Cron을 중복 등록하지 않았다.

## 근거 등록 전 영향 계산

운영 DB는 17건(`open` 11, 기존 `closed` 6), scope 0건이었다.
원문과 저장된 값이 일치한 3건만 사전 계산했다. 오프라인 mirror와 운영
`opportunity_status_target` 함수가 같은 결과를 반환했다.

| Opportunity ID | 원문 근거 | 정밀도 | 사전 상태 → 예상 상태 |
| --- | --- | --- | --- |
| `c3a00d1d-1ebb-4deb-903b-fe7300a19ea6` | [기업마당 126283](https://www.bizinfo.go.kr/sii/siia/selectSIIA200Detail.do?pblancId=PBLN_000000000126283): 9월 8일–10월 6일, 시각 없음 | 시작·마감 `DATE_ONLY` | `open→closed` |
| `883d7e82-5870-474f-8d70-e39cedfeddff` | [기업마당 126814](https://www.bizinfo.go.kr/sii/siia/selectSIIA200Detail.do?pblancId=PBLN_000000000126814): 9월 28일–10월 12일, 시각 없음 | 시작·마감 `DATE_ONLY` | `open→open` |
| `cc108cc2-ae35-4c39-90dc-a6cc11873ee6` | [Meetup 공식 행사](https://www.meetup.com/ko-kr/ai-seoul-public/events/kssfbvyjcnbfb/): 10월 3일 12:00–14:00 KST | 행사 시작·종료 `EXACT_TIME`; 모집마감 `UNKNOWN` | `open→ended` |

`DATE_ONLY`는 원문 마감 시각이 아니다. 서비스 정책은 해당 날짜의 다음 날
00:00 Asia/Seoul부터 마감한다. 기업마당 126814의 추천 제외 cutoff는
2026-10-12 15:00 UTC(10월 13일 00:00 KST)로 읽기 RPC에서 확인했다.

## 등록 및 첫 자동 실행

- `supabase/backfill/002_opportunity_status_verified_evidence.sql`과 동일한
  preflight·RPC payload를 SQL Editor의 한 트랜잭션에서 실행했다. 대상 행의
  상태·시간 snapshot, 전체 17건·상태 분포, 기존 scope 부재를 확인한 뒤
  `record_opportunity_status_evidence`로 **3건**을 등록했다. 등록 직후
  scope 3건, 상태는 여전히 `open` 11/`closed` 6이었다.
- 이미 활성화된 `opportunities-status-10m`의 주기는 `*/10 * * * *`다.
  등록 후 첫 자동 실행은 2026-10-11 13:30:00 UTC(22:30 KST)에 시작했고,
  `cron.job_run_details` run ID 6은 `succeeded`였다. 작업자 기록은
  `completed`, 검사 3건, 변경 2건, SQLSTATE 없음이었다.
- 이후 운영 상태는 **`open` 9, `closed` 7, `ended` 1**로 확인했다.
  126283은 `closed`, Meetup은 `ended`, 126814는 `open`이다.
  기존 `closed` 6건은 상태가 바뀌지 않았다.

## 운영 API·화면 검증

- Vercel 기본 도메인의 목록 API는 HTTP 200, `count=11`로 자동
  `closed` 1건과 `ended` 1건을 포함했다. 두 상세 API는 HTTP 200으로
  각 상태를 반환했고, 기존 비공개 `closed` 상세는 HTTP 404였다.
  공개 archive RPC는 총 `closed/ended` 8건 중 자동 전이 2건의 ID만 반환했다.
- [운영 목록 화면](https://www.aipath.kr/opportunities.html)에서 상태 배지가
  `마감`·`종료`로 바뀌었고 두 필터는 각각 1건을 표시했다. 두 상세 화면의
  상태 배지와 한국시간 날짜 표시도 확인했다.
- 추천 API에 개인정보 없는 시험 프로필을 보냈다. HTTP 200과 기존
  `count/message/recommendations` 계약을 확인했고, 근거 등록 후
  [기업마당 126814](https://www.bizinfo.go.kr/sii/siia/selectSIIA200Detail.do?pblancId=PBLN_000000000126814)
  1건이 추천됐다. 이 직접 호출에는 OpenAI 요청이 포함됐다.
  운영 데이터 기반 후보 판정은 검증된 미래 `DATE_ONLY` 1건,
  그 외 `open` 8건 제외였다. 마감·종료 상태는 추천 후보가 아니다.

## 보류 및 제한

검증되지 않은 시각은 `EXACT_TIME`으로 승격하지 않았다. 나머지 `open`
8건은 scope에 등록하지 않았으며 현재 DB의 `open` 상태를 유지한다.

| Opportunity ID | 보류 사유 |
| --- | --- |
| `1e7a550e-477a-4965-a05c-12f3bda66fe0` | [NIPA 원문](https://www.nipa.kr/home/2-2/16953)은 10월 10일 23:00 마감이나 저장값은 09:00 KST에 해당해 충돌 |
| `032f0059-11d4-4799-80dd-84b505b86960` | [Dacon](https://dacon.io/competitions/official/236771/overview/)의 11월 1일은 리더보드 제출 마감이며 모집마감으로 확인되지 않음. Legacy 추천 후보의 수집 run ID도 기존 감사 snapshot과 달라 fail-closed 제외 |
| `a68cad2a-a609-4ad3-8b51-7cd06f3b525d` | [NAIRL](https://nairl.kr/news/nairl-innovation-showcase-2026/)은 사전 등록 마감 10월 2일, 저장 모집마감 10월 6일로 의미 충돌 |
| `c9fd1065-154e-4e10-aa1c-424c3cc852e5` | 공식 URL이 MSS 공고 목록으로, 저장 모집마감의 개별 원문을 확인하지 못함 |
| `f4939c03-5fdc-48b7-8caa-1a04af43d636` | [AI Tinkerers](https://seoul.aitinkerers.org/)의 행사 시작일은 보이나 저장된 종료 시각·모집마감 근거는 확인되지 않음 |
| `0df5bb34-905f-4d45-aa69-8255cdc698e5` | AI Festa 공식 URL에서 정확한 모집·행사 종료 근거를 확인하지 못함 |
| `a01dde02-2fae-44d4-8252-d7090366d8b4` | [Luma](https://luma.com/AI-Salon-October-Seoul-2026)의 행사 시각은 보이나 저장된 모집마감 시각은 확인되지 않음 |
| `c15c936e-97d1-4c12-8773-9fff81e9f9d1` | [KDI](https://www.kdi.re.kr/eng/share/conferInfo?sm_no=620)는 행사 시각을 공개하지만 저장된 모집마감 시각은 확인되지 않음 |

기존 `closed` 6건은 수동·과거 종료의 provenance가 없어서 자동 scope
등록과 공개 확대를 하지 않았다. 해당 공식 URL은 열람했지만 재개·종료
판정에 필요한 관리 이력은 확인하지 못했다.

현재 `open` 9건 중 과거 날짜가 저장된 5건은 원문 불확실성 때문에
여전히 운영 화면에 `모집 중`으로 보인다. 신뢰할 수 있는 원문과 저장값이
일치하거나 별도 관리자 정정 정책이 마련되기 전에는 자동 변경하지 않는다.
새 Agent Collect 게시 경로의 근거 등록 변경은 이번 Production 배포에
포함하지 않았으며 Service Publish OFF 상태를 유지했다.

## 테스트와 후속 작업

Preview 브랜치 전체 Python 테스트 **276 passed**, 두 Opportunity JavaScript의
`node --check`, `git diff --check`, 비밀 키 패턴 검사 통과.
운영 DB에서는 등록 전 읽기 전용 사전 계산과 등록 후 자동 Cron·공개 RPC를
확인했다. 격리 PostgreSQL 통합 테스트는 이번 단계에 다시 실행하지 않았다.

후속: 보류된 8건의 원문·저장값 충돌을 개별 해결하고, 신규 Agent 게시
근거 공급 경로를 Preview에서 별도 검증한다. 기존 6건의 비공개 정책은
명시적 결정 전까지 유지한다.
