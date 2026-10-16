"""Offline Conference extraction, evidence, budget, and provider-contract tests."""

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from agent_collect.config.settings import load_settings
from agent_collect.extraction.budget import ExtractionBudgetGuard, ExtractionBudgetPolicy
from agent_collect.extraction.preprocess import preprocess_html
from agent_collect.extraction.providers import (
    ExtractionProviderError, FakeExtractionProvider, OpenAIExtractionProvider,
    ExtractionRequest,
)
from agent_collect.extraction.runner import ExtractionRunner
from agent_collect.budget.reservations import SupabaseBudgetReservations
from agent_collect.extraction.schema import FIELD_NAMES, ExtractionStatus
from agent_collect.extraction.validation import parse_unambiguous_date
from agent_collect.repositories.extraction import InMemoryExtractionRepository
from agent_collect.repositories.memory import InMemoryRawDocumentRepository
from agent_collect.schemas.models import RawDocument
from agent_collect.storage.raw import LocalRawContentStorage


URL = "https://conf.example.org/2027"
HTML = b"""<html><head><style>.x{color:red}</style><script>bad()</script></head><body>
<nav>Navigation noise</nav><div class='cookie-banner'>Accept cookies</div>
<h1>AI Conference 2027</h1><p>Organized by Korea AI Society.</p>
<p>Registration closes April 20, 2027.</p><p>Event starts May 3, 2027.</p>
<p>Venue: Seoul Hall. Fee: Free.</p><a href='/register'>Register here</a>
<footer>Footer noise</footer></body></html>"""


def payload() -> dict:
    fields = {name: {"value": None, "evidence_block_ids": [], "link_ids": [], "confidence": None}
              for name in FIELD_NAMES}
    fields["title"] = {"value": "AI Conference 2027", "evidence_block_ids": ["B001"], "link_ids": [], "confidence": 0.95}
    fields["organizer"] = {"value": "Korea AI Society", "evidence_block_ids": ["B002"], "link_ids": [], "confidence": 0.9}
    fields["official_url"] = {"value": None, "evidence_block_ids": [], "link_ids": ["L000"], "confidence": 0.9}
    fields["registration_url"] = {"value": None, "evidence_block_ids": [], "link_ids": ["L001"], "confidence": 0.9}
    fields["registration_deadline"] = {"value": "April 20, 2027", "evidence_block_ids": ["B003"], "link_ids": [], "confidence": 0.9}
    fields["event_start"] = {"value": "May 3, 2027", "evidence_block_ids": ["B004"], "link_ids": [], "confidence": 0.9}
    fields["venue"] = {"value": "Seoul Hall", "evidence_block_ids": ["B005"], "link_ids": [], "confidence": 0.9}
    fields["fee"] = {"value": "Free", "evidence_block_ids": ["B005"], "link_ids": [], "confidence": 0.9}
    return {"is_conference": True, "document_confidence": 0.94, "fields": fields}


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = LocalRawContentStorage(Path(self.temp.name))
        self.documents = InMemoryRawDocumentRepository()
        self.repo = InMemoryExtractionRepository()
        self.source_id, self.run_id = uuid4(), uuid4()
        self.document = self._document(HTML)

    def _document(self, html, *, url=URL):
        receipt = self.storage.put(html)
        row = RawDocument(uuid4(), self.run_id, self.source_id, url,
                          receipt.ref, hashlib.sha256(html).hexdigest(),
                          datetime.now(timezone.utc), media_type="text/html")
        self.documents.save(row)
        return row

    def runner(self, provider, **overrides):
        env = {"AGENT_LLM_ENABLED": "true", "AGENT_LLM_MODEL": "gpt-5.4-nano", **overrides}
        return ExtractionRunner(documents=self.documents, storage=self.storage,
                                repository=self.repo, provider=provider,
                                settings=load_settings(environ=env))

    def test_preprocessor_removes_noise_preserves_content_and_links(self):
        prepared = preprocess_html(HTML, source_url=URL, max_input_chars=12000)
        for text in ("AI Conference 2027", "Registration closes", "Venue: Seoul Hall"):
            self.assertIn(text, prepared.full_text)
        for text in ("bad()", "color:red", "Navigation noise", "Accept cookies", "Footer noise"):
            self.assertNotIn(text, prepared.full_text)
        self.assertIn("https://conf.example.org/register", prepared.links)
        self.assertEqual(prepared.evidence_blocks[0].block_id, "B001")
        self.assertEqual(prepared.link_evidence[0].link_id, "L000")
        self.assertEqual(prepared.link_evidence[1].link_id, "L001")
        self.assertEqual(prepared, preprocess_html(HTML, source_url=URL, max_input_chars=12000))
        long = HTML + b"<p>irrelevant " + b"x" * 3000 + b"</p><p>Deadline: April 20, 2027</p>"
        reduced = preprocess_html(long, source_url=URL, max_input_chars=400)
        self.assertTrue(reduced.truncated)
        self.assertLessEqual(len(reduced.model_input), 400)
        self.assertIn("Deadline: April 20, 2027", reduced.selected_text)

    def test_fragmented_date_label_is_one_stable_block(self):
        html = b"<p>Registration Deadline:</p><span>Dec. 20,</span><span>2026</span>"
        first = preprocess_html(html, source_url=URL, max_input_chars=12000)
        second = preprocess_html(html, source_url=URL, max_input_chars=12000)
        self.assertEqual(first.evidence_blocks[0].text, "Registration Deadline: Dec. 20, 2026")
        self.assertEqual(first.evidence_blocks[0].content_hash, second.evidence_blocks[0].content_hash)
        self.assertEqual(first.evidence_blocks[0].source_position, 1)

    def test_valid_conference_and_duplicate_raw(self):
        provider = FakeExtractionProvider(payload())
        runner = self.runner(provider)
        outcome = runner.run(self.document.id)
        self.assertEqual(outcome.status, ExtractionStatus.EXTRACTED)
        self.assertEqual(outcome.candidate.fields["registration_deadline"].value, "2027-04-20")
        self.assertEqual(outcome.candidate.fields["event_start"].value, "2027-05-03")
        self.assertIsNone(outcome.candidate.fields["event_end"].value)
        self.assertEqual(self.repo.rows[outcome.candidate_id].raw_document_id, self.document.id)
        cached = runner.run(self.document.id)
        self.assertEqual(cached.status, ExtractionStatus.CACHED)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(self.repo.count_attempts_since(datetime(2000, 1, 1, tzinfo=timezone.utc)), 1)

    def test_shared_llm_reservation_failure_prevents_provider_call(self):
        from agent_collect.budget.reservations import BudgetAlreadyReserved

        class Reservations:
            def reserve(self, **kwargs):
                raise BudgetAlreadyReserved("duplicate attempt")
            def dispatch(self, reservation_id):
                raise AssertionError("must not dispatch")
            def finish(self, reservation_id, **kwargs):
                raise AssertionError("must not finish")

        provider = FakeExtractionProvider(payload())
        runner = ExtractionRunner(
            documents=self.documents, storage=self.storage, repository=self.repo,
            provider=provider, settings=load_settings(environ={
                "AGENT_LLM_ENABLED": "true", "AGENT_LLM_MODEL": "gpt-5.4-nano"}),
            reservations=Reservations())
        with self.assertRaises(BudgetAlreadyReserved):
            runner.run(self.document.id)
        self.assertEqual(provider.calls, [])

    def test_supabase_openai_reserves_with_planner_off(self):
        provider = FakeExtractionProvider(payload())
        provider.name = "openai"
        repository = InMemoryExtractionRepository()
        repository.client = object()
        settings = load_settings(environ={
            "AGENT_LLM_ENABLED": "true", "AGENT_LLM_MODEL": "gpt-5.4-nano"})
        self.assertFalse(settings.ai_discovery_planner_enabled)
        runner = ExtractionRunner(
            documents=self.documents, storage=self.storage, repository=repository,
            provider=provider, settings=settings)
        self.assertIsInstance(runner.reservations, SupabaseBudgetReservations)
        with self.assertRaises(AttributeError):
            runner.run(self.document.id)
        self.assertEqual(provider.calls, [])

    def test_original_model_confidence_survives_validation_clamp_in_provenance(self):
        data = payload()
        data["fields"]["title"]["value"] = "Unsupported title"
        outcome = self.runner(FakeExtractionProvider(data)).run(self.document.id)
        self.assertEqual(outcome.status, ExtractionStatus.REVIEW_REQUIRED)
        saved = self.repo.rows[outcome.candidate_id]
        self.assertEqual(saved.payload["document_confidence"], 0.49)
        self.assertEqual(saved.provenance["model_document_confidence"], 0.94)
        self.assertEqual(saved.provenance["validated_document_confidence"], 0.49)
        self.assertTrue(saved.provenance["confidence_clamped"])

    def test_same_content_other_raw_is_cached_without_second_call(self):
        provider = FakeExtractionProvider(payload())
        runner = self.runner(provider)
        runner.run(self.document.id)
        same = self._document(HTML)
        result = runner.run(same.id)
        self.assertEqual(result.status, ExtractionStatus.CACHED)
        self.assertEqual(result.errors, ("DUPLICATE_CONTENT_VERSION",))
        self.assertEqual(len(provider.calls), 1)

    def test_missing_fields_and_non_conference(self):
        sparse = payload()
        sparse["fields"]["organizer"] = {"value": None, "evidence_block_ids": [], "link_ids": [], "confidence": None}
        result = self.runner(FakeExtractionProvider(sparse)).run(self.document.id)
        self.assertEqual(result.status, ExtractionStatus.REVIEW_REQUIRED)
        self.assertIn("organizer:missing", result.errors)
        other_doc = self._document(b"<h1>Cooking recipes</h1>")
        other = payload(); other["is_conference"] = False
        result = self.runner(FakeExtractionProvider(other)).run(other_doc.id)
        self.assertEqual(result.status, ExtractionStatus.NOT_CONFERENCE)
        self.assertTrue(all(field.value is None for field in result.candidate.fields.values()))

    def test_hallucinated_evidence_date_and_url_are_nulled(self):
        bad = payload()
        bad["fields"]["organizer"]["evidence_block_ids"] = ["B999"]
        bad["fields"]["event_start"]["value"] = "next Friday"
        bad["fields"]["registration_url"]["value"] = "https://evil.example.net/apply"
        result = self.runner(FakeExtractionProvider(bad)).run(self.document.id)
        self.assertEqual(result.status, ExtractionStatus.REVIEW_REQUIRED)
        for name in ("organizer", "event_start", "registration_url"):
            self.assertIsNone(result.candidate.fields[name].value)
            self.assertEqual(result.candidate.fields[name].confidence, 0)
        stored = self.repo.rows[result.candidate_id].payload["fields"]["organizer"]
        self.assertEqual(stored["evidence_block_ids"], ["B999"])
        self.assertEqual(stored["validation_status"], "INVALID_BLOCK_ID")
        self.assertIsNone(parse_unambiguous_date("coming November"))
        self.assertIsNone(parse_unambiguous_date("April 20"))
        self.assertEqual(parse_unambiguous_date("Apr. 23, 2027"), "2027-04-23")

    def test_private_registration_url_is_rejected(self):
        bad = payload()
        bad["fields"]["registration_url"]["value"] = "http://127.0.0.1/register"
        result = self.runner(FakeExtractionProvider(bad)).run(self.document.id)
        self.assertEqual(result.status, ExtractionStatus.REVIEW_REQUIRED)
        self.assertIsNone(result.candidate.fields["registration_url"].value)
        self.assertIn("registration_url:url_mismatch", result.errors)

    def test_multiple_blocks_unicode_punctuation_and_value_mismatch(self):
        doc = self._document(b"<h1>AI\xe2\x80\x93Conference</h1><p>2027</p><p>Hosted by Yonsei University.</p>")
        page = preprocess_html(self.storage.get(doc.content_ref), source_url=URL,
                               max_input_chars=12000)
        self.assertEqual([block.block_id for block in page.evidence_blocks[:3]],
                         ["B001", "B002", "B003"])
        data = payload()
        data["fields"]["title"] = {"value": "AI Conference  2027", "evidence_block_ids": ["B001", "B002"],
                                     "link_ids": [], "confidence": 0.9}
        data["fields"]["organizer"] = {"value": "Wrong University", "evidence_block_ids": ["B003"],
                                         "link_ids": [], "confidence": 0.9}
        result = self.runner(FakeExtractionProvider(data)).run(doc.id)
        self.assertEqual(result.candidate.fields["title"].value, "AI Conference  2027")
        self.assertEqual(result.candidate.fields["title"].evidence_block_ids, ("B001", "B002"))
        self.assertIsNone(result.candidate.fields["organizer"].value)
        self.assertIn("organizer:value_mismatch", result.errors)

    def test_date_range_and_missing_evidence(self):
        doc = self._document(b"<h1>AI Conference 2027</h1><p>Hosted by Korea AI Society.</p>"
                             b"<p>Conference Dates: Apr. 23-26, 2027</p>")
        data = payload()
        data["fields"]["event_start"] = {"value": "2027-04-23", "evidence_block_ids": ["B003"],
                                           "link_ids": [], "confidence": 0.9}
        data["fields"]["event_end"] = {"value": "2027-04-26", "evidence_block_ids": ["B003"],
                                         "link_ids": [], "confidence": 0.9}
        data["fields"]["venue"] = {"value": "Seoul Hall", "evidence_block_ids": [],
                                     "link_ids": [], "confidence": 0.9}
        result = self.runner(FakeExtractionProvider(data)).run(doc.id)
        self.assertEqual(result.candidate.fields["event_start"].value, "2027-04-23")
        self.assertEqual(result.candidate.fields["event_end"].value, "2027-04-26")
        self.assertIsNone(result.candidate.fields["venue"].value)
        self.assertIn("venue:missing_evidence", result.errors)
        mismatch = payload()
        mismatch["fields"]["event_start"] = {"value": "2026-05-18", "evidence_block_ids": ["B003"],
                                               "link_ids": [], "confidence": 0.9}
        other = self._document(b"<h1>AI Conference 2027</h1><p>Hosted by Korea AI Society.</p>"
                               b"<p>Conference Dates: May 18-20, 2027</p>")
        result = self.runner(FakeExtractionProvider(mismatch)).run(other.id)
        self.assertIsNone(result.candidate.fields["event_start"].value)
        self.assertIn("event_start:date_mismatch", result.errors)

    def test_link_id_resolution_and_invalid_link(self):
        data = payload()
        data["fields"]["official_url"]["evidence_block_ids"] = ["B000"]
        result = self.runner(FakeExtractionProvider(data)).run(self.document.id)
        self.assertEqual(result.candidate.fields["registration_url"].value,
                         "https://conf.example.org/register")
        self.assertEqual(result.candidate.fields["registration_url"].link_ids, ("L001",))
        self.assertEqual(result.candidate.fields["official_url"].evidence_block_ids, ())
        other = self._document(HTML + b"<p>More info</p>")
        data["fields"]["registration_url"]["link_ids"] = ["L999"]
        result = self.runner(FakeExtractionProvider(data)).run(other.id)
        self.assertIsNone(result.candidate.fields["registration_url"].value)
        self.assertIn("registration_url:invalid_link_id", result.errors)

    def test_prompt_version_is_separate_cache_key(self):
        provider = FakeExtractionProvider(payload())
        self.runner(provider).run(self.document.id)
        with patch("agent_collect.extraction.runner.PROMPT_VERSION", "test-new-grounding-version"):
            next_result = self.runner(provider).run(self.document.id)
        self.assertEqual(next_result.status, ExtractionStatus.EXTRACTED)
        self.assertEqual(len(provider.calls), 2)

    def test_malformed_provider_failure_timeout_and_retry_limit(self):
        malformed = self.runner(FakeExtractionProvider({"nonsense": 1})).run(self.document.id)
        self.assertEqual(malformed.status, ExtractionStatus.FAILED)
        self.assertEqual(len(self.repo.rows), 0)
        timed = self.runner(FakeExtractionProvider(ExtractionProviderError("TIMEOUT", retryable=True))).run(self.document.id)
        self.assertEqual(timed.errors, ("TIMEOUT",))
        provider = FakeExtractionProvider(payload())
        blocked = self.runner(provider).run(self.document.id, retry_count=2)
        self.assertEqual(blocked.status, ExtractionStatus.BUDGET_BLOCKED)
        self.assertEqual(blocked.errors, ("RETRY_LIMIT_REACHED",))
        # A caller cannot reset the retry budget by passing retry_count=0 again.
        blocked_from_ledger = self.runner(provider).run(self.document.id)
        self.assertEqual(blocked_from_ledger.errors, ("RETRY_LIMIT_REACHED",))
        self.assertEqual(provider.calls, [])

    def test_non_html_raw_is_not_sent_to_llm(self):
        provider = FakeExtractionProvider(payload())
        self.documents.save(replace(self.document, media_type="application/json"))
        outcome = self.runner(provider).run(self.document.id)
        self.assertEqual(outcome.errors, ("UNSUPPORTED_MEDIA_TYPE",))
        self.assertEqual(provider.calls, [])

    def test_saved_candidate_survives_completion_log_failure(self):
        class CompletionLogFailure(InMemoryExtractionRepository):
            def log(self, **kwargs):
                if kwargs["event_type"] == "EXTRACTION_COMPLETED":
                    raise RuntimeError("logging unavailable")
                return super().log(**kwargs)
        self.repo = CompletionLogFailure()
        result = self.runner(FakeExtractionProvider(payload())).run(self.document.id)
        self.assertEqual(result.status, ExtractionStatus.EXTRACTED)
        self.assertIn("COMPLETION_LOG_FAILED", result.errors)
        self.assertIn(result.candidate_id, self.repo.rows)

    def test_daily_monthly_disabled_missing_key_fail_closed(self):
        provider = FakeExtractionProvider(payload())
        self.repo.log(event_type="EXTRACTION_LLM_ATTEMPT", source_id=self.source_id,
                      run_id=self.run_id, details={})
        daily = self.runner(provider, AGENT_EXTRACTION_DAILY_LLM_LIMIT="1").run(self.document.id)
        self.assertEqual(daily.errors, ("DAILY_LLM_LIMIT_REACHED",))
        monthly = self.runner(provider, AGENT_EXTRACTION_DAILY_LLM_LIMIT="10",
                              AGENT_EXTRACTION_MONTHLY_LLM_LIMIT="1").run(self.document.id)
        self.assertEqual(monthly.errors, ("MONTHLY_LLM_LIMIT_REACHED",))
        disabled = self.runner(provider, AGENT_LLM_ENABLED="false").run(self.document.id)
        self.assertEqual(disabled.errors, ("LLM_PROVIDER_DISABLED",))
        missing = self.runner(OpenAIExtractionProvider(None, model="gpt-5.4-nano")).run(self.document.id)
        self.assertEqual(missing.errors, ("MISSING_LLM_CREDENTIAL",))
        self.assertEqual(provider.calls, [])

    def test_openai_provider_uses_structured_output_without_network(self):
        class MockResponses:
            def __init__(self):
                self.request = None
            def create(self, **kwargs):
                self.request = kwargs
                return SimpleNamespace(status="completed", output_text=json.dumps(payload()),
                                       usage=SimpleNamespace(input_tokens=100, output_tokens=200))
        responses = MockResponses()
        client = SimpleNamespace(responses=responses)
        provider = OpenAIExtractionProvider("test-key", model="gpt-5.4-nano", client=client)
        prepared = preprocess_html(HTML, source_url=URL, max_input_chars=12000)
        result = provider.extract(ExtractionRequest(URL, prepared, 1600, 30))
        self.assertEqual(result.input_tokens, 100)
        self.assertEqual(responses.request["text"]["format"]["type"], "json_schema")
        self.assertTrue(responses.request["text"]["format"]["strict"])
        self.assertFalse(responses.request["store"])

    def test_openai_provider_timeout_and_malformed_output_are_safe(self):
        APITimeoutError = type("APITimeoutError", (Exception,), {})
        class FailingResponses:
            def create(self, **kwargs):
                raise APITimeoutError("sensitive detail")
        prepared = preprocess_html(HTML, source_url=URL, max_input_chars=12000)
        provider = OpenAIExtractionProvider("test-key", model="gpt-5.4-nano",
                                            client=SimpleNamespace(responses=FailingResponses()))
        with self.assertRaises(ExtractionProviderError) as context:
            provider.extract(ExtractionRequest(URL, prepared, 1600, 30))
        self.assertEqual(context.exception.code, "TIMEOUT")
        self.assertNotIn("sensitive", str(context.exception))
        class MalformedResponses:
            def create(self, **kwargs):
                return SimpleNamespace(status="completed", output_text="not-json", usage=None)
        provider = OpenAIExtractionProvider("test-key", model="gpt-5.4-nano",
                                            client=SimpleNamespace(responses=MalformedResponses()))
        with self.assertRaises(ExtractionProviderError) as context:
            provider.extract(ExtractionRequest(URL, prepared, 1600, 30))
        self.assertEqual(context.exception.code, "MALFORMED_RESPONSE")


if __name__ == "__main__":
    unittest.main()
