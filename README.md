# AI PATH KOREA

한국의 AI 교육, 공모전, 해커톤, 밋업, 콘퍼런스와 성장 기회를 한곳에서 탐색하는 무료 공개 웹 서비스입니다.

> 당신의 다음 AI 기회를 발견하세요.  
> AI Opportunity Calendar · aipath.kr

## 현재 단계

- 서비스 기획 완료
- Supabase PostgreSQL 구조 설계 완료
- 화면 및 사용자 흐름 설계 완료
- 프로젝트 기본 구조 준비 완료
- 기능 구현 전 상태

## 예정 기술 구성

- 프론트엔드: HTML, CSS, Vanilla JavaScript
- 백엔드: Python 기반 Vercel Functions
- 데이터베이스: Supabase PostgreSQL
- AI: DB 조회 결과에 근거한 맞춤 기회 추천
- 배포: GitHub + Vercel

## 폴더 구조

```text
aipath-korea/
├── api/                 # Python Vercel Functions
├── css/                 # 공통 스타일
├── docs/                # 기획서와 제출 문서
│   └── evidence/        # 데스크톱·모바일·AI 기능 증빙
├── images/              # 사용 권한이 확인된 이미지
├── js/                  # 화면별 JavaScript
├── supabase/            # DB 스키마와 초기 데이터
├── index.html           # 홈
├── opportunities.html   # 기회 탐색
├── opportunity.html     # 기회 상세
├── recommend.html       # AI 맞춤 추천
├── about.html           # 서비스 소개
├── requirements.txt     # Python 패키지
└── vercel.json          # Vercel 배포 설정
```

## 환경 변수

실제 비밀키는 `.env` 또는 Vercel 환경 변수에만 저장합니다. `.env.example`을 복사해 로컬 설정을 만들되, 실제 값이 들어간 파일은 Git에 커밋하지 않습니다.

## 데이터 원칙

- 신청 마감일과 실제 행사일을 구분합니다.
- 모든 기회에 공식 원문 URL과 마지막 확인 시각을 기록합니다.
- 이용조건이 확인되지 않은 원문·이미지를 복제하지 않습니다.
- AI는 DB에 존재하는 기회만 추천합니다.
