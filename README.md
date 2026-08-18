# AI PATH KOREA

한국의 AI 교육, 공모전, 해커톤, 밋업, 콘퍼런스와 성장 기회를 한곳에서 탐색하는 무료 공개 웹 서비스입니다.

> 당신의 다음 AI 기회를 발견하세요.  
> AI Opportunity Calendar

## 배포 URL

- Vercel: https://aipath-korea.vercel.app
- Custom domain: https://aipath.kr

## 저장소

- GitHub: https://github.com/quantmichael/aipath-korea
- Commit history: https://github.com/quantmichael/aipath-korea/commits/main

## 서비스 소개

AI PATH KOREA는 AI를 학습하거나 업무와 진로에 접목하려는 사용자가 국내 AI 관련 성장 기회를 빠르게 찾도록 돕습니다.

사용자는 등록된 기회를 검색/필터링하고, 상세 페이지에서 공식 출처와 일정 정보를 확인할 수 있습니다. AI 추천 페이지에서는 자신의 수준, 관심 분야, 참여 목적, 선호 방식, 비용 조건, 가능 기간을 입력하면 현재 DB에 등록된 기회 중 최대 3개를 추천받습니다.

## 주요 기능

- 홈: 서비스 목적과 주요 기회 유형 안내
- 기회 탐색: AI 교육, 공모전, 해커톤, 밋업, 콘퍼런스 등 검색/필터
- 기회 상세: 주최기관, 모집 기간, 행사 기간, 장소, 참가비, 공식 URL 확인
- AI 추천: 사용자 조건을 입력받아 DB 기반 맞춤 기회 추천
- 서비스 소개: 데이터 운영 원칙과 추천 기준 안내

## 기술 스택

- Frontend: HTML, CSS, Vanilla JavaScript
- Backend: Python, FastAPI, Vercel Serverless Functions
- Database: Supabase PostgreSQL
- AI: OpenAI API
- Deploy: GitHub, Vercel

HTML은 페이지의 구조와 의미를 담당합니다. 홈, 기회 탐색, 상세 페이지, AI 추천 폼, 서비스 소개 화면의 제목, 버튼, 입력 필드, 결과 영역을 HTML로 구성했습니다.

CSS는 화면의 스타일과 반응형 레이아웃을 담당합니다. 공통 헤더, 카드, 필터 패널, 추천 결과, 모바일 화면에서의 배치 변화와 간격 조정은 `public/css/style.css`에서 관리합니다.

JavaScript는 사용자 동작과 데이터 흐름을 담당합니다. 검색/필터 입력을 처리하고, `fetch`로 `/api/opportunities`와 `/api/recommend`를 호출한 뒤 응답 데이터를 화면 카드와 안내 메시지로 렌더링합니다.

프론트엔드는 정적 파일(`public/`)로 구성해 Vercel에서 빠르게 제공하고, 백엔드는 `api/` 폴더의 Python 함수로 분리했습니다. 이렇게 나누면 브라우저에 노출되면 안 되는 Supabase secret key와 OpenAI API key를 서버 환경 변수로만 다룰 수 있고, 화면 수정과 API 로직 수정을 독립적으로 배포/검증할 수 있습니다.

## 폴더 구조

```text
aipath-korea/
├── api/                 # Python Vercel Functions
├── docs/                # 서비스 기획서와 제출 증빙 안내
│   └── evidence/        # 데스크톱/모바일/AI 기능/AI 코딩 도구 증빙
├── public/              # 정적 프론트엔드 파일
│   ├── css/             # 공통 스타일
│   ├── js/              # 화면별 JavaScript
│   ├── index.html       # 홈
│   ├── opportunities.html
│   ├── opportunity.html
│   ├── recommend.html
│   └── about.html
├── supabase/            # DB 스키마와 초기 데이터
├── requirements.txt     # Python 패키지
└── vercel.json          # Vercel 배포 설정
```

## 환경 변수

실제 비밀키는 `.env` 또는 Vercel 환경 변수에만 저장,  `.env.example`을 복사해 로컬 설정을 만들되, 실제 값이 들어간 파일은 Git에 커밋하지 않습니다.

필요한 환경 변수:

```text
SUPABASE_URL=
SUPABASE_SECRET_KEY=
OPENAI_API_KEY=
```

### API 키 유출 대응 절차

API 키나 Supabase secret key가 코드, README, 스크린샷, 커밋 이력에 노출된 것이 의심되면 아래 순서로 대응합니다.

1. 노출된 키를 즉시 폐기하거나 재발급합니다.
2. Supabase, OpenAI, Vercel에 새 키를 등록합니다.
3. 로컬 `.env` 파일의 값을 새 키로 교체합니다.
4. GitHub 코드와 README, `docs/evidence/` 스크린샷에 키가 남아 있지 않은지 확인합니다.
5. 키가 커밋 이력에 포함되었다면 해당 키는 재사용하지 않고, 필요 시 Git 이력 정리 도구로 노출 흔적을 제거합니다.
6. Vercel에서 Production 재배포를 실행하고 `/api/opportunities`, `/api/recommend`가 정상 동작하는지 확인합니다.

### 환경 변수 또는 프레임워크 변경 영향 체크리스트

환경 변수 이름, API 경로, 백엔드 프레임워크 설정을 바꾸면 다음 항목을 함께 확인합니다.

- `.env.example`, 로컬 `.env`, Vercel Environment Variables의 변수 이름이 모두 일치하는지 확인합니다.
- `api/opportunities.py`에서 읽는 환경 변수 이름과 Vercel에 등록한 이름이 같은지 확인합니다.
- `requirements.txt`에 필요한 Python 패키지가 모두 포함되어 있는지 확인합니다.
- 프론트엔드의 `fetch` 경로가 배포 환경에서는 `/api/...`, 로컬 환경에서는 `http://127.0.0.1:8000/api/...`로 동작하는지 확인합니다.
- CORS 허용 origin에 로컬 미리보기 주소가 포함되어 있는지 확인합니다.
- 변경 후 로컬 API 서버와 Vercel Production 배포에서 네비게이션, 기회 탐색, AI 추천 기능을 다시 테스트합니다.

### 기능 확장 단계 체크리스트

새 기능을 추가할 때는 기획, 구현, 검증, 배포를 한 번에 바꾸지 않고 아래 순서로 진행합니다.

1. 확장 목적 정의: 어떤 사용자 문제를 해결하는지, 기존 기회 탐색/AI 추천 흐름과 어떻게 연결되는지 작성합니다.
2. 영향 범위 확인: 변경 대상이 `public/` 화면인지, `api/` 백엔드인지, `supabase/` 스키마인지, 환경 변수 추가가 필요한지 확인합니다.
3. 데이터 변경 검토: 새 필드나 테이블이 필요하면 Supabase schema/seed 변경과 기존 데이터 호환성을 먼저 확인합니다.
4. API 계약 정의: 새 엔드포인트, 요청 필드, 응답 구조, 오류 메시지를 문서화한 뒤 프론트에서 호출합니다.
5. 프론트엔드 구현: HTML 구조, CSS 반응형 스타일, JavaScript 이벤트 처리와 로딩/오류 상태를 함께 구현합니다.
6. 보안 검토: API 키, secret, 관리자용 데이터가 브라우저 JavaScript나 스크린샷에 노출되지 않는지 확인합니다.
7. 로컬 테스트: Live Preview와 로컬 API 서버에서 기존 기능(기회 탐색, 상세, AI 추천)과 새 기능을 함께 확인합니다.
8. 배포 전 점검: `python3 -m py_compile api/opportunities.py`, `git diff --check`를 실행하고 README/기획서 변경이 필요한지 확인합니다.
9. 배포 및 모니터링: `git push origin main` 후 Vercel Production 배포가 `Ready`인지 확인하고, 브라우저 Console/Network와 Vercel Runtime Logs를 점검합니다.

기능 확장 시 영향 평가 항목:

- 사용자 영향: 기존 메뉴 이동, 검색/필터, AI 추천 결과 표시가 깨지지 않는가?
- 데이터 영향: 기존 Supabase 테이블과 seed 데이터가 계속 조회되는가?
- API 영향: 기존 `/api/opportunities`, `/api/opportunities/{slug}`, `/api/recommend` 응답 구조가 유지되는가?
- 비용/성능 영향: AI 호출 횟수, 후보 데이터 크기, 응답 시간이 증가하지 않는가?
- 운영 영향: 추가 환경 변수, 도메인, CORS, Vercel 설정 변경이 필요한가?
- 제출/문서 영향: README, 서비스 기획서, 증빙 스크린샷을 갱신해야 하는가?

## 로컬 실행 방법

1. 의존성을 설치합니다.

```bash
python3 -m pip install -r requirements.txt
```

2. `.env.example`을 참고해 `.env` 파일에 환경 변수를 입력합니다.

3. API 서버를 실행합니다.

```bash
python3 -m uvicorn api.opportunities:app --reload --host 127.0.0.1 --port 8000
```

4. VS Code Live Preview 또는 정적 서버로 `public/index.html`을 엽니다.

로컬 프론트엔드는 `localhost` 또는 `127.0.0.1`에서 실행될 때 `http://127.0.0.1:8000/api/...`를 호출합니다.

## 배포 방법

1. GitHub 저장소에 코드를 push합니다.
2. Vercel 프로젝트를 GitHub 저장소와 연결합니다.
3. Vercel 환경 변수에 `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `OPENAI_API_KEY`를 등록합니다.
4. Production 배포가 완료되면 Vercel URL에서 네비게이션, 반응형 화면, AI 추천 기능을 확인합니다.
5. 필요하면 Vercel Domains에서 커스텀 도메인을 연결합니다.

### 배포 후 문제 진단 및 재배포 순서

배포 URL에서 문제가 발생하면 아래 순서로 원인을 확인하고 수정합니다.

1. Vercel 프로젝트의 `Deployments` 탭에서 최신 Production 배포 상태가 `Ready`인지 확인합니다.
2. 배포가 실패했으면 해당 배포 상세 화면의 `Build Logs`와 `Runtime Logs`를 확인합니다.
3. 브라우저 개발자 도구의 `Console` 탭에서 JavaScript 오류를 확인합니다.
4. 브라우저 개발자 도구의 `Network` 탭에서 `/api/opportunities`, `/api/recommend` 요청 상태 코드를 확인합니다.
5. API 응답이 500 또는 502이면 Vercel 환경 변수 3개가 등록되어 있는지 다시 확인합니다.
6. 로컬에서 `python3 -m py_compile api/opportunities.py`로 Python 문법 오류를 확인합니다.
7. 로컬에서 API 서버와 Live Preview를 실행해 같은 문제가 재현되는지 확인합니다.
8. 원인을 수정한 뒤 `git add`, `git commit`, `git push origin main`을 실행합니다.
9. GitHub push 후 Vercel이 자동 재배포하는지 확인하고, 새 Production 배포가 `Ready`가 되면 배포 URL에서 다시 테스트합니다.

주요 확인 포인트:

- 화면이 깨지면 HTML/CSS 경로와 반응형 스타일을 확인합니다.
- 데이터가 표시되지 않으면 Supabase 환경 변수와 `/api/opportunities` 응답을 확인합니다.
- AI 추천이 실패하면 OpenAI 환경 변수, `/api/recommend` 응답, Vercel Runtime Logs를 확인합니다.
- 도메인이 연결되지 않으면 Vercel Domains 상태와 DNS의 A/CNAME 레코드를 확인합니다.

## AI 응답 지연 개선 방안

AI 추천 기능은 외부 AI API와 DB 조회를 함께 사용하므로 네트워크 상태, 모델 응답 속도, 후보 데이터 양에 따라 지연될 수 있습니다. 지연을 줄이기 위해 아래 전략을 적용하거나 운영 단계에서 적용할 수 있도록 설계합니다.

- 로딩 상태 표시: 추천 요청 중 버튼을 비활성화하고 `등록된 기회를 비교하고 있습니다.` 메시지를 표시합니다.
- 후보 데이터 제한: 추천 API는 DB에서 최대 30개의 후보만 조회해 AI에 전달합니다.
- 구조화 응답 사용: AI 응답을 정해진 JSON 구조로 파싱해 후처리 시간을 줄이고 잘못된 응답을 걸러냅니다.
- 캐시 전략: 동일한 조건의 추천 요청은 일정 시간 서버 또는 외부 캐시에 저장해 반복 호출을 줄일 수 있습니다.
- 모델 대체: 응답 지연이나 비용 문제가 커지면 더 가벼운 모델을 기본값으로 사용하고, 실패 시 규칙 기반 추천 결과를 반환할 수 있습니다.
- 요약/페이징: 후보 기회가 많아지면 전체 설명 대신 제목, 요약, 일정, 태그만 AI에 전달하고 후보를 페이지 단위로 나눕니다.
- 실패 안내: API 오류나 서버 연결 실패 시 사용자에게 `잠시 후 다시 시도해 주세요.` 형태의 안내 메시지를 표시합니다.

## 데이터 원칙

- 신청 마감일과 실제 행사일을 구분합니다.
- 모든 기회에 공식 원문 URL과 마지막 확인 시각을 기록합니다.
- 이용 조건이 확인되지 않은 원문과 이미지는 복제하지 않습니다.
- AI는 DB에 존재하는 기회만 추천합니다.
- 추천 결과는 참가 자격 충족이나 선정 가능성을 보장하지 않습니다.

## 수집 대상과 크롤링 전략

초기 수집 대상 후보와 운영 단계별 자동화 전략은 `docs/crawling-sources.md`에 정리합니다. 공식 출처를 우선하고, 민간 모음 플랫폼은 발견용으로만 사용한 뒤 주최사 원문을 확인해 등록합니다.
