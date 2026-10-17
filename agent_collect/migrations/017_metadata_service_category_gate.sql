-- Apply after 016 and after the category-aware Agent image is ready.
-- This changes only the metadata Candidate READY constraint. Existing rows
-- are checked by ADD CONSTRAINT; failures roll the entire transaction back.
begin;

do $$
begin
  if not exists (select 1 from pg_trigger
                 where tgname='agent_metadata_reviewer_scope_guard') then
    raise exception 'Migration 016 Source-scoped reviewer guard required';
  end if;
end;
$$;

alter table public.agent_metadata_candidates
  drop constraint agent_metadata_candidate_gate;
alter table public.agent_metadata_candidates
  add constraint agent_metadata_candidate_gate check (
    status <> 'PUBLISH_READY' or (
      verified_at is not null
      and coalesce(lower(btrim(facts->>'category')) in
          ('conference','competition','hackathon','education','meetup'),false)
      and facts ?& array['title','summary','organizer','official_url',
                          'application_start','application_deadline',
                          'application_method','category']
      and field_evidence ?& array['title','summary','organizer','official_url',
                                   'application_start','application_deadline',
                                   'application_method','category']
      and (
        lower(btrim(facts->>'category')) = 'competition'
        or (facts ?& array['event_start','event_end']
            and field_evidence ?& array['event_start','event_end'])
      )
      and ((facts ? 'event_start') = (facts ? 'event_end'))
      and ((field_evidence ? 'event_start') = (field_evidence ? 'event_end'))
      and ((facts ? 'event_start') = (field_evidence ? 'event_start'))
      and verification->>'status'='VERIFIED'
      and duplicate_result->>'status'='UNIQUE'
      and gate_decision->>'status'='PUBLISH_READY'
    )
  );

commit;
