"""Offline contract tests for PHASE 2 Agent Collect foundation."""

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from agent_collect.adapters.service_db.contract import (
    PublishAction,
    PublishResult,
    ServiceDBAdapter,
    ServicePublicationStatus,
)
from agent_collect.config.settings import load_settings
from agent_collect.schemas.models import (
    ClassificationResult,
    CollectionMethod,
    DuplicateMethod,
    DuplicateResult,
    NormalizedItem,
    PublishCandidate,
    RawDocument,
    Source,
    SourceType,
    Vertical,
    VerificationResult,
)
from agent_collect.schemas.status import (
    CandidateStatus,
    PublishStatus,
    SourceStatus,
    VerificationStatus,
    can_transition,
    require_transition,
)
from agent_collect.workflows.state import AgentCollectState


NOW = datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc)


def valid_candidate() -> PublishCandidate:
    item_id = uuid4()
    return PublishCandidate(
        contract_version=1,
        agent_item_id=item_id,
        agent_source_id=uuid4(),
        run_id=uuid4(),
        vertical=Vertical.CONFERENCE,
        title="AI Conference",
        summary="AI research and practice talks",
        organizer="AI Society",
        official_url="https://example.org/conference",
        source_name="AI Society",
        verified_at=NOW,
        verification_status=VerificationStatus.VERIFIED,
        confidence=0.96,
        duplicate_result=DuplicateResult(
            item_id=item_id,
            is_duplicate=False,
            method=DuplicateMethod.NONE,
            confidence=1.0,
        ),
        evidence_refs=("raw:123",),
        event_start_at=NOW + timedelta(days=20),
        event_end_at=NOW + timedelta(days=21),
        tag_slugs=("generative-ai",),
    )


class DomainModelTests(unittest.TestCase):
    def test_source_accepts_candidate_registration_without_collector(self) -> None:
        source = Source(
            id=uuid4(),
            name="AI Society",
            url="https://example.org",
            source_type=SourceType.OFFICIAL,
            status=SourceStatus.CANDIDATE,
            discovered_at=NOW,
            collection_method=CollectionMethod.RSS,
            confidence=0.8,
            provenance={"discovery_url": "https://example.org/about"},
        )
        self.assertEqual(source.status, SourceStatus.CANDIDATE)

    def test_source_rejects_invalid_url_and_confidence(self) -> None:
        kwargs = dict(
            id=uuid4(), name="Source", url="https://example.org",
            source_type=SourceType.OFFICIAL, status=SourceStatus.CANDIDATE,
            discovered_at=NOW,
        )
        with self.assertRaises(ValueError):
            Source(**{**kwargs, "url": "javascript:alert(1)"})
        with self.assertRaises(ValueError):
            Source(**{**kwargs, "confidence": 1.1})

    def test_raw_document_requires_valid_hash(self) -> None:
        with self.assertRaises(ValueError):
            RawDocument(
                id=uuid4(), run_id=uuid4(), source_id=uuid4(),
                source_url="https://example.org/event", content_ref="storage://raw/1",
                content_sha256="bad-hash", collected_at=NOW,
            )

    def test_normalized_item_rejects_reversed_dates(self) -> None:
        with self.assertRaises(ValueError):
            NormalizedItem(
                id=uuid4(), extracted_item_id=uuid4(), run_id=uuid4(),
                source_id=uuid4(), title="Conference", summary=None,
                organizer=None, official_url="https://example.org/event",
                event_start_at=NOW + timedelta(days=2), event_end_at=NOW,
            )

    def test_classification_confidence_is_bounded(self) -> None:
        with self.assertRaises(ValueError):
            ClassificationResult(uuid4(), Vertical.CONFERENCE, -0.1)

    def test_verified_result_requires_evidence_and_time(self) -> None:
        with self.assertRaises(ValueError):
            VerificationResult(uuid4(), VerificationStatus.VERIFIED, 0.9, ())


class StatusTests(unittest.TestCase):
    def test_stage_precedes_verification_and_publish(self) -> None:
        self.assertTrue(can_transition(CandidateStatus.DEDUP_CHECKED, CandidateStatus.STAGED))
        self.assertTrue(can_transition(CandidateStatus.STAGED, CandidateStatus.VERIFYING))
        self.assertTrue(can_transition(CandidateStatus.VERIFIED, CandidateStatus.PUBLISH_READY))
        self.assertTrue(can_transition(CandidateStatus.PUBLISH_READY, CandidateStatus.PUBLISHED))
        self.assertFalse(can_transition(CandidateStatus.DEDUP_CHECKED, CandidateStatus.VERIFIED))
        self.assertFalse(can_transition(CandidateStatus.VERIFIED, CandidateStatus.STAGED))
        with self.assertRaises(ValueError):
            require_transition(CandidateStatus.STAGED, CandidateStatus.PUBLISHED)

    def test_failure_and_review_branches(self) -> None:
        self.assertTrue(can_transition(CandidateStatus.STAGED, CandidateStatus.FAILED))
        self.assertTrue(can_transition(CandidateStatus.REVIEW_REQUIRED, CandidateStatus.VERIFYING))
        self.assertFalse(can_transition(CandidateStatus.PUBLISHED, CandidateStatus.FAILED))


class PublishCandidateTests(unittest.TestCase):
    def test_valid_publish_candidate(self) -> None:
        self.assertEqual(valid_candidate().vertical, Vertical.CONFERENCE)

    def test_rejects_unverified_duplicate_and_missing_evidence(self) -> None:
        candidate = valid_candidate()
        with self.assertRaises(ValueError):
            replace(candidate, verification_status=VerificationStatus.REVIEW_REQUIRED)
        with self.assertRaises(ValueError):
            replace(candidate, evidence_refs=())
        with self.assertRaises(ValueError):
            replace(
                candidate,
                duplicate_result=DuplicateResult(
                    item_id=candidate.agent_item_id,
                    is_duplicate=True,
                    method=DuplicateMethod.EXACT_URL,
                    confidence=1.0,
                    matched_ref="service:123",
                ),
            )

    def test_rejects_service_incompatible_values(self) -> None:
        candidate = valid_candidate()
        with self.assertRaises(ValueError):
            replace(candidate, format="virtual-reality")
        with self.assertRaises(ValueError):
            replace(candidate, tag_slugs=("Not a slug",))
        with self.assertRaises(ValueError):
            replace(candidate, application_start_at=NOW + timedelta(days=2),
                    application_deadline_at=NOW)


class WorkflowStateTests(unittest.TestCase):
    def test_start_and_transition_validate(self) -> None:
        state = AgentCollectState(run_id=uuid4())
        next_state = state.transition(CandidateStatus.SOURCE_ANALYZED)
        self.assertEqual(next_state.status, CandidateStatus.SOURCE_ANALYZED)
        self.assertEqual(state.status, CandidateStatus.DISCOVERED)
        with self.assertRaises(ValueError):
            state.transition(CandidateStatus.PUBLISHED)

    def test_rejects_cross_run_document_and_negative_retry(self) -> None:
        run_id = uuid4()
        raw = RawDocument(
            id=uuid4(), run_id=uuid4(), source_id=uuid4(),
            source_url="https://example.org/event", content_ref="storage://raw/1",
            content_sha256="a" * 64, collected_at=NOW,
        )
        with self.assertRaises(ValueError):
            AgentCollectState(run_id=run_id, raw_document=raw)
        with self.assertRaises(ValueError):
            AgentCollectState(run_id=run_id, retry_count=-1)

    def test_published_requires_verified_result_and_publish_result(self) -> None:
        run_id = uuid4()
        item_id = uuid4()
        verification = VerificationResult(
            item_id=item_id,
            status=VerificationStatus.VERIFIED,
            confidence=0.96,
            evidence_refs=("raw:123",),
            verified_at=NOW,
        )
        with self.assertRaises(ValueError):
            AgentCollectState(run_id=run_id, status=CandidateStatus.PUBLISHED,
                              verification_result=verification)
        state = AgentCollectState(
            run_id=run_id, status=CandidateStatus.PUBLISHED,
            verification_result=verification, publish_status=PublishStatus.PUBLISHED,
        )
        self.assertEqual(state.publish_status, PublishStatus.PUBLISHED)


class AdapterContractTests(unittest.TestCase):
    def test_adapter_only_receives_publish_candidate(self) -> None:
        class FakeAdapter:
            def publish(self, candidate: PublishCandidate) -> PublishResult:
                self.candidate = candidate
                return PublishResult(
                    agent_item_id=candidate.agent_item_id,
                    action=PublishAction.CREATED,
                    service_opportunity_id=uuid4(),
                    service_slug="ai-conference",
                    published_status=ServicePublicationStatus.OPEN,
                    published_at=NOW,
                )

        adapter: ServiceDBAdapter = FakeAdapter()
        candidate = valid_candidate()
        result = adapter.publish(candidate)
        self.assertIs(adapter.candidate, candidate)
        self.assertEqual(result.action, PublishAction.CREATED)

    def test_publish_result_rejects_incomplete_success(self) -> None:
        with self.assertRaises(ValueError):
            PublishResult(agent_item_id=uuid4(), action=PublishAction.CREATED)


class ConfigurationTests(unittest.TestCase):
    def test_llm_budget_profiles_are_explicit_and_fail_closed(self) -> None:
        development = load_settings(environ={"APP_ENV": "development"})
        self.assertEqual(development.app_env, "development")
        self.assertEqual((development.extraction_daily_llm_limit,
                          development.extraction_monthly_llm_limit), (20, 300))
        self.assertFalse(development.llm_enabled)

        for environment in ({}, {"APP_ENV": "production"},
                            {"APP_ENV": "unknown"}, {"APP_ENV": ""}):
            with self.subTest(environment=environment):
                settings = load_settings(environ=environment)
                self.assertEqual(settings.app_env, "production")
                self.assertEqual((settings.extraction_daily_llm_limit,
                                  settings.extraction_monthly_llm_limit), (5, 100))

    def test_defaults_disable_external_actions(self) -> None:
        settings = load_settings(environ={})
        self.assertFalse(settings.discovery_enabled)
        self.assertFalse(settings.publish_enabled)
        self.assertFalse(settings.llm_enabled)
        self.assertEqual(settings.verification_threshold, 0.75)
        self.assertEqual(settings.publish_threshold, 0.85)
        self.assertEqual(settings.raw_retention_days, 90)

    def test_environment_overrides_and_validation(self) -> None:
        settings = load_settings(environ={
            "AGENT_MAX_RETRIES": "5",
            "AGENT_COLLECTION_TIMEOUT_SECONDS": "30",
            "AGENT_DISCOVERY_ENABLED": "true",
            "AGENT_RAW_RETENTION_DAYS": "120",
        })
        self.assertEqual(settings.max_retries, 5)
        self.assertEqual(settings.collection_timeout_seconds, 30)
        self.assertTrue(settings.discovery_enabled)
        self.assertEqual(settings.raw_retention_days, 120)
        with self.assertRaises(ValueError):
            load_settings(environ={"AGENT_LLM_ENABLED": "true"})
        with self.assertRaises(ValueError):
            load_settings(environ={"AGENT_PUBLISH_THRESHOLD": "0.1"})


if __name__ == "__main__":
    unittest.main()
