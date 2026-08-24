-- AI PATH KOREA - reset opportunity-related data only
-- Keeps sources, categories, and tags.

begin;

delete from public.opportunity_tags;
delete from public.opportunities;

do $$
begin
  if to_regclass('public.opportunity_candidates') is not null then
    delete from public.opportunity_candidates;
  end if;

  if to_regclass('public.collection_runs') is not null then
    delete from public.collection_runs;
  end if;
end;
$$;

commit;

select 'sources' as table_name, count(*) as row_count from public.sources
union all
select 'categories', count(*) from public.categories
union all
select 'tags', count(*) from public.tags
union all
select 'opportunities', count(*) from public.opportunities
union all
select 'opportunity_tags', count(*) from public.opportunity_tags
union all
select 'opportunity_candidates', count(*) from public.opportunity_candidates
union all
select 'collection_runs', count(*) from public.collection_runs
order by table_name;
