"""Operational policy loading remains fail-closed without trusted audit."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_collect.discovery.followup import FollowupLead
from agent_collect.metadata.extraction import extract_labelled_metadata
from agent_collect.metadata.supabase import SupabaseMetadataEvidence
from tests.test_agent_collect_metadata_evidence import NOW, PERMISSION, URL, fixture


class EventQuery:
    def __init__(self, events):
        self.events = events

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args):
        return self

    def order(self, *args):
        return self

    def limit(self, *args):
        return self

    def execute(self):
        return SimpleNamespace(data=self.events, count=len(self.events))


class EventClient:
    def __init__(self, events):
        self.events = events

    def table(self, name):
        assert name == "agent_metadata_approval_events"
        return EventQuery(self.events)


def row_from_policy(policy):
    return {
        "id": str(uuid4()), "source_id": str(policy.source_id),
        "official_url": policy.official_url, "reviewer": policy.reviewer,
        "reviewed_at": policy.reviewed_at.isoformat(),
        "expires_at": policy.expires_at.isoformat(),
        "permission_url": policy.permission_url,
        "permission_digest": policy.permission_digest,
        "organizer_relationship": policy.organizer_relationship,
        "approved_organizer": policy.approved_organizer,
        "page_type": policy.page_type.value,
        "officiality_confirmed": policy.officiality_confirmed,
        "public_read": policy.public_read,
        "automated_access": policy.automated_access,
        "fact_use": policy.fact_use,
        "recheck_allowed": policy.recheck_allowed,
        "raw_storage_allowed": policy.raw_storage_allowed,
        "redistribution_allowed": policy.redistribution_allowed,
        "scope_origin": policy.scope_origin,
        "path_prefix": policy.path_prefix,
    }


def test_operational_policy_requires_authenticated_event_and_revocation_wins():
    source, policy, candidate, page, duplicates = fixture()
    row = row_from_policy(policy)
    missing = SupabaseMetadataEvidence(EventClient([]))._policy(row, cohort="operational")
    assert missing is None
    actor, decision = uuid4(), uuid4()
    approval = {"id": str(decision), "action": "APPROVED",
                "actor_uid": str(actor), "acted_at": NOW.isoformat()}
    loaded = SupabaseMetadataEvidence(EventClient([approval]))._policy(
        row, cohort="operational")
    assert loaded is not None
    assert loaded.approved_principal_id == actor
    assert loaded.approval_event_id == decision
    assert loaded.permits(source, candidate.official_url, now=NOW)[0]
    revocation = {"id": str(uuid4()), "action": "REVOKED",
                  "actor_uid": str(actor),
                  "acted_at": (NOW-timedelta(seconds=1)).isoformat()}
    revoked = SupabaseMetadataEvidence(EventClient([approval, revocation]))._policy(
        row, cohort="operational")
    assert revoked is not None
    assert revoked.permits(source, candidate.official_url, now=NOW) == (
        False, "permission_revoked")


def test_synthetic_policy_readback_does_not_claim_authenticated_approval():
    policy = fixture()[1]
    loaded = SupabaseMetadataEvidence(EventClient([]))._policy(
        row_from_policy(policy), cohort="synthetic")
    assert loaded is not None
    assert loaded.approved_principal_id is None


class ApprovalClient:
    def __init__(self, approval):
        self.approval = approval

    def table(self, name):
        assert name == "agent_metadata_source_approvals"
        return self

    def select(self, *args):
        return self

    def eq(self, *args):
        return self

    def limit(self, *args):
        return self

    def execute(self):
        return SimpleNamespace(data=[self.approval])


def _lead(url):
    return FollowupLead(url, url, 1, "application", "INDIVIDUAL_NOTICE",
                        None, None, "UNKNOWN", "UNKNOWN", None,
                        "requires_source_approval_and_official_evidence_pipeline")


def test_followup_handoff_replays_page_before_any_candidate_write():
    source, policy, candidate, page, duplicates = fixture()
    approval = {**row_from_policy(policy), "cohort": "operational"}
    bridge = SupabaseMetadataEvidence(ApprovalClient(approval))
    bridge.sources.get = lambda _source_id: source
    bridge._policy = lambda _row, cohort: replace(
        policy, approved_principal_id=uuid4(), approval_event_id=uuid4())
    bridge._duplicates = lambda now: duplicates
    writes = []
    bridge.create_candidate = lambda **kwargs: writes.append(kwargs) or candidate.id
    bridge.assess_and_record = lambda *args, **kwargs: writes.append(kwargs) or None
    changed = dict(candidate.facts)
    changed["title"] = replace(changed["title"], quote="search snippet only")
    result = bridge.process_followup_lead(
        lead=_lead(candidate.official_url), approval_id=uuid4(), facts=changed,
        page=page, fetched_at=NOW, permission_document=PERMISSION,
        permission_checked_at=NOW, request_id=uuid4(), decision_id=uuid4(), now=NOW,
    )
    assert result.stored_candidate_id is None
    assert result.outcome.gate.status.value == "REVIEW_REQUIRED"
    assert writes == []


def test_followup_handoff_rejects_listing_without_writes():
    source, policy, candidate, page, duplicates = fixture()
    bridge = SupabaseMetadataEvidence(ApprovalClient({}))
    lead = replace(_lead(candidate.official_url), page_type="OFFICIAL_LISTING")
    try:
        bridge.process_followup_lead(
            lead=lead, approval_id=uuid4(), facts=candidate.facts,
            page=page, fetched_at=NOW, permission_document=None,
            permission_checked_at=None, request_id=uuid4(),
            decision_id=uuid4(), now=NOW,
        )
    except ValueError as error:
        assert "individual lead" in str(error)
    else:
        raise AssertionError("listing lead was promoted")


def test_followup_handoff_uses_only_narrow_candidate_and_assessment_rpcs():
    source, policy, candidate, page, duplicates = fixture()
    approval = {**row_from_policy(policy), "cohort": "operational"}
    bridge = SupabaseMetadataEvidence(ApprovalClient(approval))
    bridge.sources.get = lambda _source_id: source
    bridge._policy = lambda _row, cohort: replace(
        policy, approved_principal_id=uuid4(), approval_event_id=uuid4())
    bridge._duplicates = lambda now: duplicates
    calls = []
    bridge.create_candidate = lambda **kwargs: calls.append(("create", kwargs)) or candidate.id
    bridge.assess_and_record = lambda *args, **kwargs: (
        calls.append(("record", kwargs)) or
        SimpleNamespace(gate=SimpleNamespace(status=SimpleNamespace(value="PUBLISH_READY"))))
    result = bridge.process_followup_lead(
        lead=_lead(candidate.official_url), approval_id=uuid4(),
        facts=candidate.facts, page=page, fetched_at=NOW,
        permission_document=PERMISSION, permission_checked_at=NOW,
        request_id=uuid4(), decision_id=uuid4(), now=NOW,
    )
    assert result.stored_candidate_id == candidate.id
    assert [name for name, _ in calls] == ["create", "record"]
    assert calls[0][1]["facts"]["official_url"] == candidate.official_url
    assert set(calls[0][1]["field_evidence"]) == set(candidate.facts)


LABELLED_PAGE = (
    b"<p>Title: Synthetic AI Expo 2027</p>"
    b"<p>Summary: A synthetic opportunity for isolated tests.</p>"
    b"<p>Organizer: Fixture Lab</p>"
    b"<p>Application opens: 2027-01-04</p>"
    b"<p>Application deadline: 2027-04-13</p>"
    b"<p>Event starts: 2027-04-14</p>"
    b"<p>Event ends: 2027-04-16</p>"
    b"<p>Application method: Apply via the official form</p>"
    b"<p>Category: conference</p>"
)


def test_followup_page_extracts_labels_and_replays_before_narrow_rpcs():
    source, policy, candidate, _, duplicates = fixture()
    approval = {**row_from_policy(policy), "cohort": "operational"}
    bridge = SupabaseMetadataEvidence(ApprovalClient(approval))
    bridge.sources.get = lambda _source_id: source
    bridge._policy = lambda _row, cohort: replace(
        policy, approved_principal_id=uuid4(), approval_event_id=uuid4())
    bridge._duplicates = lambda now: duplicates
    calls = []
    bridge.create_candidate = lambda **kwargs: calls.append(("create", kwargs)) or candidate.id
    bridge.assess_and_record = lambda *args, **kwargs: (
        calls.append(("record", kwargs)) or
        SimpleNamespace(gate=SimpleNamespace(status=SimpleNamespace(value="PUBLISH_READY"))))
    result = bridge.process_followup_lead(
        lead=_lead(URL), approval_id=uuid4(), facts=None,
        page=LABELLED_PAGE, fetched_at=NOW, permission_document=PERMISSION,
        permission_checked_at=NOW, request_id=uuid4(), decision_id=uuid4(), now=NOW,
    )
    assert result.stored_candidate_id == candidate.id
    assert [name for name, _ in calls] == ["create", "record"]
    assert set(calls[0][1]["facts"]) == set(candidate.facts)
    assert calls[0][1]["facts"]["application_deadline"] == "2027-04-13"


@pytest.mark.parametrize("category,requires_event", [
    ("exhibition", True), ("research", False),
    ("grant", False), ("program", False), ("job", False),
])
def test_sdk_holds_unmapped_category_even_when_evidence_replays(category, requires_event):
    source, policy, _, _, duplicates = fixture()
    bridge = SupabaseMetadataEvidence(ApprovalClient({
        **row_from_policy(policy), "cohort": "operational"}))
    bridge.sources.get = lambda _source_id: source
    bridge._policy = lambda _row, cohort: replace(
        policy, approved_principal_id=uuid4(), approval_event_id=uuid4())
    bridge._duplicates = lambda now: duplicates
    writes = []
    bridge.create_candidate = lambda **kwargs: writes.append(kwargs)
    page = LABELLED_PAGE.replace(b"Category: conference", f"Category: {category}".encode())
    if not requires_event:
        page = page.replace(b"<p>Event starts: 2027-04-14</p>", b"").replace(
            b"<p>Event ends: 2027-04-16</p>", b"")
    result = bridge.process_followup_lead(
        lead=_lead(URL), approval_id=uuid4(), facts=None,
        page=page, fetched_at=NOW, permission_document=PERMISSION,
        permission_checked_at=NOW, request_id=uuid4(),
        decision_id=uuid4(), now=NOW,
    )
    assert result.outcome.verification.status == "VERIFIED"
    assert result.outcome.gate.status.value == "REVIEW_REQUIRED"
    assert result.stored_candidate_id is None
    assert writes == []


def test_followup_page_ambiguous_label_holds_without_write():
    source, policy, candidate, _, duplicates = fixture()
    approval = {**row_from_policy(policy), "cohort": "operational"}
    bridge = SupabaseMetadataEvidence(ApprovalClient(approval))
    bridge.sources.get = lambda _source_id: source
    bridge._policy = lambda _row, cohort: replace(
        policy, approved_principal_id=uuid4(), approval_event_id=uuid4())
    bridge._duplicates = lambda now: duplicates
    writes = []
    bridge.create_candidate = lambda **kwargs: writes.append(kwargs)
    ambiguous = LABELLED_PAGE.replace(
        b"<p>Application deadline: 2027-04-13</p>",
        b"<p>Application deadline: 2027-04-13</p>"
        b"<p>Application deadline: 2027-05-13</p>")
    result = bridge.process_followup_lead(
        lead=_lead(URL), approval_id=uuid4(), facts=None,
        page=ambiguous, fetched_at=NOW, permission_document=PERMISSION,
        permission_checked_at=NOW, request_id=uuid4(), decision_id=uuid4(), now=NOW,
    )
    assert result.stored_candidate_id is None
    assert result.outcome.gate.status.value == "REVIEW_REQUIRED"
    assert writes == []


def test_followup_page_requires_authenticated_approval_before_extraction():
    source, policy, _, _, _ = fixture()
    approval = {**row_from_policy(policy), "cohort": "operational"}
    bridge = SupabaseMetadataEvidence(ApprovalClient(approval))
    bridge.sources.get = lambda _source_id: source
    bridge._policy = lambda _row, cohort: policy
    try:
        bridge.process_followup_lead(
            lead=_lead(URL), approval_id=uuid4(), facts=None,
            page=LABELLED_PAGE, fetched_at=NOW, permission_document=PERMISSION,
            permission_checked_at=NOW, request_id=uuid4(), decision_id=uuid4(), now=NOW,
        )
    except ValueError as error:
        assert "authenticated metadata approval" in str(error)
    else:
        raise AssertionError("unapproved page was extracted")


def test_labelled_extraction_rejects_changed_permission_document():
    source, policy, _, _, _ = fixture()
    policy = replace(policy, approved_principal_id=uuid4(), approval_event_id=uuid4())
    try:
        extract_labelled_metadata(
            source=source, policy=policy, page=LABELLED_PAGE,
            page_url=URL, fetched_at=NOW, permission_document=b"changed",
            permission_checked_at=NOW, now=NOW,
        )
    except ValueError as error:
        assert "permission" in str(error)
    else:
        raise AssertionError("changed permission was accepted")


def test_labelled_extraction_replays_korean_date_and_exact_time():
    source, policy, _, _, _ = fixture()
    policy = replace(policy, approved_principal_id=uuid4(), approval_event_id=uuid4())
    page = LABELLED_PAGE.replace(
        b"Application deadline: 2027-04-13",
        "Application deadline: 2027년 4월 13일".encode()).replace(
        b"Event starts: 2027-04-14",
        b"Event starts: 2027-04-14T09:00:00+09:00")
    facts = extract_labelled_metadata(
        source=source, policy=policy, page=page, page_url=URL,
        fetched_at=NOW, permission_document=PERMISSION,
        permission_checked_at=NOW, now=NOW,
    )
    assert facts["application_deadline"].value == "2027-04-13"
    assert facts["event_start"].value == "2027-04-14T09:00:00+09:00"
