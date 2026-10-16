"""Synthetic-only metadata evidence: no real organizer permission is implied."""

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from agent_collect.extraction.preprocess import preprocess_html
from agent_collect.discovery.quality import PageType
from agent_collect.metadata.evidence import (
    DuplicateSnapshot, EvidenceType, MetadataAccessPolicy, MetadataCandidate,
    MetadataEvidenceResolver, MetadataFact, metadata_metrics,
)
from agent_collect.quality_gate.models import DuplicateRecord, GateStatus
from agent_collect.schemas.models import Source, SourceType
from agent_collect.schemas.status import SourceStatus


NOW = datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc)
URL = "https://fixture.example.org/notices/synthetic-ai-2027"
PERMISSION = b"Synthetic test-only metadata use and transient recheck permission"
DIGEST = hashlib.sha256(PERMISSION).hexdigest()


def fixture(*, exact=False):
    start = "2027-01-04T09:00:00+09:00" if exact else "2027-01-04"
    deadline = "2027-04-13T18:00:00+09:00" if exact else "2027-04-13"
    event_start = "2027-04-14T09:00:00+09:00" if exact else "2027-04-14"
    event_end = "2027-04-16T18:00:00+09:00" if exact else "2027-04-16"
    content = (
        "<h1>Synthetic AI Expo 2027</h1>"
        "<p>A synthetic opportunity for isolated tests.</p>"
        "<p>Organizer: Fixture Lab</p>"
        f"<p>Application opens {start}</p>"
        f"<p>Application deadline {deadline}</p>"
        f"<p>Event starts {event_start}</p>"
        f"<p>Event ends {event_end}</p>"
        "<p>Apply via the official form</p>"
        "<p>Category: conference</p>"
    ).encode()
    prepared = preprocess_html(content, source_url=URL, max_input_chars=12000)
    lookup = {
        "title": "Synthetic AI Expo 2027",
        "summary": "A synthetic opportunity for isolated tests.",
        "organizer": "Fixture Lab",
        "application_start": start,
        "application_deadline": deadline,
        "event_start": event_start,
        "event_end": event_end,
        "application_method": "Apply via the official form",
        "category": "conference",
    }
    facts = {
        name: MetadataFact(
            value, URL, EvidenceType.PAGE_BLOCK,
            next(block.block_id for block in prepared.evidence_blocks
                 if value in block.text),
            next(block.text for block in prepared.evidence_blocks
                 if value in block.text),
            NOW, 0.95, DIGEST,
        )
        for name, value in lookup.items()
    }
    facts["official_url"] = MetadataFact(
        URL, URL, EvidenceType.PAGE_LINK, "L000", "", NOW, 0.95, DIGEST)
    source = Source(uuid4(), "Synthetic fixture", URL, SourceType.OFFICIAL,
                    SourceStatus.REVIEW, NOW, canonical_url=URL)
    policy = MetadataAccessPolicy(
        source.id, URL, "synthetic-fixture-reviewer", NOW - timedelta(days=1),
        NOW + timedelta(days=1), "https://fixture.example.org/policy",
        DIGEST, "fixture organizer", "Fixture Lab", PageType.INDIVIDUAL_NOTICE,
        True, True, True, True, True,
        False, False, "https://fixture.example.org", "/",
    )
    candidate = MetadataCandidate(uuid4(), source.id, URL, facts, NOW, "synthetic")
    duplicates = DuplicateSnapshot((), (), NOW, True, True)
    return source, policy, candidate, content, duplicates


def assess(data, **changes):
    source, policy, candidate, content, duplicates = data
    return MetadataEvidenceResolver().assess(
        changes.get("candidate", candidate), source=changes.get("source", source),
        policy=changes.get("policy", policy), page=changes.get("page", content),
        page_url=changes.get("page_url", URL),
        fetched_at=changes.get("fetched_at", NOW),
        permission_document=changes.get("permission_document", PERMISSION),
        permission_checked_at=changes.get("permission_checked_at", NOW),
        duplicates=changes.get("duplicates", duplicates), now=NOW,
    )


@pytest.mark.parametrize("exact,precision", [
    (False, "DATE_ONLY"), (True, "EXACT_TIME"),
])
def test_synthetic_metadata_replays_and_reuses_publish_gate(exact, precision):
    outcome = assess(fixture(exact=exact))
    assert outcome.precision.value == precision
    assert outcome.verification.status == "VERIFIED"
    assert outcome.gate.status is GateStatus.PUBLISH_READY
    assert outcome.raw_document_id is None
    assert outcome.publish_queue_written is False


def test_observed_revocation_blocks_even_with_client_clock_skew():
    data = fixture()
    policy = replace(data[1], revoked_at=NOW + timedelta(seconds=30))
    outcome = assess(data, policy=policy)
    assert outcome.gate.status is GateStatus.REVIEW_REQUIRED
    assert "permission_revoked" in outcome.reasons


@pytest.mark.parametrize("change,reason", [
    ({"officiality_confirmed": False}, "officiality_unconfirmed"),
    ({"fact_use": False}, "metadata_use_not_permitted"),
    ({"automated_access": False}, "metadata_use_not_permitted"),
    ({"recheck_allowed": False}, "metadata_use_not_permitted"),
    ({"expires_at": NOW}, "permission_expired"),
])
def test_metadata_permission_fails_closed(change, reason):
    data = fixture()
    outcome = assess(data, policy=replace(data[1], **change))
    assert outcome.gate.status is GateStatus.REVIEW_REQUIRED
    assert reason in outcome.reasons


def test_storage_and_redistribution_are_separate_permissions():
    data = fixture()
    assert data[1].raw_storage_allowed is False
    assert data[1].redistribution_allowed is False
    assert assess(data).gate.status is GateStatus.PUBLISH_READY


def test_listing_is_not_an_individual_notice():
    data = fixture()
    policy = replace(data[1], page_type=PageType.OFFICIAL_LISTING)
    assert "individual_notice_unconfirmed" in assess(data, policy=policy).reasons


@pytest.mark.parametrize("mutation,reason", [
    ("snippet", "title:quote_not_in_raw"),
    ("wrong_reference", "title:invalid_block_id"),
    ("missing_field", "application_deadline:missing_evidence"),
    ("stale_evidence", "title:evidence_invalid"),
    ("unknown_date", "application_time_unconfirmed"),
])
def test_unreplayed_or_missing_evidence_is_review(mutation, reason):
    data = fixture()
    candidate = data[2]
    facts = dict(candidate.facts)
    if mutation == "snippet":
        facts["title"] = replace(facts["title"], quote="search snippet only")
    elif mutation == "wrong_reference":
        facts["title"] = replace(facts["title"], evidence_reference="B999")
    elif mutation == "missing_field":
        del facts["application_deadline"]
    elif mutation == "stale_evidence":
        facts["title"] = replace(facts["title"], observed_at=NOW - timedelta(days=2))
    else:
        facts["application_deadline"] = replace(
            facts["application_deadline"], value="deadline unknown")
    outcome = assess(data, candidate=replace(candidate, facts=facts))
    assert outcome.gate.status is GateStatus.REVIEW_REQUIRED
    assert reason in outcome.reasons


def test_duplicate_and_incomplete_lookup_block():
    data = fixture()
    duplicate = DuplicateRecord(
        str(uuid4()), "service", "Synthetic AI Expo 2027", "Fixture Lab",
        URL, None, URL, "2027-04-14")
    snapshot = replace(data[4], service_records=(duplicate,))
    outcome = assess(data, duplicates=snapshot)
    assert outcome.gate.status is GateStatus.REVIEW_REQUIRED
    assert outcome.duplicate.status.value == "DUPLICATE"
    incomplete = replace(data[4], service_complete=False)
    outcome = assess(data, duplicates=incomplete)
    assert outcome.gate.status is GateStatus.REVIEW_REQUIRED
    assert "duplicate_lookup_incomplete" in outcome.reasons


def test_source_is_not_full_collection_approved():
    source, _, _, _, _ = fixture()
    from agent_collect.registry.approval import source_is_collectible
    assert not source_is_collectible(source)


def test_page_recheck_and_policy_change_block():
    data = fixture()
    assert assess(data, page=b"<p>content replaced</p>").gate.status is GateStatus.REVIEW_REQUIRED
    assert assess(data, page_url="https://other.example.org/").gate.status is GateStatus.REVIEW_REQUIRED
    assert assess(data, fetched_at=NOW - timedelta(days=1)).gate.status is GateStatus.REVIEW_REQUIRED
    policy = replace(data[1], permission_digest="b" * 64)
    assert assess(data, policy=policy).gate.status is GateStatus.REVIEW_REQUIRED
    assert assess(data, permission_document=b"Changed policy").gate.status is GateStatus.REVIEW_REQUIRED
    assert assess(data, permission_checked_at=NOW-timedelta(days=1)).gate.status is GateStatus.REVIEW_REQUIRED
    mismatched = replace(data[1], approved_organizer="Unrelated organizer")
    assert "organizer_relationship_mismatch" in assess(data, policy=mismatched).reasons


def test_revocation_blocks_metadata_even_when_original_approval_is_valid():
    data = fixture()
    revoked = replace(data[1], revoked_at=NOW - timedelta(seconds=1))
    outcome = assess(data, policy=revoked)
    assert outcome.gate.status is GateStatus.REVIEW_REQUIRED
    assert "permission_revoked" in outcome.reasons
    assert "official_page_recheck_missing" in outcome.reasons


def test_operational_metadata_requires_authenticated_approval_event():
    data = fixture()
    operational = replace(data[2], cohort="operational")
    held = assess(data, candidate=operational)
    assert held.gate.status is GateStatus.REVIEW_REQUIRED
    assert "authenticated_metadata_approval_missing" in held.reasons
    ready_policy = replace(data[1], approved_principal_id=uuid4(),
                           approval_event_id=uuid4())
    assert assess(data, candidate=operational,
                  policy=ready_policy).gate.status is GateStatus.PUBLISH_READY


def test_social_page_and_missing_source_approval_block():
    data = fixture()
    social = replace(data[0], source_type=SourceType.COMMUNITY)
    assert assess(data, source=social).gate.status is GateStatus.REVIEW_REQUIRED
    assert assess(data, policy=None).gate.status is GateStatus.REVIEW_REQUIRED


def test_metrics_keep_synthetic_and_operational_separate():
    data = fixture()
    ready = assess(data)
    held = assess(data, policy=None)
    metrics = metadata_metrics(
        (ready, held), cohort="synthetic",
        official_source_ids={data[0].id}, eligible_source_ids={data[0].id})
    assert metrics.metadata_candidates == 2
    assert metrics.publish_ready_candidates == 1
    assert metrics.review_required == 1
    assert metrics.source_approval_bottlenecks == 1
    assert metrics.evidence_resolution_rate == 0.5
    with pytest.raises(ValueError):
        metadata_metrics((ready,), cohort="operational",
                         official_source_ids=set(), eligible_source_ids=set())
