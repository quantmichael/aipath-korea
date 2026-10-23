"""Only bounded diagnostic metadata survives provider failures."""
from types import SimpleNamespace
import pytest
from agent_collect.extraction.providers import OpenAIExtractionProvider
from tests import test_agent_collect_extraction as extraction_tests

@pytest.mark.parametrize('name,status,cause_name', [
    ('APIConnectionError', None, 'ConnectError'),
    ('BadRequestError', 400, 'NONE'),
    ('PermissionDeniedError', 403, 'NONE'),
    ('SecretExceptionName', 500, 'SecretCauseName'),
])
def test_safe_diagnostics_reach_existing_agent_log(name, status, cause_name):
    secret = 'DO-NOT-LOG-key-payload-or-response'
    error = type(name, (Exception,), {})(secret)
    error.status_code = status
    if cause_name != 'NONE': error.__cause__ = type(cause_name, (Exception,), {})(secret)
    class Responses:
        calls = 0
        def create(self, **kwargs):
            self.calls += 1
            raise error
    responses = Responses()
    case = extraction_tests.ExtractionTests()
    case.setUp()
    try:
        provider = OpenAIExtractionProvider('fixture-key', model='fixture',
                                            client=SimpleNamespace(responses=responses))
        result = case.runner(provider).run(case.document.id)
        assert result.status.value == 'EXTRACTION_FAILED'
        assert responses.calls == 1
        # In-memory repository logs represent the exact structured writer input.
        logs = case.repo.logs
        serialized = repr(logs)
        assert secret not in serialized
        assert 'SecretExceptionName' not in serialized
        assert 'SecretCauseName' not in serialized
        details = next(log['details'] for log in logs
                       if log['event_type'] == 'EXTRACTION_FAILED')
        expected = {'exception_type': name if name != 'SecretExceptionName' else 'OTHER',
                    'cause_type': cause_name if cause_name != 'SecretCauseName' else 'OTHER'}
        if status is not None:
            expected['http_status'] = status
        assert details['provider_diagnostics'] == expected
        assert details['code'] == 'PROVIDER_ERROR'
        assert ('http_status' in serialized) == (status is not None)
    finally:
        case.doCleanups()
