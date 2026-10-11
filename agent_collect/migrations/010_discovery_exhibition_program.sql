-- PHASE 12A draft. Requires 001-009. Do not apply in this phase.
-- Extends Agent Discovery category checks only. Does not enable schedules,
-- change Source approval, or alter Service tables/data.
begin;

alter table public.agent_discovery_schedules
  drop constraint agent_discovery_schedules_vertical_check;
alter table public.agent_discovery_schedules
  add constraint agent_discovery_schedules_vertical_check
  check (vertical in ('conference','exhibition','competition','hackathon',
                     'education','research','grant','program','job'));

alter table public.agent_discovery_queries
  drop constraint agent_discovery_queries_vertical_hint_check;
alter table public.agent_discovery_queries
  add constraint agent_discovery_queries_vertical_hint_check
  check (vertical_hint in ('conference','exhibition','competition','hackathon',
                          'education','meetup','grant','research','program','job','unknown'));

commit;
