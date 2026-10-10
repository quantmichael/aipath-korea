# Agent Collect Runtime Integration — PHASE 3.5–3.6

> PHASE 7.3B 현재 주의: 아래 `register-source --activate` 예시는 당시 기록이며 지금은 **거부된다**. 활성화는 `docs/SOURCE_APPROVAL.md`의 사람 검토→승인→활성화 Python 서비스 API만 허용한다. Agent migration 004는 2026-10-04 적용·검증됐다.

> PHASE 4.5의 Discovery 003 적용과 실DB CRUD는 [DISCOVERY_RUNTIME.md](DISCOVERY_RUNTIME.md)를 따른다. 아래 내용은 PHASE 3.5–3.6 당시 수집 Runtime 검증 기록이다.

> 아래 PHASE 3.5·초기 PHASE 3.6 감사와 migration 적용 절은 당시의 기록이다. **현재 검증 상태는 맨 아래 `PHASE 3.6 최종 Runtime DB E2E` 절을 기준으로 한다.**

## 검증 범위와 실제 결과 (2026-10-03)

PHASE 3의 원문 수집 기능에 독립 CLI와 Supabase 런타임 조립 코드를 추가했다. 기존 Frontend, Backend, `collect/`, Service Table, Agent SQL 파일은 변경하지 않았다. 이번 환경에서 확인된 것과 확인되지 않은 것을 구분한다.

| 항목 | 결과 |
| --- | --- |
| 기존 Supabase 설정 | 저장소 루트 `.env`에 `SUPABASE_URL`, `SUPABASE_SECRET_KEY` 이름이 존재한다. 값은 출력하지 않았다. 현재 셸에는 export되어 있지 않다. |
| Supabase 읽기 연결 | 해당 설정으로 REST API 읽기 전용 요청을 수행했다. 기존 `sources`의 `id` 조회는 HTTP 200. |
| Agent 테이블 | 7개 모두 REST 조회 HTTP 404. 현재 API에서 사용 불가하며 마이그레이션 미적용으로 추정한다. PostgreSQL catalog 직접 조회는 하지 못했다. |
| Agent 마이그레이션 | **적용하지 않음.** 현재 환경에 DB DSN/DDL 권한이 확인되지 않았고 `psql`/Supabase CLI도 없다. REST 서비스 키만으로 임의의 DDL 실행을 시도하지 않았다. |
| 실제 공개 HTTP | `https://example.com/`에 GET 한 번, HTTP 200, HTML 577 bytes. 메모리 Repository + 임시 `LocalRawContentStorage`로 Runner `SUCCEEDED`, raw 저장/재조회와 ID 연결 확인. 임시 파일은 테스트 종료 시 삭제됐다. |
| 실패 경로 | 지원하지 않는 `BROWSER` 방법으로 네트워크 요청 없이 메모리 Repository에 `FAILED` Run 기록, raw 미생성 확인. |
| 실제 Agent Repository/DB provenance | Agent 테이블이 사용 불가하므로 **미검증**. Source/Run/RawDocument 실제 DB 쓰기는 수행하지 않았다. |

## 연결과 환경변수

`agent_collect/runtime.py`가 Supabase 클라이언트를 생성하고 Agent 전용 Source/Run/RawDocument Repository를 조립한다. 기존 Backend 코드를 import하지 않는다. 기본 클라이언트 자격증명은 `AGENT_SUPABASE_URL`, `AGENT_SUPABASE_SERVICE_KEY`이며, 없으면 기존 서버 변수 `SUPABASE_URL`, `SUPABASE_SECRET_KEY`를 사용한다. Agent 설정은 `agent_collect/config/defaults.toml`과 `AGENT_*` 환경변수 override가 담당한다. 비밀값은 설정 파일·로그·문서에 기록하지 않는다. `AGENT_SUPABASE_SERVICE_KEY`는 서버 전용이며 브라우저·클라이언트에 노출하면 안 된다.

CLI는 `.env`를 자동 로드하지 않는다. 실행 프로세스 환경에 위 변수를 안전하게 주입해야 한다. 따라서 이 환경에서 `config-check`가 출력하는 `credentials: missing`은 파일에 키가 없다는 뜻이 아니라 **현재 프로세스 환경변수에 없다는 뜻**이다. 실제 DB 명령에는 프로젝트 Python 의존성(`supabase`)이 설치되어 있어야 한다. `config-check`는 표준 라이브러리만 사용하고 네트워크 연결을 하지 않는다. 이 작업 환경의 Python에는 `supabase` SDK와 `python-dotenv`가 설치되지 않아 SDK 기반 실제 Repository 실행은 하지 못했다. `.env`의 키가 Agent 테이블 DML에 필요한 권한인지도 확인되지 않았다.

기존 Backend는 `api/opportunities.py`에서 `load_dotenv()` 후 `SUPABASE_URL`과 `SUPABASE_SECRET_KEY`로 `create_client()`를 호출한다. Agent CLI는 별도 조립 코드이며 Backend 시작/실행 방식에 의존하지 않는다. 장기적으로 Agent 전용 최소 권한 DB 역할 또는 분리 자격증명과 운영 secret manager를 권장한다.

## 마이그레이션 안전 감사 및 적용 절차

적용 순서는 `001_agent_foundation.sql` → `002_collection_foundation.sql`이다. 001은 `public.agent_*` 7개 테이블, Agent 간 FK/복합 FK, CHECK, 인덱스, RLS, `service_role` grant를 만든다. 기존 Service Table을 참조하거나 변경하지 않는다. 002는 `agent_sources.last_collected_at`을 추가하고 `agent_sources`·`agent_collection_runs`의 collection method CHECK를 새 방법 값으로 교체한다. `DROP CONSTRAINT IF EXISTS`는 **Agent 테이블의 기존 CHECK 두 개만** 대상으로 하며 `DROP TABLE`, `TRUNCATE`, `DELETE`는 없다. 파일별 `begin`/`commit`이 있다. 001의 `CREATE TABLE`은 재실행 안전하지 않으므로 이미 적용된 DB에 중복 실행하면 안 된다. 002의 CHECK 교체도 대상 테이블·기존 값·실제 constraint 이름을 먼저 확인해야 한다.

실제 적용 전에는 DB 관리자/운영자가 PostgreSQL catalog에서 `agent_*` 객체명 및 제약 이름 충돌, 기존 Agent 객체의 정의·행 수, PostgreSQL 확장/`gen_random_uuid()`, 실행 역할의 DDL 권한, 백업 및 롤백 방식을 확인해야 한다. 이번 REST 404는 API 노출 부재를 보여줄 뿐 catalog 수준의 부재 증명은 아니다. Supabase SQL Editor 또는 승인된 PostgreSQL migration 경로로 두 파일을 **순서대로 한 번씩** 실행한다. `supabase db push`처럼 저장소의 다른 기존 migration까지 함께 적용하는 포괄 명령은 사용하지 않는다. 성공 후 Agent 7개 테이블만 조회 가능 여부, RLS/grant, FK, Source 저장/조회, 서비스 테이블 변경 없음(사전/사후 스키마 비교)을 확인한다. 서비스 키는 DML용이지 DDL 연결 권한의 증명이 아니다.

## CLI와 수동 스모크 절차

저장소 루트에서:

```bash
python3 -m agent_collect config-check
python3 -m agent_collect register-source --name "Runtime smoke: example.com" --url https://example.com/ --method http_html --type official --activate
python3 -m agent_collect collect-source --source-id SOURCE_UUID --raw-dir /absolute/private/raw-dir
python3 -m agent_collect show-run --run-id RUN_UUID
python3 -m agent_collect show-document --document-id DOCUMENT_UUID
```

명령은 `register-source` 결과의 Source UUID, `collect-source` 결과의 Run/RawDocument UUID를 다음 명령에 넘긴다. `collect-source`는 `--raw-dir`를 필수로 요구하며 임시 디렉터리를 운영 저장소로 쓰지 않는다. Agent 테이블에 migration이 적용되고 자격증명·SDK·egress가 갖춰진 뒤에만 DB 명령을 실행한다. CLI 출력에는 비밀키나 raw 본문을 포함하지 않는다. Source 등록은 `manual_cli` provenance를 남긴다. 운영 smoke 레코드는 추가로 명확히 표시하고 정리 정책을 결정해야 한다. 이번 작업에서는 실제 DB smoke 레코드를 생성하지 않았다.

검증 순서: Source 등록/조회/수정 → 활성화 → `collect-source` → `show-run`/`show-document` → `raw_dir` 아래 `content_ref` 원문 검증 → Source ID, Run ID, RawDocument ID·hash 비교. 실패 검증은 별도 테스트 Source에서 지원하지 않는 `browser` 방법을 명시적으로 사용해 `FAILED` Run을 확인한다. 외부 공개 사이트에는 최소 요청만 하고 robots/이용조건을 운영자가 확인한다.

## Raw Storage와 멱등성

현재 `LocalRawContentStorage`는 `--raw-dir/YYYY/MM/DD/SHA256-UUID.raw`에 권한 `0600`으로 bytes를 저장하고 DB에는 `local:` 참조와 SHA-256만 넣는다. 저장 경로는 모든 실행에서 동일한 영속 볼륨이어야 한다. DB와 로컬 파일은 원자적 트랜잭션이 아니며, 여러 호스트/서버리스 인스턴스가 공유하지 못하고 백업·보존·접근통제·재해복구가 어렵다. Production에서는 같은 Storage 포트를 구현한 공유 객체 저장소(Supabase Storage/S3 호환), 접근권한·암호화·retention/GC·참조 무결성·복구 절차가 필요하다. 이번 PHASE에서 신규 Storage를 구축하지 않았다.

Registry는 canonical URL을 조회한 후 저장하며 DB의 `canonical_url` unique가 최종 중복 방어다. 그러나 동시 등록 경합에서는 한 요청이 unique 오류를 받을 수 있다. Runner는 재실행마다 새 Run ID와 raw 파일/문서 ID를 만들기 때문에 동일 수집 작업의 재실행은 중복 저장된다. 현재 파일명 UUID는 충돌만 피하며 멱등성을 제공하지 않는다. 다음 단계에서는 `(source_id, collection_operation_key)` 고유 제약 또는 일정/요청별 실행 key, DB 충돌 시 기존 Source 재조회, raw hash+Source+URL 정책, 중단 후 재개·고아 파일 청소를 결정해야 한다. 이 작업에서는 기존 migration을 확장하지 않았다.

## 알려진 제약과 Production 전 작업

- Agent 테이블 실DB와 Repository DML은 미검증이며, migration 적용 후 별도 integration 테스트가 필요하다. 비밀정보를 사용하는 테스트는 일반 unit suite와 분리하고 명시적 opt-in으로 실행한다.
- 실제 HTTP 스모크는 `example.com` 한 번에 한정되고 Agent DB에 기록되지 않았다. 특정 Source의 접근 정책·rate limit 검토는 배포 시 별도로 필요하다.
- CLI는 수동 실행용이다. Scheduler/Daemon, Discovery, LLM, 추출·검증·게시, Service DB 쓰기는 없다.
- Source config의 `headers`에는 비밀을 넣지 않고 `header_env`로 런타임 주입해야 한다. 현재 CLI는 인증 헤더 설정 명령을 제공하지 않는다.
- Agent 전용 DDL 권한·자격증명 분리, DB migration 이력/롤백, 구조화 로그와 경보, 공유 raw 저장소, 멱등성 및 재시도 정책을 운영 전 확정해야 한다.

## PHASE 4 진행 조건

기초 수집 코드와 오프라인 테스트는 준비됐지만 PHASE 3.5의 **실제 DB E2E 완료 조건은 미충족**이다. Agent migration 적용과 실제 Repository/Source→Run→RawDocument 연결 검증이 끝난 뒤 후속 기능을 시작하는 것을 권장한다.

## PHASE 3.6 DDL 접근 및 DB E2E 재검증 (2026-10-03)

이번 단계는 기능 구현 없이 실제 DB 적용을 시도하기 위한 접근·안전 감사였다. 결과는 **DDL 접근 미확보로 중단**이다.

- 로컬 `supabase`/`psql`/`pgcli` 실행 파일과 프로젝트 `supabase/config.toml`·`supabase/.temp` 링크가 없다. `SUPABASE_ACCESS_TOKEN`, `SUPABASE_DB_PASSWORD`, PostgreSQL DSN 및 `PG*` 접속 환경변수도 현재 셸과 `.env`에 없다. `.env`에는 REST API용 `SUPABASE_URL`, `SUPABASE_SECRET_KEY`만 있다. REST 키를 PostgreSQL 비밀번호로 사용하거나 REST API로 DDL을 실행하지 않았다.
- 사용 가능한 브라우저의 Supabase 공식 대시보드는 로그인 화면이어서 SQL Editor 접근을 확인할 수 없었다. Chrome의 기존 프로필에서도 Supabase 홈페이지는 로그아웃 상태였다. 인증을 새로 시도하거나 자격증명을 입력하지 않았다.
- 읽기 전용 REST 재조회: 기존 `sources`는 HTTP 200, Agent 7개 테이블은 모두 HTTP 404. 이는 API 접근 불가의 증거이며 PostgreSQL catalog 수준의 객체 부재·이름 충돌 없음·DDL 권한을 증명하지 않는다.
- 정적 감사: 001은 Agent 테이블 7개, Agent 전용 FK/인덱스/RLS/grant만 포함한다. 002는 Agent 테이블 두 개의 method CHECK를 교체하고 `last_collected_at`을 추가한다. 두 파일 모두 트랜잭션으로 감싸졌고 Service Table 변경, `DROP TABLE`, `TRUNCATE`, 데이터 `DELETE`가 없다. 002의 `DROP CONSTRAINT IF EXISTS` 두 건은 Agent 테이블 대상이다. 실제 DB의 객체명/constraint 충돌, `gen_random_uuid()` 가용성, 실행 역할 권한은 DDL 연결 없이는 확인할 수 없다. 따라서 001/002를 실행하지 않았다.
- Repository 실DB CRUD, 실제 HTTP→Agent DB E2E, FK/ID provenance, 실DB `FAILED` Run은 실행하지 않았다. 추가 HTTP 요청과 Agent/Service DB 쓰기는 모두 0건이다. 신규 smoke 데이터가 없어 삭제할 것도 없다.
- `canonical_url` unique는 이미 001에 있다. 동시 등록 시 unique 충돌을 재조회하는 동작과 수집 재실행의 operation key 의미가 확정되지 않아 003 migration은 만들지 않았다. 같은 Source 재실행 시 여러 Run/RawDocument를 보존하는 것이 이력인지 중복인지 먼저 결정해야 한다.
- 전체 오프라인 unit suite: `python3 -m unittest discover -s tests -q` → 48개 통과.

재개에는 **대상 Supabase 프로젝트에 대한 DDL 권한**을 가진 정식 경로 하나가 필요하다: (1) 프로젝트에 연결된 Supabase CLI와 DB 비밀번호/접근 토큰, (2) Supabase Dashboard의 해당 프로젝트 SQL Editor에 로그인된 세션, 또는 (3) Dashboard **Connect**에서 복사한 direct/session PostgreSQL connection string과 DB 비밀번호를 안전하게 주입한 환경변수. API `SUPABASE_SECRET_KEY`만으로는 충분하지 않다. [Supabase 연결 문서](https://supabase.com/docs/guides/database/connecting-to-postgres)는 migration에 PostgreSQL 연결을 사용하고 Dashboard Connect에서 문자열을 얻도록 안내한다. 연결 확보 후 catalog/권한/백업을 먼저 확인하고, 001→002를 중복 없이 적용하며, Agent 테이블 접근·Repository CRUD·DB E2E·실패 Run·Service Table 무변경을 확인한다. 실제 smoke 행은 `runtime_smoke_test`로 표시해 검증 후 **정확한 ID만** Agent FK 역순으로 정리할지 운영자가 결정한다.

## PHASE 3.6 Supabase Dashboard migration 적용 (2026-10-03)

사용자가 현재 로그인된 Chrome 세션을 명시적으로 사용하도록 요청한 뒤, `aipath-korea`의 `main PRODUCTION` Supabase Dashboard SQL Editor에서 001→002를 **각 한 번씩** 적용했다. Dashboard 프로젝트 ID가 로컬 `.env`의 `SUPABASE_URL` 프로젝트 ID와 일치하는 것을 값 노출 없이 확인했다. REST 키를 PostgreSQL credential로 사용하거나 REST DDL을 실행하지 않았다.

실행 전 두 파일의 원문을 재검토했다. 001은 Agent 전용 테이블·인덱스·FK·RLS·권한만 생성하고, 002는 Agent 테이블의 column/CHECK만 변경한다. Service Table 대상 SQL, `DROP TABLE`, `TRUNCATE`, 데이터 `DELETE`는 없다. 002의 `DROP CONSTRAINT IF EXISTS` 2건은 Agent method CHECK만 대상으로 한다. SQL Editor에 붙여 넣은 001/002는 각각 로컬 파일(마지막 개행 제외)과 SHA-256이 같음을 실행 전에 확인했다.

사전 DB catalog 점검: `current_user=postgres`, `public` CREATE 권한 `true`, `gen_random_uuid()` 가용 `true`, 기존 `public.agent_*` 객체 0개. 기존 서비스 테이블 7개(`opportunities`, `sources`, `categories`, `tags`, `opportunity_tags`, `collection_runs`, `opportunity_candidates`)의 컬럼 정의 기준값은 106개 컬럼 / MD5 `e1aadcf8393fdef169bec702b739fb9b`였다.

001과 002 각각 SQL Editor에서 `Success. No rows returned`로 완료됐다. 001 후 catalog에서 Agent 테이블 7개를 확인했다. 002 후 catalog 집계는 **테이블 7개, `agent_sources.last_collected_at` 존재, `http_html`을 허용하는 method CHECK 2개, RLS 활성화 테이블 7개**다. `agent_sources`, `agent_collection_runs`, `agent_raw_documents`, `agent_candidates`, `agent_verifications`, `agent_publish_queue`, `agent_logs`는 기존 서버 자격증명으로 REST `id` 조회 시 모두 HTTP 200이었다. 서비스 7개 테이블의 컬럼 기준값은 적용 후에도 106개 / 동일 MD5였다. 이번 적용에서 서비스 행을 쓰거나 삭제하지 않았다.

이번 사용자 요청 범위는 migration 적용과 테이블 확인이다. 실제 Agent Repository CRUD, 공개 HTML→실제 Agent DB 수집, RawDocument DB provenance, 실패 Run DB E2E는 **아직 실행하지 않았다**. PHASE 4도 시작하지 않았다. 001/002는 이미 적용됐으므로 재실행하지 말아야 한다. 다음 DB E2E를 별도로 진행할 때 `runtime_smoke_test` 표시, 정확한 행 ID 추적, 로컬 raw 파일 위치·정리 정책을 먼저 정한다.

## PHASE 3.6 최종 Runtime DB E2E (2026-10-03)

위 migration 적용 이후 별도 사용자 요청으로 실제 Supabase Agent 테이블에서 SourceRepository create/get/update, CollectionRunRepository create/update, RawDocumentRepository create/get을 검증했다. 공개 HTML을 HTTP GET하여 LocalRawContentStorage에 보존하고 RawDocument metadata를 DB에 기록했으며, Source→Run→RawDocument의 실제 ID/FK와 `SUCCEEDED` 상태를 조회해 확인했다. 별도 안전한 실패 경로에서 `FAILED` Run도 실DB에 기록·확인했다. `runtime_smoke_test`로 식별한 Agent 전용 행은 테스트 후 정리했고, 기존 Service Data는 변경하지 않았다. 당시 전체 오프라인 테스트 48개가 통과했다. PHASE 4에서 추가한 Discovery 003은 아직 적용하지 않았으며 실제 Discovery DB CRUD/검색 API 테스트는 별도 작업이다.
