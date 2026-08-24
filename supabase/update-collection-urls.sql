-- AI PATH KOREA - collection URL verification update
-- Run this in Supabase SQL Editor after source rows already exist.

begin;

update public.sources
set
  collection_url = homepage_url,
  is_active = false,
  collection_method = 'manual'
where homepage_url is not null;

update public.sources
set
  collection_url = case name
    when 'AI Hub' then 'https://www.aihub.or.kr/aihubnews/bsnspblanc/list.do'
    when '정보통신산업진흥원(NIPA)' then 'https://www.nipa.kr/home/2-2'
    when 'DACON' then 'https://dacon.io/competitions'
    when '기업마당' then 'https://www.bizinfo.go.kr/web/lay1/bbs/S1T122C128/AS/74/list.do'
    else collection_url
  end,
  source_type = 'regular',
  collection_method = 'html',
  is_active = true
where name in (
  'AI Hub',
  '정보통신산업진흥원(NIPA)',
  'DACON',
  '기업마당'
);

update public.sources
set
  collection_url = 'https://www.seoulaihub.kr/index.asp',
  source_type = 'irregular',
  collection_method = 'manual',
  is_active = false
where name = '서울 AI 허브';

update public.sources
set
  collection_url = 'https://www.k-startup.go.kr/web/contents/bizpbanc-ongoing.do',
  source_type = 'irregular',
  collection_method = 'manual',
  is_active = false
where name = 'K-Startup 창업지원포털';

update public.sources
set
  source_type = 'discovery',
  collection_method = 'manual',
  is_active = false
where name in (
  '온오프믹스',
  '콘테스트코리아',
  '씽굿'
);

update public.sources
set
  source_type = 'irregular',
  collection_method = 'manual',
  is_active = false
where name in (
  '고용24 K-디지털 훈련',
  'K-ICT 창업멘토링센터',
  '인천테크노파크',
  '경기창조경제혁신센터',
  '전남테크노파크',
  '경남테크노파크',
  '인천광역시교육청',
  '한국인공지능학회'
);

commit;

select
  name,
  source_type,
  collection_method,
  is_active,
  collection_url
from public.sources
order by is_active desc, name;
