"""Unresolved clocks cannot silently become date-only recruitment deadlines."""
from dataclasses import replace
from uuid import uuid4

import pytest

from agent_collect.discovery.evidence import EvidenceClaim, TimePrecision, _replay_claim
from agent_collect.extraction.preprocess import preprocess_html
from agent_collect.metadata.extraction import _date_value
from agent_collect.metadata.supabase import SupabaseMetadataEvidence
from tests.test_agent_collect_metadata_evidence import NOW, PERMISSION, URL, fixture
from tests.test_agent_metadata_supabase import ApprovalClient, LABELLED_PAGE, _lead, row_from_policy


@pytest.mark.parametrize('literal', [
    '2027-04-13 18:00', '2027-04-13 오후 6시', '2027-04-13 18시',
    '2027-04-13 6 PM', '2027-04-13 6 p.m.', '2027-04-13 18:00 KST',
    '2027-04-13T18:00+09:00 or 19:00',
])
def test_unresolved_or_additional_clock_is_not_extracted_as_a_calendar_day(literal):
    assert _date_value(literal) is None


@pytest.mark.parametrize('literal,expected', [
    ('2027-04-13', '2027-04-13'),
    ('2027년 4월 13일', '2027-04-13'),
    ('2027-04-13T18:00+09:00', '2027-04-13T18:00+09:00'),
    ('2027-04-13T09:00:00Z', '2027-04-13T09:00:00Z'),
])
def test_original_date_and_explicit_offset_are_preserved(literal, expected):
    assert _date_value(literal) == expected


@pytest.mark.parametrize('literal', ['2027-04-13 18:00', '2027-04-13 오후 6시',
                                     '2027-04-13T18:00+09:00'])
def test_replay_rejects_supplied_date_that_strips_the_cited_clock(literal):
    text = 'Application deadline: ' + literal
    prepared = preprocess_html(('<p>'+text+'</p>').encode(), source_url=URL, max_input_chars=12000)
    block = next(block for block in prepared.evidence_blocks if block.text == text)
    value, precision, error = _replay_claim(
        'registration_deadline', EvidenceClaim('2027-04-13', text, (block.block_id,), ()),
        prepared, URL)
    assert value is None
    assert precision is TimePrecision.UNKNOWN
    assert error == 'date_only_discards_time'


def test_actual_sdk_handoff_holds_unzoned_deadline_before_candidate_write():
    source, policy, _, _, duplicates = fixture()
    bridge = SupabaseMetadataEvidence(ApprovalClient({**row_from_policy(policy), 'cohort':'operational'}))
    bridge.sources.get = lambda identity: source
    bridge._policy = lambda row, cohort: replace(policy, approved_principal_id=uuid4(), approval_event_id=uuid4())
    bridge._duplicates = lambda now: duplicates
    def forbidden(**kwargs):
        raise AssertionError('unresolved time must not reach Candidate creation')
    bridge.create_candidate = forbidden
    result = bridge.process_followup_lead(
        lead=_lead(URL), approval_id=uuid4(), facts=None,
        page=LABELLED_PAGE.replace(b'Application deadline: 2027-04-13',
                                   b'Application deadline: 2027-04-13 18:00'),
        fetched_at=NOW, permission_document=PERMISSION, permission_checked_at=NOW,
        request_id=uuid4(), decision_id=uuid4(), now=NOW)
    assert result.stored_candidate_id is None
    assert result.outcome.gate.status.value == 'REVIEW_REQUIRED'
    assert 'application_deadline:missing_evidence' in result.outcome.reasons
