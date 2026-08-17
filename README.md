# AI PATH KOREA

한국의 AI 교육, 공모전, 해커톤, 밋업, 콘퍼런스와 성장 기회를 한곳에서 탐색하는 무료 공개 웹 서비스입니다.

> 당신의 다음 AI 기회를 발견하세요.  
> AI Opportunity Calendar

## 배포 URL

- Vercel: https://aipath-korea.vercel.app
- Custom domain: https://aipath.kr

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

실제 비밀키는 `.env` 또는 Vercel 환경 변수에만 저장합니다. `.env.example`을 복사해 로컬 설정을 만들되, 실제 값이 들어간 파일은 Git에 커밋하지 않습니다.

필요한 환경 변수:

```text
SUPABASE_URL=
SUPABASE_SECRET_KEY=
OPENAI_API_KEY=
```

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

## 데이터 원칙

- 신청 마감일과 실제 행사일을 구분합니다.
- 모든 기회에 공식 원문 URL과 마지막 확인 시각을 기록합니다.
- 이용 조건이 확인되지 않은 원문과 이미지는 복제하지 않습니다.
- AI는 DB에 존재하는 기회만 추천합니다.
- 추천 결과는 참가 자격 충족이나 선정 가능성을 보장하지 않습니다.
