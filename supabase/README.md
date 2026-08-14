# Supabase

`schema.sql`은 PostgreSQL 테이블, 관계, 인덱스, 날짜 검증, 자동 수정 시각 및 RLS 보안을 구성합니다.

예정 핵심 테이블:

- `sources`
- `categories`
- `opportunities`
- `tags`
- `opportunity_tags`

## 적용 순서

1. Supabase SQL Editor에서 `schema.sql` 전체를 실행합니다.
2. Table Editor에서 핵심 테이블 5개가 생성됐는지 확인합니다.
3. `seed.sql`을 실행해 초기 카테고리와 태그를 입력합니다.
4. `sample-opportunity.sql`로 실제 공식 출처, 첫 기회와 태그 관계를 검증합니다.

브라우저 역할인 `anon`과 `authenticated`에는 테이블 직접 접근 권한을 주지 않습니다. 서버의 Python API만 비밀 환경 변수를 통해 접근합니다.
