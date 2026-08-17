# AI 웹 개발 과제 제출 체크리스트

기준 문서: `AI 웹 개발- 내 아이디어를 현실로, AI 웹 서비스 빌딩 .pdf`

## 필수 제출 패키지 5종

| 항목 | 상태 | 현재 증빙/위치 | 보완 필요 |
| --- | --- | --- | --- |
| 배포된 웹 서비스 | 완료 | https://aipath-korea.vercel.app | 커스텀 도메인 연결 완료 후 `https://aipath.kr` 추가 확인 |
| GitHub 저장소 | 완료 | https://github.com/quantmichael/aipath-korea | 없음 |
| README.md | 완료 | `README.md` | 배포 URL이 변경되면 업데이트 |
| 서비스 기획서 | 완료 | `docs/service-plan.md` | 없음 |
| 증빙 자료 | 준비 필요 | `docs/evidence/` | 스크린샷 파일 저장 필요 |

## 기능 요구사항 점검

| 요구사항 | 상태 | 확인 내용 |
| --- | --- | --- |
| 웹 서비스 아이디어 정의 | 완료 | AI 기회 탐색/추천 서비스 |
| 목적과 타겟 사용자 정의 | 완료 | `docs/service-plan.md` |
| 3개 이상 페이지/섹션 | 완료 | 홈, 기회 탐색, AI 추천, 서비스 소개, 상세 페이지 |
| 메뉴 이동 | 완료 | 공통 헤더 네비게이션 |
| AI 기능 1개 이상 | 완료 | `/api/recommend`, `public/js/recommend.js` |
| 프로젝트 구조 구성 | 완료 | `public/`, `api/`, `supabase/`, `docs/` |
| GitHub 저장소/커밋 이력 | 완료 | 현재 15개 커밋 |
| 바닐라 프론트 구현 | 완료 | HTML/CSS/Vanilla JS |
| 반응형 적용 | 완료 | CSS media query 적용 |
| 사용자 입력 UI | 완료 | `recommend.html` 추천 폼 |
| AI 결과 화면 표시 | 완료 | 추천 카드 렌더링 |
| 실패 처리 안내 | 완료 | 필수값 누락, API 오류, 서버 연결 실패, 추천 후보 없음 |
| Python API 엔드포인트 | 완료 | `api/opportunities.py` |
| AI API 호출 | 완료 | OpenAI API 호출 후 추천 반환 |
| requirements.txt | 완료 | FastAPI, Uvicorn, Supabase, OpenAI 포함 |
| fetch('/api/...') 호출 | 완료 | 배포 환경에서 `/api/opportunities`, `/api/recommend` 호출 |
| GitHub + Vercel 배포 | 완료 | Vercel 배포 URL 확인 필요 |
| 환경 변수 관리 | 완료 | `.env.example`, Vercel 환경 변수 사용 |

## 제출 전 캡처해야 할 증빙

- 데스크톱 화면: 홈, 기회 탐색, AI 추천 결과
- 모바일 화면: 홈 또는 기회 탐색, AI 추천 화면
- AI 기능 동작 장면: 추천 폼 입력 전/후 결과
- AI 코딩 도구 사용 과정: Codex 대화 화면 또는 주요 수정 과정 스크린샷
- Vercel 배포 화면: Production 배포 성공 상태
- GitHub 저장소 화면: 파일 구조 또는 commit history

## 제출용 요약문

AI PATH KOREA는 국내 AI 교육, 공모전, 해커톤, 밋업, 콘퍼런스와 같은 성장 기회를 한곳에서 탐색하고, 사용자의 수준과 관심 분야에 맞는 기회를 AI가 추천하는 웹 서비스입니다. 프론트엔드는 HTML/CSS/Vanilla JavaScript로 구현했고, 백엔드는 Vercel Serverless Functions에서 Python FastAPI로 구성했습니다. 데이터는 Supabase PostgreSQL에 저장하며, OpenAI API는 DB에 존재하는 기회만 근거로 최대 3개의 추천 결과와 이유를 생성합니다.

## 테스트 입력 예시

AI 추천 페이지에서 다음 값으로 테스트합니다.

```text
수준: 입문
관심 분야: 생성형 AI
참여 목적: 새로운 기술 학습
선호 참여 방식: 온라인
선호 참가비: 무료
참여 가능한 기간: 8월 이후
```

기대 결과:

- 추천 결과 카드가 최대 3개 표시됩니다.
- 각 카드에 기회 제목, 요약, 추천 이유, 상세 정보 링크가 표시됩니다.
- 조건에 맞는 후보가 없거나 API 오류가 발생하면 사용자 안내 메시지가 표시됩니다.
