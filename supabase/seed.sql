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

commit;

select 'categories' as table_name, count(*) as row_count from public.categories
union all
select 'tags', count(*) from public.tags
order by table_name;
