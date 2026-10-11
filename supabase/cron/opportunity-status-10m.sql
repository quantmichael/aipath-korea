-- OPERATOR-ONLY activation definition. Do not run during foundation review.
-- Apply and validate migration 002 in a separate deployment, establish trusted
-- per-field time evidence, then inspect the read-only preview before activation.
-- Schedule as the database owner so the restricted function can execute.
-- Preflight: cron.schedule replaces a job with the same name. Stop instead.
do $preflight$
begin
  if exists (select 1 from cron.job where jobname = 'opportunities-status-10m') then
    raise exception 'opportunities-status-10m already exists; inspect before changing it';
  end if;
end;
$preflight$;

select cron.schedule(
  'opportunities-status-10m',
  '*/10 * * * *',
  $$select * from public.reconcile_opportunity_statuses();$$
);

-- When enabled, inspect public.opportunity_status_automation_runs for counts
-- and cron.job_run_details for failures. This file is NOT a migration.
