from tests.test_agent_collect_metadata_evidence import fixture
"""Exact-ID publication reconciliation under faults; no network or database."""
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from agent_collect.publishing.worker import PublishWorker


def setup_worker(stage, fault=None):
    candidate, service = uuid4(), uuid4()
    prior = {'candidate_id': str(candidate), 'opportunity_id': str(service),
             'disposition': 'PUBLISHED'}
    row = {'id': str(service), 'external_id': f'runtime_agent_metadata:{candidate}'}
    if fault == 'candidate':
        prior['candidate_id'] = str(uuid4())
    elif fault == 'service':
        row['id'] = str(uuid4())
    elif fault == 'external_id':
        row['external_id'] = f'runtime_agent_metadata:{uuid4()}'
    elif fault == 'disposition':
        prior['disposition'] = 'UNKNOWN'
    elif fault == 'malformed_uuid':
        prior['opportunity_id'] = 'invalid'
    elif fault == 'missing':
        prior = None
    elif fault == 'rpc_identity':
        prior['opportunity_id'] = str(uuid4())
        row['id'] = prior['opportunity_id']
    if fault == 'completion_unavailable':
        prior = TimeoutError('synthetic private upstream error')
    if fault == 'row_unavailable':
        row = TimeoutError('synthetic private upstream error')
    metadata, adapter, jobs = MagicMock(), MagicMock(), MagicMock()
    adapter.publish_enabled = True
    adapter.tables.get_metadata_completion.side_effect = (
        [prior] if stage == 'prior' else [None, prior])
    if isinstance(row, Exception):
        adapter.tables.get_opportunity.side_effect = row
    else:
        adapter.tables.get_opportunity.return_value = row
    if stage == 'lost':
        adapter.tables.publish_metadata_atomic.side_effect = TimeoutError('synthetic lost response')
    else:
        adapter.tables.publish_metadata_atomic.return_value = (service, 'registered')
    metadata.publication_context.return_value = (MagicMock(), fixture()[1])
    adapter.dry_run_metadata.return_value = MagicMock(
        eligible=True, record={'external_id': f'runtime_agent_metadata:{candidate}'},
        evidence={'evidence_ref': f'agent-metadata:{candidate}'})
    worker = PublishWorker(jobs=jobs, ledger=MagicMock(), adapter=adapter,
                           worker_id='offline', metadata=metadata,
                           metadata_publish_enabled=True)
    return worker, candidate, service


def execute(worker, candidate):
    return worker.run_metadata_candidate(
        candidate, page=b'synthetic', page_url='https://fixture.example.org/notice',
        fetched_at=None, permission_document=b'synthetic',
        permission_checked_at=None, dry_run=False)


@pytest.mark.parametrize('stage', ['prior', 'lost', 'success'])
@pytest.mark.parametrize('fault', ['candidate', 'service', 'external_id', 'disposition',
                                   'malformed_uuid', 'completion_unavailable', 'row_unavailable'])
def test_inconsistent_or_unreadable_completion_never_reports_success(stage, fault):
    worker, candidate, _ = setup_worker(stage, fault)
    result = execute(worker, candidate)
    assert result.status == 'UNKNOWN'
    assert result.opportunity_id is None
    assert 'private upstream' not in repr(result)
    assert worker.adapter.tables.publish_metadata_atomic.call_count == (0 if stage == 'prior' else 1)
    if stage == 'prior':
        worker.metadata.assess_and_record.assert_not_called()
    assert worker.jobs.mock_calls == []


@pytest.mark.parametrize('stage', ['lost', 'success'])
def test_missing_completion_after_attempt_never_causes_retry(stage):
    worker, candidate, _ = setup_worker(stage, 'missing')
    assert execute(worker, candidate).status == 'UNKNOWN'
    worker.adapter.tables.publish_metadata_atomic.assert_called_once()


def test_success_response_must_match_completion_service_id():
    worker, candidate, _ = setup_worker('success', 'rpc_identity')
    assert execute(worker, candidate).status == 'UNKNOWN'
    worker.adapter.tables.publish_metadata_atomic.assert_called_once()


@pytest.mark.parametrize('stage,expected', [
    ('prior', 'ALREADY_PUBLISHED'), ('lost', 'ALREADY_PUBLISHED'), ('success', 'PUBLISHED')])
def test_consistent_completion_confirms_exact_candidate_and_service(stage, expected):
    worker, candidate, service = setup_worker(stage)
    result = execute(worker, candidate)
    assert (result.status, result.candidate_id, result.opportunity_id) == (expected, candidate, service)
    assert worker.adapter.tables.publish_metadata_atomic.call_count == (0 if stage == 'prior' else 1)
    assert worker.jobs.mock_calls == []


def test_concurrent_rpc_completion_is_not_reported_as_a_new_publication():
    worker, candidate, service = setup_worker('success')
    worker.adapter.tables.publish_metadata_atomic.return_value = (service, 'already_published')
    result = execute(worker, candidate)
    assert result.status == 'ALREADY_PUBLISHED'
    assert result.opportunity_id == service
    worker.adapter.tables.publish_metadata_atomic.assert_called_once()
