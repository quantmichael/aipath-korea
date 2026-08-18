-- AI PATH KOREA - initial reference data
-- Safe to run more than once because unique values are upserted.

begin;

insert into public.categories (name, slug, description, display_order)
values
  ('교육', 'education', 'AI 강의, 부트캠프, 실습 과정과 워크숍', 10),
  ('공모전', 'competition', 'AI·데이터·산업 융합 아이디어 및 결과물 공모', 20),
  ('해커톤', 'hackathon', '제한된 기간 동안 팀 또는 개인이 결과물을 만드는 행사', 30),
  ('밋업', 'meetup', 'AI 관심자와 실무자가 교류하는 소규모 모임', 40),
  ('콘퍼런스', 'conference', 'AI 기술·산업의 발표와 네트워킹이 함께하는 행사', 50),
  ('웨비나', 'webinar', '온라인으로 진행되는 AI 세미나와 설명회', 60),
  ('성장 프로그램', 'growth-program', '멘토링, 취업 연계, 포트폴리오 및 커리어 프로그램', 70)
on conflict (slug) do update set
  name = excluded.name,
  description = excluded.description,
  display_order = excluded.display_order,
  is_active = true;

insert into public.tags (name, slug)
values
  ('생성형 AI', 'generative-ai'),
  ('LLM', 'llm'),
  ('AI 에이전트', 'ai-agent'),
  ('머신러닝', 'machine-learning'),
  ('딥러닝', 'deep-learning'),
  ('데이터 분석', 'data-analysis'),
  ('컴퓨터 비전', 'computer-vision'),
  ('자연어 처리', 'nlp'),
  ('Python', 'python'),
  ('AI 서비스 개발', 'ai-service-development'),
  ('AI 콘텐츠', 'ai-content'),
  ('AI 디자인', 'ai-design'),
  ('AI 비즈니스', 'ai-business'),
  ('AI 창업', 'ai-startup'),
  ('취업·커리어', 'career')
on conflict (slug) do update set
  name = excluded.name;

insert into public.sources (
  name,
  homepage_url,
  collection_method,
  attribution_required,
  notes,
  last_terms_checked_at
)
values
  (
    'AI Hub',
    'https://www.aihub.or.kr/',
    'manual',
    true,
    '우선 수집 후보. AI 교육정보, 공지사항, 사업공고를 확인한다. AI Hub는 오픈 API도 제공하지만 기회성 공고는 원문 페이지 기준으로 검수한다.',
    '2026-08-19 00:00:00+09'
  ),
  (
    '정보통신산업진흥원(NIPA)',
    'https://www.nipa.kr/home/index',
    'manual',
    true,
    '우선 수집 후보. AI, SW, 디지털 인재양성, 사업공고, 공지사항을 확인한다.',
    '2026-08-19 00:00:00+09'
  ),
  (
    '서울 AI 허브',
    'https://www.seoulaihub.kr/',
    'manual',
    true,
    '우선 수집 후보. AI 교육, 프로그램, 오픈이노베이션, 입주/성장 지원, 네트워킹 공고를 확인한다.',
    '2026-08-19 00:00:00+09'
  ),
  (
    'K-Startup 창업지원포털',
    'https://www.k-startup.go.kr/web/main/index.do',
    'manual',
    true,
    '우선 수집 후보. AI 스타트업, 창업지원, 사업화, 공모/모집성 공고를 확인한다.',
    '2026-08-19 00:00:00+09'
  ),
  (
    '고용24 K-디지털 훈련',
    'https://www.work24.go.kr/',
    'manual',
    true,
    '우선 수집 후보. K-Digital Training, 생성형 AI, 데이터 분석, AI 개발자 교육 과정을 확인한다.',
    '2026-08-19 00:00:00+09'
  ),
  (
    'DACON',
    'https://dacon.io/',
    'manual',
    true,
    '우선 수집 후보. AI 경진대회, 해커톤, 교육 콘텐츠를 확인한다. 공식 대회 상세 페이지 기준으로 등록한다.',
    '2026-08-19 00:00:00+09'
  ),
  (
    '온오프믹스',
    'https://onoffmix.com/',
    'manual',
    true,
    '보조 수집 후보. 과학/IT/AI 주제의 교육, 세미나, 컨퍼런스, 네트워킹 행사를 탐색하고 주최자 원문을 우선 확인한다.',
    '2026-08-19 00:00:00+09'
  ),
  (
    '콘테스트코리아',
    'https://www.contestkorea.com/',
    'manual',
    true,
    '보조 수집 후보. AI, 데이터, 해커톤, 공모전 정보를 탐색하되 등록 전 주최사 공고를 반드시 확인한다.',
    '2026-08-19 00:00:00+09'
  ),
  (
    '씽굿',
    'https://www.thinkcontest.com/thinkgood/user/contest/index.do',
    'manual',
    true,
    '보조 수집 후보. 공모전, 대외활동, 교육/강연 정보를 탐색하되 등록 전 주최사 공고를 반드시 확인한다.',
    '2026-08-19 00:00:00+09'
  ),
  (
    'K-ICT 창업멘토링센터',
    'https://gomentoring.or.kr/',
    'manual',
    true,
    '보조 수집 후보. K-Global 창업멘토링, 오픈멘토링, 스타트업 지원소식을 확인한다.',
    '2026-08-19 00:00:00+09'
  ),
  (
    '기업마당',
    'https://www.bizinfo.go.kr/',
    'manual',
    true,
    '지역 공공 수집 후보. 중앙부처, 지자체, 유관기관의 기업지원, 교육, 행사 공고를 탐색하고 AI, 데이터, DX, AX 키워드로 필터링한다.',
    '2026-08-19 00:00:00+09'
  ),
  (
    '인천테크노파크',
    'https://itp.or.kr/',
    'manual',
    true,
    '지역 공공 수집 후보. AI혁신센터, AI 전문인력 양성, 지역 기업지원, 디지털 전환 관련 공고를 확인한다.',
    '2026-08-19 00:00:00+09'
  ),
  (
    '경기창조경제혁신센터',
    'https://ccei.creativekorea.or.kr/gyeonggi/',
    'manual',
    true,
    '지역 공공 수집 후보. AI 창업 교육, 오픈이노베이션, 멘토링, 데모데이, 스타트업 지원 프로그램을 확인한다.',
    '2026-08-19 00:00:00+09'
  ),
  (
    '전남테크노파크',
    'https://jntp.or.kr/',
    'manual',
    true,
    '지역 공공 수집 후보. 지역산업 AI 지원, AI 산학연 프로젝트, 기업지원 및 교육 공고를 확인한다.',
    '2026-08-19 00:00:00+09'
  ),
  (
    '경남테크노파크',
    'https://www.gntp.or.kr/',
    'manual',
    true,
    '지역 공공 수집 후보. AI 이노베이션 아카데미, 제조 AI 전환, 지역 기업지원 및 교육 공고를 확인한다.',
    '2026-08-19 00:00:00+09'
  ),
  (
    '인천광역시교육청',
    'https://www.ice.go.kr/',
    'manual',
    true,
    '지역 공공 수집 후보. 학생 AI교육 캠프, AI활용 해커톤, 교원 연수 등 교육청 주관 AI 프로그램을 확인한다.',
    '2026-08-19 00:00:00+09'
  ),
  (
    '한국인공지능학회',
    'https://aiassociation.kr/',
    'manual',
    true,
    '보조 수집 후보. 인공지능 학술행사, 단기강좌, 컨퍼런스 정보를 확인한다.',
    '2026-08-19 00:00:00+09'
  )
on conflict (name) do update set
  homepage_url = excluded.homepage_url,
  collection_method = excluded.collection_method,
  attribution_required = excluded.attribution_required,
  notes = excluded.notes,
  last_terms_checked_at = excluded.last_terms_checked_at;

commit;

select 'categories' as table_name, count(*) as row_count from public.categories
union all
select 'sources', count(*) from public.sources
union all
select 'tags', count(*) from public.tags
order by table_name;
