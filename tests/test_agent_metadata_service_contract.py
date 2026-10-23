"""Synthetic, read-only checks for category mapping and Atomic RPC envelopes."""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from agent_collect.adapters.service_db.metadata_contract import preview_metadata_registration
from agent_collect.adapters.service_db.adapter import SupabaseServiceDBAdapter
from agent_collect.publishing.worker import PublishWorker
from agent_collect.metadata.category_policy import category_policy
from agent_collect.registry.approval import (
    SourceDecision, SourceReviewCommand, decide_source_transition,
)
from agent_collect.schemas.models import CollectionMethod
from agent_collect.schemas.status import SourceStatus
from tests.test_agent_collect_metadata_evidence import NOW, assess, fixture


def active_source(source):
    source = replace(source, collection_method=CollectionMethod.HTTP_HTML,
                     confidence=0.95)
    for current, target, decision in (
        (SourceStatus.REVIEW, SourceStatus.APPROVED, SourceDecision.APPROVED),
        (SourceStatus.APPROVED, SourceStatus.ACTIVE, SourceDecision.ACTIVATED),
    ):
        source, _ = decide_source_transition(source, SourceReviewCommand(
            source.id, current, target, "synthetic reviewer", "fixture only", decision,
            NOW, uuid4(), officiality_confirmed=True if decision is SourceDecision.APPROVED else None,
            collection_allowed=True if decision is SourceDecision.APPROVED else None,
        ))
    return source


def plan(category, *, event_dates, exact=False, source_change=None,
         policy_change=None, categories=None, at=NOW):
    data = fixture(category=category, event_dates=event_dates, exact=exact)
    source, policy, *_ = data
    outcome = assess(data)
    source = active_source(source) if source_change is None else source_change(source)
    policy = policy if policy_change is None else policy_change(policy)
    slug = category_policy(category).service_slug
    rows = ([{"id": str(uuid4()), "slug": slug, "is_active": True}]
            if categories is None and slug else categories or [])
    return preview_metadata_registration(
        outcome, source=source, approval=policy,
        service_sources=[{"id": str(uuid4()), "name": source.name,
                          "homepage_url": source.url, "is_active": True}],
        service_categories=rows, at=at,
    )


@pytest.mark.parametrize("category,event_dates", [
    ("conference", True), ("competition", False), ("hackathon", True),
    ("education", True), ("meetup", True),
])
def test_supported_category_builds_date_only_atomic_contract(category, event_dates):
    result = plan(category, event_dates=event_dates)
    assert result.eligible and result.status == "scheduled"
    assert result.service_slug == category
    assert result.record["application_deadline_at"] == "2027-04-13T00:00:00+00:00"
    assert result.evidence["application_deadline"] == {
        "precision": "DATE_ONLY", "source_date": "2027-04-13",
        "source_ref": result.evidence["application_deadline"]["source_ref"],
    }
    assert result.evidence["application_deadline"]["source_ref"].endswith("#B005")
    assert result.evidence["event_start"]["precision"] == (
        "DATE_ONLY" if event_dates else "UNKNOWN")
    assert result.record["status"] == "scheduled"


def test_exact_time_keeps_offset_and_no_invented_event_end():
    result = plan("competition", event_dates=False, exact=True)
    assert result.eligible
    assert result.evidence["application_deadline"]["precision"] == "EXACT_TIME"
    assert result.evidence["application_deadline"]["source_literal"].endswith("+09:00")
    assert "event_end_at" not in result.record


def test_adapter_metadata_preview_reads_mapping_but_never_queues_or_writes():
    data = fixture(category="competition", event_dates=False)
    source, approval, *_ = data
    source = active_source(source)
    tables, ledger = MagicMock(), MagicMock()
    tables.list_sources.return_value = [{"id": str(uuid4()), "name": source.name,
                                         "homepage_url": source.url, "is_active": True}]
    tables.list_categories.return_value = [{"id": str(uuid4()),
                                            "slug": "competition", "is_active": True}]
    adapter = SupabaseServiceDBAdapter(tables=tables, ledger=ledger,
                                       publish_enabled=False)
    result = adapter.dry_run_metadata(assess(data), source=source,
                                      approval=approval, at=NOW)
    assert result.eligible
    ledger.assert_not_called()
    tables.register_opportunity.assert_not_called()
    tables.insert_opportunity.assert_not_called()


@pytest.mark.parametrize("category", ["exhibition", "research", "grant", "program", "job"])
def test_unmapped_categories_hold_without_arbitrary_service_slug(category):
    result = plan(category, event_dates=category == "exhibition")
    assert not result.eligible
    assert "CATEGORY_SERVICE_MAPPING_UNAVAILABLE" in result.reasons
    assert result.record is None


def test_missing_active_service_category_holds():
    result = plan("conference", event_dates=True, categories=[
        {"id": str(uuid4()), "slug": "conference", "is_active": False}])
    assert not result.eligible
    assert "SERVICE_CATEGORY_MISSING_OR_AMBIGUOUS" in result.reasons


def test_source_approval_and_metadata_revocation_are_independent_holds():
    source_hold = plan("conference", event_dates=True,
                       source_change=lambda source: source)
    assert not source_hold.eligible
    assert "SOURCE_APPROVAL_NOT_ACTIVE" in source_hold.reasons
    revoked = plan("conference", event_dates=True,
                   policy_change=lambda policy: replace(policy, revoked_at=NOW))
    assert not revoked.eligible
    assert "METADATA_APPROVAL_NOT_ACTIVE" in revoked.reasons


def test_stale_evidence_and_closed_recruitment_hold():
    stale = plan("conference", event_dates=True, at=NOW + timedelta(minutes=16))
    assert "EVIDENCE_SNAPSHOT_STALE_OR_MISMATCHED" in stale.reasons
    closed = plan("conference", event_dates=True, at=NOW + timedelta(days=1))
    assert not closed.eligible


def test_metadata_canary_requires_both_publish_flags_before_any_read():
    metadata, adapter = MagicMock(), MagicMock()
    adapter.publish_enabled = False
    worker = PublishWorker(jobs=MagicMock(), ledger=MagicMock(), adapter=adapter,
                           worker_id="metadata-fixture", metadata=metadata,
                           metadata_publish_enabled=True)
    with pytest.raises(RuntimeError, match="disabled"):
        worker.run_metadata_candidate(uuid4(), page=None, page_url=None,
                                      fetched_at=None, permission_document=None,
                                      permission_checked_at=None, dry_run=False)
    metadata.assert_not_called()
    adapter.tables.assert_not_called()


def test_metadata_canary_explicit_id_dry_run_never_scans_queue_or_writes():
    candidate_id = uuid4()
    metadata, adapter, jobs = MagicMock(), MagicMock(), MagicMock()
    adapter.publish_enabled = False
    metadata.assess.return_value = MagicMock(candidate_id=candidate_id)
    metadata.publication_context.return_value = (MagicMock(), fixture()[1])
    adapter.dry_run_metadata.return_value = MagicMock(
        eligible=True, record={"external_id": f"runtime_agent_metadata:{candidate_id}"},
        evidence={"evidence_ref": f"agent-metadata:{candidate_id}"})
    worker = PublishWorker(jobs=jobs, ledger=MagicMock(), adapter=adapter,
                           worker_id="metadata-fixture", metadata=metadata)
    result = worker.run_metadata_candidate(candidate_id, page=b"synthetic",
                                            page_url="https://fixture.example.org/notice",
                                            fetched_at=NOW, permission_document=b"synthetic",
                                            permission_checked_at=NOW)
    assert result.status == "DRY_RUN"
    assert metadata.assess.call_args.args == (candidate_id,)
    jobs.assert_not_called()
    adapter.tables.assert_not_called()


def test_metadata_canary_lost_response_requires_completion_readback():
    candidate_id, service_id = uuid4(), uuid4()
    metadata, adapter, jobs = MagicMock(), MagicMock(), MagicMock()
    adapter.publish_enabled = True
    adapter.tables.get_metadata_completion.side_effect = [
        None, {"candidate_id": str(candidate_id), "opportunity_id": str(service_id),
               "disposition": "PUBLISHED"}]
    adapter.tables.publish_metadata_atomic.side_effect = TimeoutError("lost response")
    adapter.tables.get_opportunity.return_value = {
        "id": str(service_id), "external_id": f"runtime_agent_metadata:{candidate_id}"}
    metadata.assess_and_record.return_value = MagicMock(candidate_id=candidate_id)
    metadata.publication_context.return_value = (MagicMock(), fixture()[1])
    adapter.dry_run_metadata.return_value = MagicMock(eligible=True,
        record={"external_id": f"runtime_agent_metadata:{candidate_id}"},
        evidence={"evidence_ref": f"agent-metadata:{candidate_id}"})
    worker = PublishWorker(jobs=jobs, ledger=MagicMock(), adapter=adapter,
                           worker_id="metadata-fixture", metadata=metadata,
                           metadata_publish_enabled=True)
    result = worker.run_metadata_candidate(candidate_id, page=b"synthetic",
                                            page_url="https://fixture.example.org/notice",
                                            fetched_at=NOW, permission_document=b"synthetic",
                                            permission_checked_at=NOW, dry_run=False)
    assert result.status == "ALREADY_PUBLISHED"
    assert result.opportunity_id == service_id
    adapter.tables.publish_metadata_atomic.assert_called_once()
    jobs.assert_not_called()
