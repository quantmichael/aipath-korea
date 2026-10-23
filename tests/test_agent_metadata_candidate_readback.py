"""Do not mistake a deduplicated old Candidate for newly supplied evidence."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import MagicMock
import pytest
from agent_collect.metadata.supabase import SupabaseMetadataEvidence
from tests.test_agent_collect_metadata_evidence import NOW, URL


@pytest.mark.parametrize('case', ['identical', 'facts_changed', 'evidence_changed', 'missing'])
def test_candidate_readback_matches_exact_submitted_facts_and_evidence(case):
    candidate_id = uuid4()
    facts = {'official_url': URL, 'title': 'Synthetic AI conference'}
    evidence = {'title': {'quote': 'Synthetic AI conference', 'confidence': 0.95}}
    stored = {'facts': deepcopy(facts), 'field_evidence': deepcopy(evidence)}
    if case == 'facts_changed':
        stored['facts']['title'] = 'Old title'
    if case == 'evidence_changed':
        stored['field_evidence']['title']['quote'] = 'Old quote'
    client = MagicMock()
    client.rpc.return_value.execute.return_value.data = str(candidate_id)
    query = client.table.return_value.select.return_value.eq.return_value
    query.limit.return_value.execute.return_value = SimpleNamespace(
        data=[] if case == 'missing' else [stored])
    bridge = SupabaseMetadataEvidence(client)
    args = dict(request_id=uuid4(), approval_id=uuid4(), facts=facts,
                field_evidence=evidence, discovered_at=NOW)
    if case == 'identical':
        assert bridge.create_candidate(**args) == candidate_id
    else:
        with pytest.raises(ValueError, match='existing metadata candidate differs'):
            bridge.create_candidate(**args)
    client.rpc.assert_called_once_with('agent_metadata_create_candidate', {
        'p_request_id': str(args['request_id']), 'p_approval_id': str(args['approval_id']),
        'p_facts': facts, 'p_field_evidence': evidence, 'p_discovered_at': NOW.isoformat()})
    client.table.assert_called_once_with('agent_metadata_candidates')
    client.table.return_value.select.assert_called_once_with('facts,field_evidence')
    client.table.return_value.select.return_value.eq.assert_called_once_with('id', str(candidate_id))
    client.table.return_value.update.assert_not_called()
    client.table.return_value.delete.assert_not_called()


def test_conflicting_readback_stops_before_assessment_or_gate_write():
    from dataclasses import replace
    from tests.test_agent_collect_metadata_evidence import fixture, PERMISSION
    from tests.test_agent_metadata_supabase import row_from_policy, _lead

    source, policy, candidate, page, duplicates = fixture()
    approval = {**row_from_policy(policy), 'cohort': 'operational'}
    client = MagicMock()
    client.rpc.return_value.execute.return_value.data = str(candidate.id)
    def table(name):
        query = MagicMock()
        rows = ([approval] if name == 'agent_metadata_source_approvals' else
                [{'facts': {'title': 'Old unrelated evidence'}, 'field_evidence': {}}])
        query.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = rows
        return query
    client.table.side_effect = table
    bridge = SupabaseMetadataEvidence(client)
    bridge.sources.get = lambda _: source
    bridge._policy = lambda _row, cohort: replace(
        policy, approved_principal_id=uuid4(), approval_event_id=uuid4())
    bridge._duplicates = lambda now: duplicates
    bridge.assess_and_record = MagicMock()
    with pytest.raises(ValueError, match='existing metadata candidate differs'):
        bridge.process_followup_lead(
            lead=_lead(URL), approval_id=uuid4(), facts=candidate.facts,
            page=page, fetched_at=NOW, permission_document=PERMISSION,
            permission_checked_at=NOW, request_id=uuid4(), decision_id=uuid4(), now=NOW)
    bridge.assess_and_record.assert_not_called()
    assert [call.args[0] for call in client.rpc.call_args_list] == ['agent_metadata_create_candidate']
