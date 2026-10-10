-- PHASE 7.3C: Agent-only, one-shot collection scheduling foundation.
-- CREATED ONLY. Do not apply during security hardening. Requires applied 001-004.
-- No Service table or existing Agent Source row is changed.
begin;

-- A window is identified by Source + its scheduled UTC instant (microseconds
-- since epoch). Retry after backoff has a new due instant, not a random ID.
-- md5 is an identifier encoding here, not an authentication primitive.
create function public.agent_schedule_window_id(
  p_source_id uuid, p_next_collection_at timestamptz
) returns uuid
language sql strict set search_path = pg_catalog as $$
  select md5(p_source_id::text || ':' ||
    ((extract(epoch from p_next_collection_at) * 1000000)::bigint)::text)::uuid
$$;
revoke all on function public.agent_schedule_window_id(uuid,timestamptz)
  from public, anon, authenticated, service_role;

create table public.agent_source_schedules (
  source_id uuid primary key references public.agent_sources(id),
  window_id uuid not null unique,
  schedule_policy text not null default 'FIXED' check (schedule_policy = 'FIXED'),
  interval_minutes integer not null check (interval_minutes > 0),
  next_collection_at timestamptz not null,
  last_collection_at timestamptz,
  last_success_at timestamptz,
  last_failure_at timestamptz,
  consecutive_failures integer not null default 0 check (consecutive_failures >= 0),
  review_needed boolean not null default false,
  claim_id uuid unique,
  claimed_by text,
  claimed_at timestamptz,
  claim_expires_at timestamptz,
  updated_at timestamptz not null default now(),
  constraint agent_schedule_claim_consistent check (
    (claim_id is null and claimed_by is null and claimed_at is null and claim_expires_at is null)
    or (claim_id is not null and claimed_by is not null and btrim(claimed_by) <> '' and
        claimed_at is not null and claim_expires_at is not null and
        claim_expires_at > claimed_at)
  )
);
create index agent_schedules_due_idx
  on public.agent_source_schedules(next_collection_at, source_id)
  where review_needed = false;
create index agent_schedules_lease_idx
  on public.agent_source_schedules(claim_expires_at)
  where claim_id is not null;

-- One terminal outcome per claim/window, including failures that produced no
-- CollectionRun. This makes duplicate completion calls no-ops after rotation.
create table public.agent_collection_completions (
  source_id uuid not null references public.agent_sources(id),
  window_id uuid not null,
  claim_id uuid not null unique,
  run_id uuid references public.agent_collection_runs(id),
  outcome text not null check (outcome in ('SUCCEEDED', 'FAILED')),
  completed_at timestamptz not null,
  next_collection_at timestamptz not null,
  primary key (source_id, window_id)
);
create index agent_completions_source_time_idx
  on public.agent_collection_completions(source_id, completed_at desc);
alter table public.agent_collection_completions enable row level security;
revoke all on public.agent_collection_completions from public, anon, authenticated, service_role;
grant select on public.agent_collection_completions to service_role;

-- The partial unique index is insufficient when the indexed JSON value is NULL.
-- Require a nonempty UUID for every scheduled Run before creating the index.
alter table public.agent_collection_runs
  add constraint agent_runs_scheduled_window_required check (
    source_config_snapshot ->> 'trigger' is distinct from 'scheduled'
    or (
      coalesce((source_config_snapshot ->> 'schedule_window_id') ~
        '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', false)
      and coalesce((source_config_snapshot ->> 'claim_id') ~
        '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', false)
      and nullif(btrim(source_config_snapshot ->> 'worker_id'), '') is not null
    )
  );
alter table public.agent_collection_runs
  add constraint agent_runs_scheduled_success_finished check (
    source_config_snapshot ->> 'trigger' is distinct from 'scheduled'
    or status <> 'SUCCEEDED' or finished_at is not null
  );
-- Exactly one CollectionRun row per Source/scheduling window. A crashed RUNNING
-- row is marked FAILED; a policy backoff creates a new deterministic due window.
create unique index agent_runs_scheduled_window_unique
  on public.agent_collection_runs(source_id, (source_config_snapshot ->> 'schedule_window_id'))
  where source_config_snapshot ->> 'trigger' = 'scheduled';

alter table public.agent_source_schedules enable row level security;
revoke all on public.agent_source_schedules from public, anon, authenticated, service_role;
grant select on public.agent_source_schedules to service_role;

-- A scheduled Run must be inserted under the currently leased Source/window.
-- This prevents a direct INSERT with a fresh arbitrary UUID from evading the
-- partial unique index. The trigger runs as its postgres owner so its row lock
-- does not require granting schedule UPDATE to the application service_role.
-- Manual Runs remain unchanged.
create function public.agent_scheduled_run_claim_guard() returns trigger
language plpgsql security definer set search_path = pg_catalog as $$
declare
  v_source public.agent_sources%rowtype;
  v_schedule public.agent_source_schedules%rowtype;
begin
  if new.source_config_snapshot ->> 'trigger' = 'scheduled' then
    -- Lock the reviewed identity before the schedule, matching claim RPC's
    -- lock order. A disabled Source cannot insert a scheduled Run.
    select * into v_source from public.agent_sources
      where id = new.source_id for share;
    if not found or not public.agent_schedulable_source(v_source) then
      raise exception 'scheduled Run requires an approved ACTIVE Source';
    end if;
    -- Serialize Run creation against completion's FOR UPDATE on this row.
    select * into v_schedule from public.agent_source_schedules
      where source_id = new.source_id for share;
    if not found or v_schedule.claim_id is null or
       v_schedule.claimed_by is null or v_schedule.claim_expires_at is null or
       v_schedule.window_id::text is distinct from (new.source_config_snapshot ->> 'schedule_window_id') or
       v_schedule.claim_id::text is distinct from (new.source_config_snapshot ->> 'claim_id') or
       v_schedule.claimed_by is distinct from (new.source_config_snapshot ->> 'worker_id') or
       v_schedule.claim_expires_at <= clock_timestamp() then
      raise exception 'scheduled Run requires a matching live Source claim';
    end if;
  end if;
  return new;
end;
$$;
alter function public.agent_scheduled_run_claim_guard() owner to postgres;
create trigger agent_scheduled_run_claim_before_insert
  before insert on public.agent_collection_runs
  for each row execute function public.agent_scheduled_run_claim_guard();
revoke all on function public.agent_scheduled_run_claim_guard()
  from public, anon, authenticated, service_role;

-- A committed terminal scheduled Run cannot be downgraded by a late
-- application exception or moved to another claim/window.
create function public.agent_scheduled_run_terminal_guard() returns trigger
language plpgsql security definer set search_path = pg_catalog as $$
declare
  v_source public.agent_sources%rowtype;
  v_schedule public.agent_source_schedules%rowtype;
begin
  if old.source_config_snapshot ->> 'trigger' = 'scheduled' then
    if new.source_id is distinct from old.source_id or
       new.source_config_snapshot is distinct from old.source_config_snapshot or
       (old.status in ('SUCCEEDED', 'FAILED') and
         (new.status is distinct from old.status or
          new.finished_at is distinct from old.finished_at)) then
      raise exception 'terminal scheduled Run and claim/window identity are immutable';
    end if;
    if new.status = 'SUCCEEDED' and old.status <> 'SUCCEEDED' then
      select * into v_source from public.agent_sources
        where id = new.source_id for share;
      if not found or not public.agent_schedulable_source(v_source) then
        raise exception 'scheduled success requires an approved ACTIVE Source';
      end if;
      -- A success commit cannot race past a failure completion for its claim.
      select * into v_schedule from public.agent_source_schedules
        where source_id = new.source_id for share nowait;
      if not found or v_schedule.claim_id is null or
         v_schedule.claimed_by is null or v_schedule.claim_expires_at is null or
         v_schedule.window_id::text is distinct from (new.source_config_snapshot ->> 'schedule_window_id') or
         v_schedule.claim_id::text is distinct from (new.source_config_snapshot ->> 'claim_id') or
         v_schedule.claimed_by is distinct from (new.source_config_snapshot ->> 'worker_id') or
         v_schedule.claim_expires_at <= clock_timestamp() then
        raise exception 'scheduled success requires a matching live Source claim';
      end if;
    end if;
  end if;
  return new;
end;
$$;
alter function public.agent_scheduled_run_terminal_guard() owner to postgres;
create trigger agent_scheduled_run_terminal_before_update
  before update on public.agent_collection_runs
  for each row execute function public.agent_scheduled_run_terminal_guard();
revoke all on function public.agent_scheduled_run_terminal_guard()
  from public, anon, authenticated, service_role;

-- The 004 review RPC remains the sole lifecycle transition. This trigger only
-- creates/resets Agent scheduling state AFTER an approved ACTIVE transition.
create function public.agent_schedule_on_activation() returns trigger
language plpgsql security definer set search_path = pg_catalog as $$
declare
  v_now timestamptz := clock_timestamp();
begin
  if new.status = 'ACTIVE' and new.is_active and old.status is distinct from 'ACTIVE' then
    insert into public.agent_source_schedules (
      source_id, window_id, interval_minutes, next_collection_at
    ) values (
      new.id, public.agent_schedule_window_id(new.id, v_now),
      coalesce(new.collection_interval_minutes, 720), v_now
    ) on conflict (source_id) do update set
      window_id = excluded.window_id,
      interval_minutes = excluded.interval_minutes,
      next_collection_at = excluded.next_collection_at,
      consecutive_failures = 0, review_needed = false,
      claim_id = null, claimed_by = null, claimed_at = null, claim_expires_at = null,
      updated_at = v_now;
  end if;
  return new;
end;
$$;
create trigger agent_schedule_after_activation
  after update of status, is_active on public.agent_sources
  for each row execute function public.agent_schedule_on_activation();
revoke all on function public.agent_schedule_on_activation() from public, anon, authenticated, service_role;

-- An interval is selected before approval; it cannot drift while approved.
create function public.agent_schedule_interval_guard() returns trigger
language plpgsql set search_path = pg_catalog as $$
begin
  if old.status in ('APPROVED', 'ACTIVE') and
     old.collection_interval_minutes is distinct from new.collection_interval_minutes then
    raise exception 'return Source to human review before changing collection interval';
  end if;
  return new;
end;
$$;
create trigger agent_schedule_interval_before_update
  before update of collection_interval_minutes on public.agent_sources
  for each row execute function public.agent_schedule_interval_guard();
revoke all on function public.agent_schedule_interval_guard() from public, anon, authenticated, service_role;

-- 004's ACTIVE CHECK is NOT VALID for old rows; repeat the approved snapshot
-- check so a legacy ACTIVE row cannot enter the new automatic worker.
create function public.agent_schedulable_source(p_source public.agent_sources) returns boolean
language plpgsql security invoker set search_path = pg_catalog as $$
begin
  return coalesce((
    p_source.status = 'ACTIVE' and p_source.is_active and p_source.collection_method is not null
    and p_source.provenance #>> '{source_review,last_decision}' = 'ACTIVATED'
    and p_source.provenance #>> '{source_review,last_decision_id}' is not null
    and p_source.provenance #>> '{source_review,approval,decision}' = 'APPROVED'
    and p_source.provenance #>> '{source_review,approval,canonical_url}' = p_source.canonical_url
    and p_source.provenance #>> '{source_review,approval,domain}' =
      regexp_replace(lower(substring(p_source.canonical_url from '^https?://([^/:?#]+)')), '^www\.', '')
    and p_source.provenance #>> '{source_review,approval,source_type}' = p_source.source_type
    and p_source.provenance #>> '{source_review,approval,confidence}' is not distinct from p_source.confidence::text
    and p_source.provenance #>> '{source_review,approval,collection_method}' = p_source.collection_method
    and p_source.provenance #> '{source_review,approval,collection_config}' = p_source.collection_config
    and p_source.provenance #> '{source_review,approval,collection_allowed}' = 'true'::jsonb
    and p_source.provenance #>> '{source_review,approval,reviewer}' is not null
    and p_source.provenance #>> '{source_review,approval,reason}' is not null
    and p_source.provenance #>> '{source_review,approval,reviewed_at}' is not null
    and p_source.provenance #>> '{source_review,approval,trust}' = case
      when p_source.source_type = 'official' and
           p_source.provenance #> '{source_review,approval,officiality_confirmed}' = 'true'::jsonb
        then 'OFFICIAL'
      when p_source.source_type = 'aggregator' and p_source.confidence >= 0.8 then 'TRUSTED'
      else null end
  ), false);
end;
$$;
revoke all on function public.agent_schedulable_source(public.agent_sources)
  from public, anon, authenticated, service_role;

create function public.agent_list_due_sources(p_limit integer)
returns table(source_id uuid, window_id uuid, interval_minutes integer, next_collection_at timestamptz)
language plpgsql security definer set search_path = pg_catalog as $$
declare
  v_now timestamptz := clock_timestamp();
begin
  if p_limit is null or p_limit < 1 or p_limit > 100 then
    raise exception 'invalid due source query';
  end if;
  return query
    select s.source_id, s.window_id, s.interval_minutes, s.next_collection_at
    from public.agent_source_schedules s
    join public.agent_sources src on src.id = s.source_id
    where public.agent_schedulable_source(src)
      and not s.review_needed and s.next_collection_at <= v_now
      and (s.claim_id is null or s.claim_expires_at <= v_now)
    order by s.next_collection_at, s.source_id limit p_limit;
end;
$$;
revoke all on function public.agent_list_due_sources(integer)
  from public, anon, authenticated, service_role;
grant execute on function public.agent_list_due_sources(integer) to service_role;

-- A transaction-scoped advisory lock serializes claims for one normalized
-- domain. A row lock serializes claims for one Source. No persistent lock table.
create function public.agent_claim_due_source(
  p_source_id uuid, p_worker_id text, p_claim_id uuid,
  p_lease_seconds integer, p_max_domain_claims integer,
  p_backoff_minutes integer[], p_failure_review_threshold integer
) returns setof public.agent_source_schedules
language plpgsql security definer set search_path = pg_catalog as $$
declare
  v_source public.agent_sources%rowtype;
  v_schedule public.agent_source_schedules%rowtype;
  v_run public.agent_collection_runs%rowtype;
  v_domain text;
  v_occupied integer;
  v_failures integer;
  v_delay integer;
  v_backoff integer;
  v_finished timestamptz;
  v_next timestamptz;
  v_now timestamptz;
begin
  if p_source_id is null or p_claim_id is null or
     p_worker_id is null or btrim(p_worker_id) = '' or
     p_lease_seconds is null or p_lease_seconds not between 1 and 86400 or
     p_max_domain_claims is null or p_max_domain_claims not between 1 and 16 or
     p_failure_review_threshold is null or p_failure_review_threshold < 1 or
     coalesce(array_length(p_backoff_minutes, 1), 0) < 1 then
    raise exception 'invalid collection claim policy';
  end if;
  foreach v_backoff in array p_backoff_minutes loop
    if v_backoff is null or v_backoff < 1 then
      raise exception 'invalid failure backoff';
    end if;
  end loop;
  -- Hold the reviewed Source identity stable through the claim transaction;
  -- concurrent DISABLED/HOLD transitions wait, then the next runner rechecks.
  select * into v_source from public.agent_sources where id = p_source_id for share;
  if not found or not public.agent_schedulable_source(v_source) then return; end if;
  v_domain := regexp_replace(lower(substring(v_source.canonical_url from '^https?://([^/:?#]+)')), '^www\.', '');
  if v_domain is null or not pg_try_advisory_xact_lock(hashtextextended(v_domain, 0)) then return; end if;
  select * into v_schedule from public.agent_source_schedules
    where source_id = p_source_id for update;
  -- Take one fresh wall-clock reading AFTER the row lock. Transaction-start
  -- now() could be stale if another claim held that lock for a long time.
  v_now := clock_timestamp();
  if not found or v_schedule.review_needed or v_schedule.next_collection_at > v_now then return; end if;
  if v_schedule.claim_id is not null then
    if v_schedule.claim_expires_at > v_now then return; end if;
    select * into v_run from public.agent_collection_runs
      where source_id = p_source_id
        and source_config_snapshot ->> 'trigger' = 'scheduled'
        and source_config_snapshot ->> 'claim_id' = v_schedule.claim_id::text
        and source_config_snapshot ->> 'schedule_window_id' = v_schedule.window_id::text
        and source_config_snapshot ->> 'worker_id' = v_schedule.claimed_by
        and status in ('SUCCEEDED', 'FAILED')
      order by finished_at desc limit 1;
    if v_run.status = 'SUCCEEDED' then
      v_finished := v_run.finished_at;
      v_next := v_finished + make_interval(mins => v_schedule.interval_minutes);
      insert into public.agent_collection_completions (
        source_id, window_id, claim_id, run_id, outcome, completed_at, next_collection_at
      ) values (
        p_source_id, v_schedule.window_id, v_schedule.claim_id, v_run.id,
        'SUCCEEDED', v_finished, v_next
      );
      update public.agent_source_schedules set
        last_collection_at = v_finished, last_success_at = v_finished,
        consecutive_failures = 0, review_needed = false,
        next_collection_at = v_next,
        window_id = public.agent_schedule_window_id(p_source_id, v_next),
        claim_id = null, claimed_by = null,
        claimed_at = null, claim_expires_at = null, updated_at = v_now
      where source_id = p_source_id;
    else
      if v_run.id is null then
        update public.agent_collection_runs set status = 'FAILED', finished_at = v_now,
          failed_count = greatest(failed_count, 1), error_summary = 'scheduled lease expired'
        where source_id = p_source_id
          and source_config_snapshot ->> 'trigger' = 'scheduled'
          and source_config_snapshot ->> 'claim_id' = v_schedule.claim_id::text
          and source_config_snapshot ->> 'schedule_window_id' = v_schedule.window_id::text
          and source_config_snapshot ->> 'worker_id' = v_schedule.claimed_by
          and status = 'RUNNING' returning * into v_run;
      end if;
      v_finished := v_now;
      v_failures := v_schedule.consecutive_failures + 1;
      v_delay := p_backoff_minutes[least(v_failures, array_length(p_backoff_minutes, 1))];
      v_next := v_finished + make_interval(mins => v_delay);
      insert into public.agent_collection_completions (
        source_id, window_id, claim_id, run_id, outcome, completed_at, next_collection_at
      ) values (
        p_source_id, v_schedule.window_id, v_schedule.claim_id, v_run.id,
        'FAILED', v_finished, v_next
      );
      update public.agent_source_schedules set
        last_collection_at = v_finished, last_failure_at = v_finished,
        consecutive_failures = v_failures,
        review_needed = (v_failures >= p_failure_review_threshold),
        next_collection_at = v_next,
        window_id = public.agent_schedule_window_id(p_source_id, v_next),
        claim_id = null, claimed_by = null,
        claimed_at = null, claim_expires_at = null, updated_at = v_now
      where source_id = p_source_id;
    end if;
    return;
  end if;
  select count(*) into v_occupied
  from public.agent_source_schedules other
  join public.agent_sources os on os.id = other.source_id
  where other.claim_id is not null and other.claim_expires_at > v_now
    and regexp_replace(lower(substring(os.canonical_url from '^https?://([^/:?#]+)')), '^www\.', '') = v_domain;
  if v_occupied >= p_max_domain_claims then return; end if;
  update public.agent_source_schedules set
    claim_id = p_claim_id, claimed_by = p_worker_id,
    claimed_at = v_now, claim_expires_at = v_now + make_interval(secs => p_lease_seconds),
    updated_at = v_now
  where source_id = p_source_id returning * into v_schedule;
  return next v_schedule;
end;
$$;
revoke all on function public.agent_claim_due_source(uuid,text,uuid,integer,integer,integer[],integer)
  from public, anon, authenticated, service_role;
grant execute on function public.agent_claim_due_source(uuid,text,uuid,integer,integer,integer[],integer)
  to service_role;

create function public.agent_finish_collection(
  p_source_id uuid, p_claim_id uuid, p_run_id uuid,
  p_backoff_minutes integer[], p_failure_review_threshold integer
) returns text
language plpgsql security definer set search_path = pg_catalog as $$
declare
  v_schedule public.agent_source_schedules%rowtype;
  v_prior public.agent_collection_completions%rowtype;
  v_explicit public.agent_collection_runs%rowtype;
  v_success public.agent_collection_runs%rowtype;
  v_record_run_id uuid;
  v_outcome text;
  v_failures integer;
  v_delay integer;
  v_backoff integer;
  v_finished timestamptz;
  v_next timestamptz;
begin
  if p_source_id is null or p_claim_id is null or
     p_failure_review_threshold is null or p_failure_review_threshold < 1 or
     coalesce(array_length(p_backoff_minutes, 1), 0) < 1 then
    raise exception 'invalid collection completion';
  end if;
  foreach v_backoff in array p_backoff_minutes loop
    if v_backoff is null or v_backoff < 1 then raise exception 'invalid failure backoff'; end if;
  end loop;
  select * into v_schedule from public.agent_source_schedules
    where source_id = p_source_id for update;
  if not found then raise exception 'schedule not found'; end if;
  -- A rotated window remains idempotent by its immutable completion record.
  select * into v_prior from public.agent_collection_completions
    where source_id = p_source_id and claim_id = p_claim_id;
  if found then
    if p_run_id is not null and p_run_id is distinct from v_prior.run_id then
      raise exception 'Run ID does not match completed claim';
    end if;
    return v_prior.outcome;
  end if;
  if v_schedule.claim_id is distinct from p_claim_id then
    raise exception 'stale collection claim';
  end if;
  -- Validate an explicit Run before any schedule mutation, even if another
  -- terminal result might be discoverable for this window.
  if p_run_id is not null then
    select * into v_explicit from public.agent_collection_runs
      where id = p_run_id and source_id = p_source_id
        and status in ('SUCCEEDED', 'FAILED')
        and source_config_snapshot ->> 'trigger' = 'scheduled'
        and source_config_snapshot ->> 'claim_id' = p_claim_id::text
        and source_config_snapshot ->> 'schedule_window_id' = v_schedule.window_id::text
        and source_config_snapshot ->> 'worker_id' = v_schedule.claimed_by;
    if not found then raise exception 'completed Run does not match claim'; end if;
  end if;
  -- SUCCESS is terminal. A NULL/FAILED completion cannot downgrade a Run
  -- already committed as SUCCEEDED for this exact Source/claim/window.
  select * into v_success from public.agent_collection_runs
    where source_id = p_source_id and status = 'SUCCEEDED'
      and source_config_snapshot ->> 'trigger' = 'scheduled'
      and source_config_snapshot ->> 'claim_id' = p_claim_id::text
      and source_config_snapshot ->> 'schedule_window_id' = v_schedule.window_id::text
      and source_config_snapshot ->> 'worker_id' = v_schedule.claimed_by
    limit 1;
  if v_success.id is not null then
    v_outcome := 'SUCCEEDED';
    v_record_run_id := v_success.id;
    v_finished := v_success.finished_at;
    v_next := v_finished + make_interval(mins => v_schedule.interval_minutes);
    insert into public.agent_collection_completions (
      source_id, window_id, claim_id, run_id, outcome, completed_at, next_collection_at
    ) values (
      p_source_id, v_schedule.window_id, p_claim_id, v_record_run_id,
      v_outcome, v_finished, v_next
    );
    update public.agent_source_schedules set
      last_collection_at = v_finished, last_success_at = v_finished,
      consecutive_failures = 0, review_needed = false,
      next_collection_at = v_next,
      window_id = public.agent_schedule_window_id(p_source_id, v_next),
      claim_id = null, claimed_by = null,
      claimed_at = null, claim_expires_at = null, updated_at = v_finished
    where source_id = p_source_id;
  else
    v_outcome := 'FAILED';
    v_finished := clock_timestamp();
    if p_run_id is not null then
      v_record_run_id := v_explicit.id;
    else
      update public.agent_collection_runs set status = 'FAILED', finished_at = v_finished,
        failed_count = greatest(failed_count, 1), error_summary = 'scheduled worker failed'
      where source_id = p_source_id and status = 'RUNNING'
        and source_config_snapshot ->> 'trigger' = 'scheduled'
        and source_config_snapshot ->> 'claim_id' = p_claim_id::text
        and source_config_snapshot ->> 'schedule_window_id' = v_schedule.window_id::text
        and source_config_snapshot ->> 'worker_id' = v_schedule.claimed_by
      returning id into v_record_run_id;
    end if;
    v_failures := v_schedule.consecutive_failures + 1;
    v_delay := p_backoff_minutes[least(v_failures, array_length(p_backoff_minutes, 1))];
    v_next := v_finished + make_interval(mins => v_delay);
    insert into public.agent_collection_completions (
      source_id, window_id, claim_id, run_id, outcome, completed_at, next_collection_at
    ) values (
      p_source_id, v_schedule.window_id, p_claim_id, v_record_run_id,
      v_outcome, v_finished, v_next
    );
    update public.agent_source_schedules set
      last_collection_at = v_finished, last_failure_at = v_finished,
      consecutive_failures = v_failures,
      review_needed = (v_failures >= p_failure_review_threshold),
      next_collection_at = v_next,
      window_id = public.agent_schedule_window_id(p_source_id, v_next),
      claim_id = null, claimed_by = null,
      claimed_at = null, claim_expires_at = null, updated_at = v_finished
    where source_id = p_source_id;
  end if;
  return v_outcome;
end;
$$;
revoke all on function public.agent_finish_collection(uuid,uuid,uuid,integer[],integer)
  from public, anon, authenticated, service_role;
grant execute on function public.agent_finish_collection(uuid,uuid,uuid,integer[],integer)
  to service_role;

commit;
