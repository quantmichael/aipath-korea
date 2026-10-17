-- Apply only after 014 and 015, after project-specific reviewer preflight.
-- Existing inactive reviewer rows are preserved. An active unscoped row makes
-- this migration fail rather than silently retaining a global reviewer.
begin;

alter table public.agent_metadata_reviewers
  add column source_id uuid references public.agent_sources(id);
alter table public.agent_metadata_reviewers
  add constraint agent_metadata_active_reviewer_scoped
  check (not active or source_id is not null);

create function public.agent_metadata_reviewer_rescope_guard()
returns trigger language plpgsql set search_path=pg_catalog as $$
begin
  if old.active and new.active and old.source_id is distinct from new.source_id then
    raise exception 'deactivate reviewer before changing Source scope';
  end if;
  return new;
end;
$$;
revoke all on function public.agent_metadata_reviewer_rescope_guard()
  from public, anon, authenticated, service_role;
create trigger agent_metadata_reviewer_rescope_guard
before update of active,source_id on public.agent_metadata_reviewers
for each row execute function public.agent_metadata_reviewer_rescope_guard();

-- The 015 RPCs still authenticate and require an active reviewer. This guard
-- adds a Source-specific authorization check at the immutable audit boundary.
-- A failed guard rolls back the entire approval or revocation RPC transaction.
create function public.agent_metadata_reviewer_scope_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog as $$
declare v_uid uuid := auth.uid();
begin
  if v_uid is null or new.actor_uid is distinct from v_uid or
     new.source_id is distinct from (
       select a.source_id from public.agent_metadata_source_approvals a
       where a.id=new.approval_id
     ) or not exists (
       select 1 from public.agent_metadata_reviewers r
       where r.user_id=v_uid and r.active and r.source_id=new.source_id
       for share
     ) then
    raise exception 'source-scoped metadata reviewer required';
  end if;
  return new;
end;
$$;
revoke all on function public.agent_metadata_reviewer_scope_guard()
  from public, anon, authenticated, service_role;
create trigger agent_metadata_reviewer_scope_guard
before insert on public.agent_metadata_approval_events
for each row execute function public.agent_metadata_reviewer_scope_guard();

commit;
