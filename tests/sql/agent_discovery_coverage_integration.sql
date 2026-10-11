-- Run only after Agent migrations 001-009 on an isolated NONPRODUCTION database.
-- Never run in Production. Every fixture row is rolled back.
begin;

do $$
declare
  v_schedule public.agent_discovery_schedules%rowtype;
  v_claim public.agent_discovery_schedules%rowtype;
  v_query uuid := gen_random_uuid();
  v_claim_id uuid := gen_random_uuid();
  v_outcome text;
begin
  if not (select relrowsecurity from pg_class where oid='public.agent_discovery_schedules'::regclass) then
    raise exception 'Discovery schedule RLS disabled';
  end if;
  if has_table_privilege('anon','public.agent_discovery_schedules','INSERT') or
     has_table_privilege('anon','public.agent_discovery_schedules','SELECT') or
     has_function_privilege('anon',
       'public.agent_register_discovery_schedule_vertical(text,text,text,integer,text,text,text)',
       'EXECUTE') then
    raise exception 'anonymous Discovery privileges are too broad';
  end if;
  if not has_table_privilege('service_role','public.agent_discovery_schedules','SELECT') or
     has_table_privilege('service_role','public.agent_discovery_schedules','UPDATE') or
     not has_function_privilege('service_role',
       'public.agent_register_discovery_schedule_vertical(text,text,text,integer,text,text,text)',
       'EXECUTE') then
    raise exception 'Agent Discovery privileges differ from contract';
  end if;
  if not exists (select 1 from pg_trigger where tgrelid='public.agent_discovery_queries'::regclass
                 and tgname='agent_discovery_query_guard_trigger' and not tgisinternal) then
    raise exception 'scheduled query guard trigger missing';
  end if;

  v_schedule := public.agent_register_discovery_schedule_vertical(
    'phase11b isolated AI education test', 'en', 'KR', 1440,
    'NONPROD_TEST', 'isolated rollback fixture', 'education');
  if v_schedule.enabled or v_schedule.vertical <> 'education' then
    raise exception 'new schedule must be reviewed and disabled';
  end if;
  begin
    perform public.agent_register_discovery_schedule_vertical(
      'phase11b isolated AI education test', 'en', 'KR', 1440,
      'NONPROD_TEST', 'duplicate fixture', 'education');
    raise exception 'duplicate schedule registration unexpectedly succeeded';
  exception when unique_violation then null;
  end;
  if not public.agent_set_discovery_schedule_enabled(
    v_schedule.id, true, 'NONPROD_TEST', 'isolated query guard validation') then
    raise exception 'fixture enablement failed';
  end if;
  update public.agent_discovery_schedules set next_run_at=clock_timestamp()-interval '1 minute'
    where id=v_schedule.id;
  select * into v_claim from public.agent_claim_discovery(
    v_schedule.id, 'NONPROD_TEST', v_claim_id, 120);
  if v_claim.claim_id is distinct from v_claim_id then
    raise exception 'Discovery claim failed';
  end if;
  begin
    insert into public.agent_discovery_queries(
      id, query_text, provider, vertical_hint, status, started_at,
      schedule_id, schedule_window_id, schedule_claim_id, schedule_worker_id)
    values (v_query, v_schedule.query_text, 'tavily', 'conference', 'RUNNING',
      clock_timestamp(), v_schedule.id, v_claim.current_window_id, v_claim_id, 'NONPROD_TEST');
    raise exception 'mismatched schedule vertical was accepted';
  exception when raise_exception then
    if sqlerrm <> 'scheduled Discovery query lacks a live matching claim' then
      raise;
    end if;
  end;
  insert into public.agent_discovery_queries(
    id, query_text, provider, vertical_hint, status, started_at,
    schedule_id, schedule_window_id, schedule_claim_id, schedule_worker_id)
  values (v_query, v_schedule.query_text, 'tavily', 'education', 'RUNNING',
    clock_timestamp(), v_schedule.id, v_claim.current_window_id, v_claim_id, 'NONPROD_TEST');
  update public.agent_discovery_queries set status='SUCCEEDED', completed_at=clock_timestamp()
    where id=v_query;
  v_outcome := public.agent_finish_discovery(
    v_schedule.id, v_claim_id, v_claim.current_window_id, 'SUCCEEDED', v_query,
    array[60,360,1440], 1440);
  if v_outcome <> 'SUCCEEDED' or not exists (
      select 1 from public.agent_discovery_schedules
      where id=v_schedule.id and last_outcome='SUCCEEDED' and claim_id is null) then
    raise exception 'Discovery completion did not persist expected terminal state';
  end if;
end;
$$;

rollback;
