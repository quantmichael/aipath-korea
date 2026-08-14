-- AI PATH KOREA - first verified opportunity
-- Source checked on 2026-08-14 (Asia/Seoul).

begin;

insert into public.sources (
  name,
  homepage_url,
  collection_method,
  license_name,
  commercial_use_allowed,
  modification_allowed,
  attribution_required,
  attribution_text,
  terms_url,
  notes,
  last_terms_checked_at
)
values (
  '한국코드페어 공식 홈페이지',
  'https://kcf.or.kr',
  'manual',
  '공식 사이트 이용조건 확인 필요',
  null,
  null,
  true,
  '출처: 한국코드페어 공식 홈페이지',
  'https://kcf.or.kr',
  'AI PATH KOREA 관리자가 공식 페이지를 직접 확인해 핵심 사실과 자체 요약을 등록합니다.',
  timestamptz '2026-08-14 00:00:00+09'
)
on conflict (name) do update set
  homepage_url = excluded.homepage_url,
  collection_method = excluded.collection_method,
  license_name = excluded.license_name,
  commercial_use_allowed = excluded.commercial_use_allowed,
  modification_allowed = excluded.modification_allowed,
  attribution_required = excluded.attribution_required,
  attribution_text = excluded.attribution_text,
  terms_url = excluded.terms_url,
  notes = excluded.notes,
  last_terms_checked_at = excluded.last_terms_checked_at;

insert into public.opportunities (
  source_id,
  category_id,
  title,
  slug,
  summary,
  description,
  organizer,
  target_audience,
  difficulty,
  format,
  region,
  venue,
  price_type,
  price_text,
  application_start_at,
  application_deadline_at,
  event_start_at,
  event_end_at,
  official_url,
  image_url,
  status,
  is_featured,
  published_at,
  last_verified_at
)
select
  s.id,
  c.id,
  '2026 제8회 한국코드페어 해커톤',
  'korea-code-fair-hackathon-2026',
  'AI와 데이터를 활용해 사회 문제의 해결 방안을 기획하고 구현하는 청소년 대상 해커톤입니다.',
  '공개된 문제에 대해 개인 또는 팀이 해결 방안을 만들고 소프트웨어 작품을 기획·구현합니다. 세부 제출자료와 일정 변경 여부는 공식 페이지에서 최종 확인해야 합니다.',
  '과학기술정보통신부 · 한국지능정보사회진흥원',
  '대한민국 국적의 중·고등학생 개인 또는 3인 이하 팀',
  'all',
  null,
  null,
  null,
  'unknown',
  '공식 페이지 확인 필요',
  timestamptz '2026-07-13 10:00:00+09',
  timestamptz '2026-08-18 23:59:00+09',
  null,
  null,
  'https://kcf.or.kr/79',
  null,
  'open',
  true,
  now(),
  timestamptz '2026-08-14 00:00:00+09'
from public.sources s
cross join public.categories c
where s.name = '한국코드페어 공식 홈페이지'
  and c.slug = 'hackathon'
on conflict (slug) do update set
  source_id = excluded.source_id,
  category_id = excluded.category_id,
  title = excluded.title,
  summary = excluded.summary,
  description = excluded.description,
  organizer = excluded.organizer,
  target_audience = excluded.target_audience,
  difficulty = excluded.difficulty,
  format = excluded.format,
  region = excluded.region,
  venue = excluded.venue,
  price_type = excluded.price_type,
  price_text = excluded.price_text,
  application_start_at = excluded.application_start_at,
  application_deadline_at = excluded.application_deadline_at,
  event_start_at = excluded.event_start_at,
  event_end_at = excluded.event_end_at,
  official_url = excluded.official_url,
  status = excluded.status,
  is_featured = excluded.is_featured,
  published_at = excluded.published_at,
  last_verified_at = excluded.last_verified_at;

-- Replace the tag set so a rerun also removes stale classifications.
delete from public.opportunity_tags
where opportunity_id = (
  select id
  from public.opportunities
  where slug = 'korea-code-fair-hackathon-2026'
);

insert into public.opportunity_tags (opportunity_id, tag_id)
select o.id, t.id
from public.opportunities o
join public.tags t on t.slug in (
  'machine-learning',
  'data-analysis',
  'ai-service-development'
)
where o.slug = 'korea-code-fair-hackathon-2026'
on conflict (opportunity_id, tag_id) do nothing;

-- Remove the earlier internal placeholder after the opportunity points to its real source.
delete from public.sources
where name = 'AI PATH KOREA 관리자 큐레이션'
  and not exists (
    select 1
    from public.opportunities
    where source_id = public.sources.id
  );

commit;

select
  o.title,
  c.name as category,
  s.name as source,
  s.collection_method,
  o.status,
  o.application_start_at at time zone 'Asia/Seoul' as application_start_kst,
  o.application_deadline_at at time zone 'Asia/Seoul' as application_deadline_kst,
  o.event_start_at,
  o.official_url,
  count(t.id) as tag_count,
  string_agg(t.name, ', ' order by t.name) as tags
from public.opportunities o
join public.categories c on c.id = o.category_id
join public.sources s on s.id = o.source_id
left join public.opportunity_tags ot on ot.opportunity_id = o.id
left join public.tags t on t.id = ot.tag_id
where o.slug = 'korea-code-fair-hackathon-2026'
group by o.id, c.id, s.id;
