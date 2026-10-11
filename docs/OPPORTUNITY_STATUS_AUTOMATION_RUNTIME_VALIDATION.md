# Opportunity Status Automation — Supabase Runtime Validation

검증일: 2026-10-11 KST. 대상: 운영 Supabase `aipath-korea`, Migration 002 파일 SHA-256 `8586f315d202a3ced52fa4b45d4a7a1b7c8af1bf83251c5b69bf583debf0d0b1`.

> 아래 기존 절의 Cron 미설치·미활성화 설명은 Migration 적용 당시의 기록이다.
> 이후의 활성화와 첫 자동 실행 결과는 문서 마지막 절에 기록했다.
> 이 문서만 Preview 전용 브랜치에 기록하며 언급된 Migration·Cron SQL 파일은
> 이번 문서 전용 커밋에 포함하지 않는다.
> 이후 Production 배포·검증된 근거 3건 등록·첫 상태 전이 결과는
> [Production release](OPPORTUNITY_STATUS_AUTOMATION_PRODUCTION_RELEASE.md)에 기록했다.

## [Migration 002]

사전 감사 후 `supabase/migrations/002_opportunity_status_automation.sql`을 Supabase SQL Editor에서 **한 번 적용**했다. SQL Editor는 `Success. No rows returned`를 반환했다. 이 파일은 scope·실행 기록 테이블, 인덱스, 함수 6개를 정의한다. 기존 Opportunity를 갱신하거나 Cron을 만드는 문장은 실행되지 않았다. 운영 DB에는 `supabase_migrations.schema_migrations` 테이블이 없어 이 수동 적용은 해당 migration history에 기록되지 않았다. 재적용 금지.

## [Safety Audit]

- 운영 대상 프로젝트는 로컬 `.env`의 Supabase 프로젝트 참조와 일치했다. Secret은 출력하지 않았다.
- 적용 전 `opportunities` 17건: `open` 11, `closed` 6. 전체 행 지문: `15b1901c32a947af5c019118f61dfffe`.
- 새 테이블·함수·인덱스 이름 충돌 없음. 기존 Opportunity 트리거는 `opportunities_set_updated_at` 하나. Cron 스키마 및 `supabase_migrations` 스키마 없음. 로컬 migration 파일 번호 중복 없음.
- SQL에 기존 행 대상 `DROP`, `TRUNCATE`, `DELETE`, 백필, `cron.schedule` 없음. 새 scope의 Opportunity 외래키에 `ON DELETE CASCADE`가 있으나 Migration 적용 자체로 기존 행을 삭제하지 않는다. 작업자의 `UPDATE opportunities`는 함수 본문이며 Migration에서 호출하지 않는다.
- 운영의 `id uuid`, `status text`, 날짜·`updated_at`의 `timestamptz` 타입과 기존 상태 CHECK가 Migration 요구와 일치했다.

## [DB Objects]

적용 후 scope·실행 기록 테이블, 함수 6개, 실행 기록 기본키 및 `opportunity_status_automation_runs_recorded_idx`, CHECK·FK 제약조건을 운영 catalog에서 확인했다. 두 테이블 모두 RLS 활성화. `pg_try_advisory_xact_lock` 호출 성공. 작업자 소유자는 `postgres`이며 SQL Editor의 `postgres` 역할로 실제 실행했다.

## [Privileges / RLS]

`anon`·`authenticated`의 새 테이블 직접 SELECT 권한이 없고, `service_role`에도 새 테이블 직접 SELECT/INSERT가 없다. `service_role`의 근거 등록·공개 archive·추천 window RPC 실행권은 있고 작업자 실행권은 없다. `anon`은 이 RPC와 작업자 실행권이 없다. `service_role`로 두 읽기 RPC를 실제 호출했고, DATE_ONLY 근거 등록 RPC도 합성 행에 대해 실행해 `POLICY_KST_DAY_END` 기록을 확인한 뒤 ROLLBACK했다. 격리 PostgreSQL에서는 `anon`의 직접 테이블 접근 거부도 확인했다.

## [Runtime Tests]

운영 DB에서 합성 Opportunity만 추가하는 격리 트랜잭션을 실행하고 모두 ROLLBACK했다. 검증한 전이: `open→closed`, 자동 `closed→ongoing`, `ongoing→ended`, `open→ended`, `scheduled→open`, `scheduled→closed`, `DATE_ONLY open→closed` (총 7건). 기존 수동 `closed`, `draft`, NULL 마감은 유지됐다. DATE_ONLY의 KST 익일 00:00 경계, UTC/KST 동일 instant, 자동 archive 출처, 재실행 시 변경 0건과 `updated_at` 불변을 검증했다.

별도 롤백 트랜잭션에서 합성 행에만 실패시키는 임시 Trigger로 작업자 오류를 발생시켰다. `failed` 실행 기록과 SQLSTATE, 부분 변경 없음, Trigger 제거 후 정상 복구를 확인했다. 임시 Trigger·함수·실행 기록·합성 행은 모두 ROLLBACK됐다. 운영 두 세션 동시 실행은 하지 않았고, advisory lock의 중복 실행 건너뜀과 행 잠금 skip/retry는 격리 PostgreSQL 18.4에서 재검증했다.

## [Existing 17 Records]

모든 운영 롤백 테스트 후 17건, `open` 11/`closed` 6, 전체 행 지문 `15b1901c32a947af5c019118f61dfffe`로 적용 전과 동일했다. scope 0건, 실행 기록 0건, 합성 행 0건, 임시 Trigger·함수 없음. 기존 closed 6건 공개 정책이나 open 11건 상태를 변경하지 않았다.

## [Cron Readiness]

`pg_cron`은 설치 가능한 확장 목록에는 있으나 **미설치**다. `cron` 스키마와 `cron.schedule` 함수가 없으므로 `supabase/cron/opportunity-status-10m.sql`은 당시 실행 불가능했다. Cron 생성·활성화는 하지 않았다. scope가 0건이므로 당시 작업자를 스케줄해도 기존 17건 중 자동 전이 대상은 0건이다. 확장 설치, 시간 근거 공급/백필 검증, API·Collect 운영 배포 확인 및 preview가 활성화 전 필요하다.

## [Tests]

Python 전체 테스트: **260 passed**. 격리 PostgreSQL 18.4 통합 테스트: schema, Migration 002, 상태 경계, 시간 근거, 전이·멱등성, 백필 합성 행, 동시 advisory lock, 행 잠금, `anon` 권한 거부 통과. 당시 운영 DB의 Cron 실행·두 세션 동시성은 미검증이었다.

## [Blocking Defects]

Migration 및 상태 작업자 핵심 런타임에서 차단 결함은 발견되지 않았다. Cron 활성화는 `pg_cron` 미설치, scope 근거 0건, 운영 API·Collect 배포 상태 미검증으로 당시 보류했다. 수동 SQL Editor 적용은 migration history에 기록되지 않으므로 후속 배포에서 중복 적용을 방지해야 한다.

## [Runtime Validation 완료 여부]

Migration 객체, 권한, 상태 작업자와 오류 경로의 운영 PostgreSQL 롤백 검증은 완료. 당시 Cron 실제 실행·운영 두 세션 동시성은 검증하지 않았다.

## [Cron 활성화 가능 여부 — Migration 검증 당시]

**당시 불가.** 이 단계에서 Cron·백필·운영 Opportunity 상태 변경·Cloud Run/Scheduler·Service Publish·Git Commit/Push·Vercel 배포는 하지 않았다.

## [Cron Activation and First Automatic Run — 2026-10-11 KST]

- Preview 검증 완료 후 운영 SQL Editor에서 읽기 전용으로 재점검했다.
  Opportunity 17건, 시간 근거 scope 0건, 상태 변경 예상 대상 0건/`[]`였다.
  `pg_cron`은 미설치였으며 기존 `cron.job` 테이블도 없었다.
- 운영 DB에 `pg_cron` 1.6.4를 설치했다. 설치 직후 Cron 작업 총수 0,
  동일 이름 0, 동일 작업자 호출 0, scope 0을 다시 확인했다.
- `opportunities-status-10m`을 `*/10 * * * *` 주기로 등록·활성화했다.
  작업 ID는 1, 실행 계정은 `postgres`, 실행 SQL은
  `select * from public.reconcile_opportunity_statuses();`다.
- 첫 **자동** 실행은 2026-10-11 12:40:00 UTC (21:40 KST)에 시작했다.
  `cron.job_run_details`의 run ID 1은 `succeeded`, 작업자 실행 기록은
  `completed`, `examined_count=0`, `updated_count=0`, 오류 SQLSTATE 없음이다.
  실행 후 Opportunity 17건(`open` 11, `closed` 6), scope 0건으로
  기존 상태 분포가 유지됐다.
- `anon`·`authenticated`는 `cron` 스키마 USAGE와 `cron.job` INSERT
  권한이 없었다. 시간 근거 추가, Production 애플리케이션 배포,
  Cloud Run/Scheduler 변경은 하지 않았다. 근거 scope가 여전히 0건이므로
  이 활성화 시점의 Cron은 상태를 변경하지 않았다.
