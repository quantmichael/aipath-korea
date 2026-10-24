from dataclasses import replace
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from agent_collect.automation.runtime import AutomationRuntime
from agent_collect.discovery.models import DiscoveryQuery, QueryStatus, SearchResult, SearchResultStatus
from agent_collect.discovery.auto_approval import ApprovalScope
from agent_collect.discovery.followup import FollowupLead, OfficialFollowupExplorer
from agent_collect.config.settings import load_settings
from agent_collect.discovery.quality import Authority, OfficialPageEvidence, PageType
from agent_collect.metadata.evidence import MetadataUsageRights
from agent_collect.metadata.extraction import PublicNoticeFact, extract_labelled_public_facts
from agent_collect.metadata.search_registration import (
    SearchUseReview,
    preview_fetched_search_result, preview_scoped_fetched_search_result,
    preview_scoped_direct_result, preview_scoped_followup_lead,
    preview_search_registration,
    publish_one_search_preview,
    resolve_discovery_service_source, verify_scoped_publication_use,
)
from agent_collect.runtime import AgentRuntime
from tests.test_agent_collect_discovery_followup import FakeTransport, page as http_page


NOW = datetime(2027, 4, 1, tzinfo=timezone.utc)
URL = "https://official.example.org/notices/ai-competition-2027"


def _fact(value, ref):
    return PublicNoticeFact(value, ref, value)


def _inputs():
    result = SearchResult(uuid4(), uuid4(), "fixture", 1, "AI Competition", URL,
                          "untrusted search snippet", NOW)
    page = OfficialPageEvidence(
        URL, NOW, Authority.OFFICIAL_ORGANIZER, PageType.INDIVIDUAL_NOTICE,
        recruitment_start_on=date(2027, 4, 2),
        recruitment_deadline_on=date(2027, 4, 13))
    facts = {
        "title": _fact("2027 AI Competition", "B001"),
        "organizer": _fact("Official Organizer", "B002"),
        "official_url": _fact(URL, "L000"),
        "application_start": _fact("2027-04-02", "B003"),
        "application_deadline": _fact("2027-04-13", "B004"),
        "application_method": _fact("Apply on the official page", "B005"),
        "category": _fact("competition", "B006"),
    }
    source_id = uuid4()
    return dict(facts=facts, official_page=page, search_result=result,
                service_sources=[{"id": str(source_id), "name": "AIPATH AI Discovery",
                                  "source_type": "discovery", "collection_method": "ai_search",
                                  "is_active": True}], category_id=uuid4(),
                category_slug="competition", access_allowed=True,
                metadata_use_allowed=True, access_evidence_ref="robots-and-terms:B007",
                permission_valid_until=NOW+timedelta(days=1),
                page_sha256="a" * 64,
                existing_rows=[],
                snapshot_complete=True, now=NOW)


def test_korean_application_period_replays_each_date_without_inference():
    page = (b"<html><body><p>" +
            "접수기간: 2027.04.02. ~ 2027.04.13.".encode() +
            b"</p></body></html>")
    facts = extract_labelled_public_facts(
        page=page, page_url=URL, fetched_at=NOW, now=NOW)
    assert facts["application_start"].value == "2027-04-02"
    assert facts["application_deadline"].value == "2027-04-13"
    assert facts["application_start"].evidence_reference == facts["application_deadline"].evidence_reference

    exact = extract_labelled_public_facts(
        page=("<p>신청기간: 2027.04.02. 09:00 KST ~ "
              "2027.04.13. 18:00 KST</p>").encode(),
        page_url=URL, fetched_at=NOW, now=NOW)
    assert exact["application_start"].value == "2027-04-02T09:00:00+09:00"
    assert exact["application_deadline"].value == "2027-04-13T18:00:00+09:00"


def test_korean_application_period_reaches_read_only_atomic_preview():
    values = _inputs()
    page = ("<p>Title: 2027 AI Competition</p>"
            "<p>Organizer: Official Organizer</p>"
            "<p>접수기간: 2027.04.02. ~ 2027.04.13.</p>"
            "<p>Application method: Apply on the official page</p>"
            "<p>Category: competition</p>").encode()
    preview = preview_fetched_search_result(
        search_result=values["search_result"], page=page, page_url=URL,
        official_page=values["official_page"],
        service_sources=values["service_sources"],
        service_categories=[{"id": str(values["category_id"]),
                             "slug": "competition", "is_active": True}],
        existing_rows=[], snapshot_complete=True,
        use_review=SearchUseReview(True, "reviewed", "terms:B007", NOW + timedelta(days=1)),
        fetched_at=NOW, now=NOW)
    assert preview.eligible, preview.reasons
    assert preview.evidence["application_start"]["precision"] == "DATE_ONLY"
    assert preview.evidence["application_deadline"]["source_date"] == "2027-04-13"


def test_unlabelled_page_title_requires_unique_page_evidence_match():
    values = _inputs()
    result = replace(values["search_result"], title="2027 AI Competition")
    body = ("<p>Organizer: Official Organizer</p>"
            "<p>접수기간: 2027.04.02. ~ 2027.04.13.</p>"
            "<p>Application method: Apply on the official page</p>"
            "<p>Category: competition</p>")

    def check(page: str, title: str = "2027 AI Competition"):
        return preview_fetched_search_result(
            search_result=replace(result, title=title), page=page.encode(),
            page_url=URL, official_page=values["official_page"],
            service_sources=values["service_sources"],
            service_categories=[{"id": str(values["category_id"]),
                                 "slug": "competition", "is_active": True}],
            existing_rows=[], snapshot_complete=True,
            use_review=SearchUseReview(True, "reviewed", "terms:B007", NOW + timedelta(days=1)),
            fetched_at=NOW, now=NOW)

    ready = check("<h1>2027 AI Competition</h1>" + body)
    assert ready.eligible, ready.reasons
    assert ready.record["title"] == "2027 AI Competition"
    assert "untrusted search snippet" not in str(ready.record)
    assert "REQUIRED_PAGE_FACT_MISSING" in check(
        "<h1>Unrelated Notice</h1>" + body).reasons
    assert "REQUIRED_PAGE_FACT_MISSING" in check(
        "<p>2027 AI Competition</p>" + body).reasons
    assert "REQUIRED_PAGE_FACT_MISSING" in check(
        "<h1>2027 AI Competition</h1><h2>2027 AI Competition</h2>" + body).reasons
    assert "REQUIRED_PAGE_FACT_MISSING" in check(
        "<h1>2027 AI Competition</h1><h1>Other Heading</h1>" + body).reasons


def test_direct_individual_result_requires_reviewed_parent_directory_and_matching_page_title():
    values = _inputs()
    page = (b"<h1>2027 AI Competition</h1>"
            b"<p>Organizer: Official Organizer</p>"
            b"<p>Application starts: 2027-04-02</p>"
            b"<p>Application deadline: 2027-04-13</p>"
            b"<p>Application method: Apply on the official page</p>"
            b"<p>Category: competition</p>")
    officiality = b"<p>Officiality evidence</p>"
    terms = b"<p>Permission evidence</p>"
    scope = ApprovalScope(
        "direct-notice", "https://official.example.org",
        "/notices/", "fixture-reviewer",
        NOW-timedelta(days=1), NOW+timedelta(days=1),
        "https://official.example.org/officiality",
        hashlib.sha256(officiality).hexdigest(),
        "https://official.example.org/terms",
        hashlib.sha256(terms).hexdigest(), "explicit_license")
    snapshot = {name: {"status": "ALLOWED", "evidence_reference": "B001"}
                for name in ("public_read", "automated_access", "fact_extract",
                             "fact_store", "fact_display")}
    snapshot.update({name: {"status": "UNVERIFIED"}
                     for name in ("html_store", "html_redistribute",
                                  "attachment_collect")})
    rights = MetadataUsageRights.from_snapshot(snapshot)
    assert rights is not None
    kwargs = dict(
        search_result=replace(values["search_result"], title="2027 AI Competition"),
        page=page, page_url=URL, scope=scope, rights=rights,
        officiality_document=officiality, terms_document=terms,
        permission_checked_at=NOW,
        service_sources=values["service_sources"],
        service_categories=[{"id": str(values["category_id"]),
                             "slug": "competition", "is_active": True}],
        existing_rows=[], snapshot_complete=True, fetched_at=NOW, now=NOW)
    ready = preview_scoped_direct_result(**kwargs)
    assert ready.eligible, ready.reasons
    assert ready.record["title"] == "2027 AI Competition"
    assert "untrusted search snippet" not in str(ready.record)
    assert "DIRECT_NOTICE_TITLE_UNVERIFIED" in preview_scoped_direct_result(
        **{**kwargs, "page": page.replace(b"<h1>2027 AI Competition</h1>",
                                        b"<h1>Other Notice</h1>")}).reasons
    assert "DIRECT_NOTICE_TITLE_UNVERIFIED" in preview_scoped_direct_result(
        **{**kwargs, "page": page.replace(b"<h1>2027 AI Competition</h1>",
                                        b"<h1>2027 AI Competition</h1><h1>Other Notice</h1>")}).reasons
    assert "DIRECT_NOTICE_SCOPE_UNVERIFIED" in preview_scoped_direct_result(
        **{**kwargs, "scope": replace(scope, path_prefix="/other/")}).reasons
    assert "DIRECT_NOTICE_SCOPE_UNVERIFIED" in preview_scoped_direct_result(
        **{**kwargs, "page_url": URL + "?notice=another"}).reasons


def test_application_period_conflict_or_ambiguity_holds_dates():
    for period in (
        "접수기간: 2027-04-02 ~ 2027-04-13 ~ 2027-04-14",
        "접수기간: 2027-04-02 ~ 2027-04-13 18:00",
    ):
        facts = extract_labelled_public_facts(
            page=f"<p>{period}</p>".encode(), page_url=URL,
            fetched_at=NOW, now=NOW)
        assert "application_start" not in facts
        assert "application_deadline" not in facts
    facts = extract_labelled_public_facts(
        page=(b"<p>Application deadline: 2027-04-14</p>"
              b"<p>" + "접수기간: 2027-04-02 ~ 2027-04-13".encode() + b"</p>"),
        page_url=URL, fetched_at=NOW, now=NOW)
    assert "application_start" not in facts
    assert "application_deadline" not in facts


def test_search_registration_prepares_date_only_atomic_envelope():
    preview = preview_search_registration(**_inputs())
    assert preview.eligible and preview.reasons == ()
    assert preview.record["status"] == "scheduled"
    assert preview.record["application_deadline_at"] == "2027-04-13T00:00:00+00:00"
    assert preview.evidence["application_deadline"] == {
        "precision": "DATE_ONLY", "source_date": "2027-04-13", "source_ref": "B004"}
    assert preview.evidence["official_url"] == URL
    assert preview.evidence["page_sha256"] == "a" * 64
    assert "untrusted search snippet" not in str(preview.record)


def test_search_registration_holds_duplicate_and_unknown_source():
    values = _inputs()
    values["service_sources"] = []
    values["existing_rows"] = [{"official_url": URL, "title": "Other title",
                                "organizer": "Someone else"}]
    preview = preview_search_registration(**values)
    assert not preview.eligible
    assert "EXISTING_OPPORTUNITY_MATCH" in preview.reasons
    assert "SERVICE_SOURCE_UNRESOLVED" in preview.reasons


def test_search_registration_reuses_existing_possible_duplicate_policy():
    values = _inputs()
    values["existing_rows"] = [{"id": str(uuid4()),
                                "official_url": "https://other.example.org/notice",
                                "title": "2027 AI Competitions",
                                "organizer": "Official Organizer",
                                "event_start_at": None}]
    preview = preview_search_registration(**values)
    assert not preview.eligible
    assert "EXISTING_OPPORTUNITY_MATCH" in preview.reasons


def test_discovery_duplicate_is_held_even_if_service_snapshot_has_no_match():
    values = _inputs()
    values["search_result"] = replace(
        values["search_result"], filter_reasons=("existing_opportunity",))
    held = preview_search_registration(**values)
    assert not held.eligible
    assert "DISCOVERY_DUPLICATE" in held.reasons


def test_search_registration_holds_list_and_mismatched_dates():
    values = _inputs()
    values["official_page"] = replace(
        values["official_page"], page_type=PageType.OFFICIAL_LISTING,
        recruitment_deadline_on=date(2027, 4, 14))
    preview = preview_search_registration(**values)
    assert not preview.eligible
    assert "OFFICIAL_INDIVIDUAL_UNVERIFIED" in preview.reasons
    assert "APPLICATION_DEADLINE_EVIDENCE_MISMATCH" in preview.reasons


def test_search_registration_holds_missing_fact_and_incomplete_snapshot():
    values = _inputs()
    del values["facts"]["application_method"]
    values["snapshot_complete"] = False
    preview = preview_search_registration(**values)
    assert not preview.eligible
    assert "REQUIRED_PAGE_FACT_MISSING" in preview.reasons
    assert "SERVICE_SNAPSHOT_INCOMPLETE" in preview.reasons


def test_search_registration_holds_unconfirmed_use_rights():
    values = _inputs()
    values["metadata_use_allowed"] = False
    preview = preview_search_registration(**values)
    assert not preview.eligible
    assert "ACCESS_OR_METADATA_USE_UNCONFIRMED" in preview.reasons


def test_technical_source_is_exact_and_never_created():
    source_id = uuid4()
    rows = [{"id": str(source_id), "name": "AIPATH AI Discovery",
             "source_type": "discovery", "collection_method": "ai_search",
             "is_active": True}]
    assert resolve_discovery_service_source(rows) == source_id
    assert resolve_discovery_service_source(rows + rows) is None
    assert resolve_discovery_service_source([{**rows[0], "is_active": False}]) is None


def test_atomic_registration_is_off_by_default_and_one_call_when_enabled():
    preview = preview_search_registration(**_inputs())

    class Tables:
        calls = 0

        def list_opportunities(self):
            return []

        def register_opportunity(self, payload, evidence):
            self.calls += 1
            assert evidence["official_url"] == payload["official_url"]
            return {**payload, "id": str(uuid4())}, "registered"

    tables = Tables()
    assert publish_one_search_preview(preview, tables).status == "DISABLED"
    assert tables.calls == 0
    outcome = publish_one_search_preview(preview, tables, publish_enabled=True, now=NOW)
    assert outcome.status == "PUBLISHED" and outcome.opportunity_id is not None
    assert tables.calls == 1


def test_atomic_registration_never_retries_unknown_or_existing():
    preview = preview_search_registration(**_inputs())

    class Tables:
        calls = 0
        disposition = "protected_existing"

        def list_opportunities(self):
            return []

        def register_opportunity(self, payload, evidence):
            self.calls += 1
            if self.disposition == "error":
                raise RuntimeError("unknown commit state")
            return {**payload, "id": str(uuid4())}, self.disposition

    tables = Tables()
    assert publish_one_search_preview(preview, tables, publish_enabled=True, now=NOW).status == "DUPLICATE"
    tables.disposition = "error"
    assert publish_one_search_preview(preview, tables, publish_enabled=True, now=NOW).status == "UNKNOWN"
    assert tables.calls == 2


def test_atomic_registration_rechecks_possible_duplicates_before_rpc():
    preview = preview_search_registration(**_inputs())

    class Tables:
        calls = 0

        def list_opportunities(self):
            return [{"id": str(uuid4()), "title": "2027 AI Competitions",
                     "organizer": "Official Organizer",
                     "official_url": "https://other.example.org/notice",
                     "event_start_at": None}]

        def register_opportunity(self, payload, evidence):
            self.calls += 1
            raise AssertionError("possible duplicate must not be published")

    tables = Tables()
    assert publish_one_search_preview(
        preview, tables, publish_enabled=True, now=NOW).status == "DUPLICATE"
    assert tables.calls == 0


def test_atomic_registration_holds_when_final_snapshot_fails():
    preview = preview_search_registration(**_inputs())

    class Tables:
        calls = 0

        def list_opportunities(self):
            raise RuntimeError("incomplete Service scan")

        def register_opportunity(self, payload, evidence):
            self.calls += 1
            raise AssertionError("incomplete snapshot must not publish")

    tables = Tables()
    assert publish_one_search_preview(
        preview, tables, publish_enabled=True, now=NOW).status == "UNKNOWN"
    assert tables.calls == 0


def test_atomic_registration_rechecks_exact_deadline_before_rpc():
    values = _inputs()
    deadline = NOW + timedelta(seconds=30)
    values["facts"]["application_start"] = _fact(
        (NOW-timedelta(hours=1)).isoformat(), "B003")
    values["facts"]["application_deadline"] = _fact(deadline.isoformat(), "B004")
    values["official_page"] = replace(
        values["official_page"], recruitment_start_on=None,
        recruitment_deadline_on=None,
        recruitment_start_at=NOW-timedelta(hours=1),
        recruitment_deadline_at=deadline)
    preview = preview_search_registration(**values)
    assert preview.eligible

    class Tables:
        def list_opportunities(self):
            raise AssertionError("expired notice must stop before Service scan")

    assert publish_one_search_preview(
        preview, Tables(), publish_enabled=True,
        now=NOW+timedelta(seconds=31)).status == "STALE"


def test_atomic_registration_holds_if_deadline_passes_during_service_scan(monkeypatch):
    from agent_collect.metadata import search_registration as registration_module

    values = _inputs()
    deadline = NOW + timedelta(seconds=30)
    values["facts"]["application_start"] = _fact(
        (NOW-timedelta(hours=1)).isoformat(), "B003")
    values["facts"]["application_deadline"] = _fact(deadline.isoformat(), "B004")
    values["official_page"] = replace(
        values["official_page"], recruitment_start_on=None,
        recruitment_deadline_on=None,
        recruitment_start_at=NOW-timedelta(hours=1),
        recruitment_deadline_at=deadline)
    preview = preview_search_registration(**values)
    assert preview.eligible
    clock = {"now": NOW + timedelta(seconds=20)}

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    class Tables:
        def list_opportunities(self):
            clock["now"] = NOW + timedelta(seconds=31)
            return []

        def register_opportunity(self, payload, evidence):
            raise AssertionError("expired notice must stop before Service RPC")

    monkeypatch.setattr(registration_module, "datetime", FakeDatetime)
    assert publish_one_search_preview(
        preview, Tables(), publish_enabled=True).status == "STALE"


def test_atomic_registration_rechecks_kst_date_only_boundary():
    values = _inputs()
    values["now"] = datetime(2027, 4, 12, 14, 59, 30, tzinfo=timezone.utc)
    values["permission_valid_until"] = values["now"] + timedelta(days=1)
    values["official_page"] = replace(values["official_page"],
                                      checked_at=values["now"])
    preview = preview_search_registration(**values)
    assert preview.eligible

    class Tables:
        def list_opportunities(self):
            raise AssertionError("same-day date-only cutoff must stop before scan")

    assert publish_one_search_preview(
        preview, Tables(), publish_enabled=True,
        now=values["now"]+timedelta(seconds=31)).status == "STALE"


def test_stale_registration_preview_never_reaches_atomic_rpc():
    preview = preview_search_registration(**_inputs())

    class Tables:
        def register_opportunity(self, payload, evidence):
            raise AssertionError("stale preview must not be submitted")

    assert publish_one_search_preview(
        preview, Tables(), publish_enabled=True,
        now=NOW+timedelta(minutes=2)).status == "STALE"
    values = _inputs()
    values["permission_valid_until"] = NOW+timedelta(seconds=10)
    expiring = preview_search_registration(**values)
    assert expiring.eligible
    assert publish_one_search_preview(
        expiring, Tables(), publish_enabled=True,
        now=NOW+timedelta(seconds=11)).status == "STALE"


def test_real_automation_entrypoint_calls_new_handoff_once():
    class Lease:
        def acquire(self, **kwargs):
            return True

        def release(self, claim_id):
            return True

        def record_summary(self, details):
            self.details = details

    calls = []
    values = _inputs()
    page = (b"<html><head><link rel='canonical' href='" + URL.encode() +
            b"'></head><body><p>Title: 2027 AI Competition</p>"
            b"<p>Organizer: Official Organizer</p>"
            b"<p>Application starts: 2027-04-02</p>"
            b"<p>Application deadline: 2027-04-13</p>"
            b"<p>Application method: Apply on the official page</p>"
            b"<p>Category: competition</p></body></html>")
    lease = Lease()

    class Worker:
        def run_once(self, *, dry_run):
            return SimpleNamespace(query_ids=("query-one",))

    def handoff(ids):
        calls.append(("new", ids))
        preview = preview_fetched_search_result(
            search_result=values["search_result"], page=page, page_url=URL,
            official_page=values["official_page"],
            service_sources=values["service_sources"],
            service_categories=[{"id": str(values["category_id"]),
                                 "slug": "competition", "is_active": True}],
            existing_rows=[], snapshot_complete=True,
            use_review=SearchUseReview(True, "synthetic", "test-scope:B007",
                                       NOW+timedelta(days=1)),
            fetched_at=NOW, now=NOW)
        return {"preview_count": int(preview.eligible)}

    runtime = AutomationRuntime(
        enabled=True, worker_enabled={"discovery": True},
        workers={"discovery": Worker}, lease=lease, worker_id="search-test",
        metadata_handoff=lambda ids: calls.append(("old", ids)),
        search_registration_handoff=handoff)
    assert runtime.run_once(dry_run=True).results == {"discovery": Worker().run_once(dry_run=True)}
    assert calls == []
    result = runtime.run_once()
    assert result.status == "COMPLETED"
    assert calls == [("old", ("query-one",)), ("new", ("query-one",))]
    assert result.results["metadata"] is None
    assert result.results["search_registration"] == {"preview_count": 1}
    assert lease.details["search_registration"] == {"preview_count": 1}


def test_search_handoff_failure_does_not_erase_existing_metadata_handoff():
    class Lease:
        def acquire(self, **kwargs):
            return True

        def release(self, claim_id):
            return True

        def record_summary(self, details):
            self.details = details

    class Worker:
        def run_once(self, *, dry_run):
            return SimpleNamespace(query_ids=("query-one",))

    def broken_search(ids):
        raise ValueError("synthetic failure")

    lease = Lease()
    runtime = AutomationRuntime(
        enabled=True, worker_enabled={"discovery": True},
        workers={"discovery": Worker}, lease=lease, worker_id="search-isolation",
        metadata_handoff=lambda ids: {"processed_count": 1},
        search_registration_handoff=broken_search)
    result = runtime.run_once()
    assert result.status == "PARTIAL"
    assert result.results["metadata"] == {"processed_count": 1}
    assert result.errors == {"search_registration": "ValueError"}
    assert lease.details["metadata"] == {"processed_count": 1}


def test_real_automation_assembly_uses_only_injected_reviewed_search_permission(monkeypatch):
    from agent_collect import runtime as runtime_module

    now = datetime.now(timezone.utc)
    listing = "https://official.example.org/notices/"
    query_id = uuid4()
    query = DiscoveryQuery(query_id, "synthetic search", "fake", now,
                           status=QueryStatus.SUCCEEDED)
    result = SearchResult(uuid4(), query_id, "fake", 1, "Official listing",
                          listing, "untrusted snippet", now, canonical_url=listing,
                          status=SearchResultStatus.CANDIDATE)
    page = (b"<html><body><p>Title: 2027 AI Competition</p>"
            b"<p>Organizer: Official Organizer</p>"
            b"<p>Application starts: 2027-04-02</p>"
            b"<p>Application deadline: 2027-04-13</p>"
            b"<p>Application method: Apply on the official page</p>"
            b"<p>Category: competition</p></body></html>")
    officiality = b"<p>Officiality evidence</p>"
    terms = b"<p>Permission evidence</p>"
    scope = ApprovalScope(
        "synthetic", "https://official.example.org", "/notices/",
        "fixture-reviewer", now-timedelta(days=1), now+timedelta(days=1),
        "https://official.example.org/officiality",
        hashlib.sha256(officiality).hexdigest(),
        "https://official.example.org/terms", hashlib.sha256(terms).hexdigest(),
        "explicit_license")
    rights_snapshot = {name: {"status": "ALLOWED", "evidence_reference": "B001"}
                       for name in ("public_read", "automated_access", "fact_extract",
                                    "fact_store", "fact_display")}
    rights_snapshot.update({name: {"status": "UNVERIFIED"}
                            for name in ("html_store", "html_redistribute", "attachment_collect")})
    rights = MetadataUsageRights.from_snapshot(rights_snapshot)
    assert rights is not None
    transport = FakeTransport({
        scope.officiality_evidence_url: http_page(scope.officiality_evidence_url,
                                                    officiality.decode()),
        scope.terms_url: http_page(scope.terms_url, terms.decode()),
        listing: http_page(listing, f'<a href="{URL}">AI 참가 신청</a>'),
        URL: http_page(URL, page.decode()),
    })

    class QueryRows:
        def select(self, *args, **kwargs): return self
        def eq(self, *args): return self
        def limit(self, value): return self
        def execute(self):
            return SimpleNamespace(data=[{"id": str(result.id)}], count=1)

    class Client:
        def table(self, name):
            assert name == "agent_search_results"
            return QueryRows()

    class Repository:
        def __init__(self, client): pass
        def get_query(self, identity): return query if identity == query_id else None
        def get_result(self, identity): return result if identity == result.id else None

    class Tables:
        def __init__(self): self.writes = 0
        def list_sources(self): return _inputs()["service_sources"]
        def list_categories(self):
            return [{"id": str(_inputs()["category_id"]), "slug": "competition",
                     "is_active": True}]
        def list_opportunities(self): return []
        def register_opportunity(self, record, evidence):
            self.writes += 1
            return {**record, "id": str(uuid4())}, "registered"

    class Lease:
        def __init__(self, client): pass
        def acquire(self, **kwargs): return True
        def release(self, claim_id): return True
        def record_summary(self, details): pass

    class Discovery:
        def run_once(self, *, dry_run):
            return SimpleNamespace(query_ids=() if dry_run else (str(query_id),))

    tables = Tables()
    monkeypatch.setattr(runtime_module, "SupabaseDiscoveryRepository", Repository)
    monkeypatch.setattr(runtime_module, "SupabaseServiceTables", lambda client: tables)
    monkeypatch.setattr(runtime_module, "OfficialFollowupExplorer",
                        lambda **kwargs: OfficialFollowupExplorer(transport=transport))
    monkeypatch.setattr(runtime_module, "SupabaseAutomationLease", Lease)
    monkeypatch.setattr(AgentRuntime, "discovery_worker",
                        lambda self, worker_id: Discovery())
    monkeypatch.setattr(AgentRuntime, "process_discovery_metadata",
                        lambda self, ids: {"processed_count": 0})
    settings = replace(load_settings(environ={}), automation_enabled=True,
                       discovery_enabled=True, discovery_worker_enabled=True)
    agent = AgentRuntime(SimpleNamespace(client=Client()), None, None, settings)
    off = agent.automation_runtime(Path("/unused"), worker_id="synthetic").run_once()
    assert "search_registration" not in off.results and transport.calls == []

    agent.search_registration_permissions = ((scope, rights),)
    runtime = agent.automation_runtime(Path("/unused"), worker_id="synthetic")
    dry = runtime.run_once(dry_run=True)
    assert "search_registration" not in dry.results and transport.calls == []
    shadow = runtime.run_once()
    assert shadow.status == "COMPLETED"
    assert shadow.results["search_registration"]["eligible_count"] == 1
    assert shadow.results["search_registration"]["published_count"] == 0
    assert tables.writes == 0 and URL in transport.calls
    assert transport.calls[0] == "https://official.example.org/robots.txt"

    agent.settings = replace(settings, publish_enabled=True)
    canary = runtime.run_once()
    assert canary.status == "COMPLETED"
    assert canary.results["search_registration"]["published_count"] == 1
    assert tables.writes == 1

    # A search result already pointing to the individual notice has no
    # depth-one Follow-up lead. The same run_once entry point must still
    # reach the fresh-page preview under the reviewed notice directory.
    result = replace(result, title="2027 AI Competition", original_url=URL,
                     canonical_url=URL)
    transport.pages[URL] = http_page(
        URL, "<h1>2027 AI Competition</h1>" + page.decode())
    agent.settings = replace(settings, publish_enabled=False)
    direct = runtime.run_once()
    assert direct.status == "COMPLETED", direct.errors
    assert direct.results["search_registration"]["eligible_count"] == 1
    assert direct.results["search_registration"]["published_count"] == 0
    assert tables.writes == 1

    # A direct individual result can also link to related notices. Follow-up
    # must not replace the original result with those depth-one leads.
    related = "https://official.example.org/notices/related-competition"
    transport.pages[URL] = http_page(
        URL, "<h1>2027 AI Competition</h1>" + page.decode() +
        f'<a href="{related}">Related competition application</a>')
    transport.pages[related] = http_page(related, "<p>Competition application</p>")
    with_related = runtime.run_once()
    assert with_related.status == "COMPLETED", with_related.errors
    assert related in transport.calls
    assert with_related.results["search_registration"]["eligible_count"] == 1
    assert with_related.results["search_registration"]["published_count"] == 0
    assert tables.writes == 1


def test_search_registration_holds_uncited_date():
    values = _inputs()
    values["facts"]["application_deadline"] = PublicNoticeFact(
        "2027-04-13", "", "2027-04-13")
    preview = preview_search_registration(**values)
    assert not preview.eligible
    assert "PAGE_FACT_EVIDENCE_MISSING" in preview.reasons


def test_search_registration_normalizes_explicit_offset_only():
    values = _inputs()
    values["facts"]["application_start"] = _fact("2027-04-02 09:00:00+09:00", "B003")
    values["facts"]["application_deadline"] = _fact("2027-04-13T18:00:00+09:00", "B004")
    values["official_page"] = replace(
        values["official_page"],
        recruitment_start_on=None, recruitment_deadline_on=None,
        recruitment_start_at=datetime.fromisoformat("2027-04-02T09:00:00+09:00"),
        recruitment_deadline_at=datetime.fromisoformat("2027-04-13T18:00:00+09:00"))
    preview = preview_search_registration(**values)
    assert preview.eligible
    assert preview.record["application_deadline_at"] == "2027-04-13T09:00:00+00:00"
    assert preview.evidence["application_deadline"]["precision"] == "EXACT_TIME"

    assert preview.evidence["application_start"]["source_literal"] == "2027-04-02T09:00:00+09:00"
    assert preview.evidence["application_start"]["source_text"] == "2027-04-02 09:00:00+09:00"
    values["facts"]["application_deadline"] = _fact("2027-04-13T18:00:00", "B004")
    held = preview_search_registration(**values)
    assert not held.eligible
    assert "APPLICATION_DEADLINE_AMBIGUOUS" in held.reasons


def test_search_registration_holds_expired_notice():
    values = _inputs()
    values["now"] = datetime(2027, 4, 14, tzinfo=timezone.utc)
    values["official_page"] = replace(values["official_page"], checked_at=values["now"])
    preview = preview_search_registration(**values)
    assert not preview.eligible
    assert "RECRUITMENT_NOT_CONFIRMED" in preview.reasons


def test_known_future_deadline_can_be_open_without_inventing_a_start_date():
    values = _inputs()
    del values["facts"]["application_start"]
    values["official_page"] = replace(
        values["official_page"], recruitment_start_on=None)
    preview = preview_search_registration(**values)
    assert preview.eligible and preview.record["status"] == "open"
    assert "application_start_at" not in preview.record
    assert preview.evidence["application_start"] == {"precision": "UNKNOWN"}

    values["now"] = datetime(2027, 4, 13, 0, tzinfo=timezone.utc)
    values["official_page"] = replace(values["official_page"], checked_at=values["now"])
    held = preview_search_registration(**values)
    assert not held.eligible and "RECRUITMENT_NOT_CONFIRMED" in held.reasons

    exact = _inputs()
    del exact["facts"]["application_start"]
    exact["facts"]["application_deadline"] = _fact(
        "2027-04-13T18:00:00+09:00", "B004")
    exact["official_page"] = replace(
        exact["official_page"], recruitment_start_on=None,
        recruitment_deadline_on=None,
        recruitment_deadline_at=datetime(2027, 4, 13, 18,
                                         tzinfo=timezone(timedelta(hours=9))))
    ready = preview_search_registration(**exact)
    assert ready.eligible and ready.record["status"] == "open"
    assert "application_start_at" not in ready.record


def test_category_event_window_is_required_for_conference():
    values = _inputs()
    values["facts"]["category"] = _fact("conference", "B006")
    values["category_slug"] = "conference"
    held = preview_search_registration(**values)
    assert not held.eligible
    assert "CATEGORY_EVENT_WINDOW_MISSING" in held.reasons

    values["facts"]["event_start"] = _fact("2027-04-14", "B007")
    values["facts"]["event_end"] = _fact("2027-04-15", "B008")
    values["official_page"] = replace(
        values["official_page"], event_start_on=date(2027, 4, 14),
        event_end_on=date(2027, 4, 15))
    ready = preview_search_registration(**values)
    assert ready.eligible
    assert ready.evidence["event_end"]["precision"] == "DATE_ONLY"

    values["facts"]["event_start"] = _fact("2027-04-14T09:00:00+09:00", "B007")
    values["facts"]["event_end"] = _fact("2027-04-15T18:00:00+09:00", "B008")
    values["official_page"] = replace(
        values["official_page"], event_start_on=None, event_end_on=None,
        event_start_at=datetime(2027, 4, 14, 9, tzinfo=timezone(timedelta(hours=9))),
        event_end_at=datetime(2027, 4, 15, 18, tzinfo=timezone(timedelta(hours=9))))
    exact = preview_search_registration(**values)
    assert exact.eligible
    assert exact.record["event_start_at"] == "2027-04-14T00:00:00+00:00"
    assert exact.evidence["event_end"]["precision"] == "EXACT_TIME"

    values["official_page"] = replace(values["official_page"],
                                       event_end_at=datetime(2027, 4, 15, 17,
                                                             tzinfo=timezone(timedelta(hours=9))))
    mismatch = preview_search_registration(**values)
    assert not mismatch.eligible
    assert "EVENT_END_EVIDENCE_MISMATCH" in mismatch.reasons


def test_reverse_date_window_is_held():
    values = _inputs()
    values["facts"]["application_start"] = _fact("2027-04-15", "B003")
    values["official_page"] = replace(
        values["official_page"], recruitment_start_on=date(2027, 4, 15))
    held = preview_search_registration(**values)
    assert not held.eligible
    assert "APPLICATION_START_AFTER_APPLICATION_DEADLINE" in held.reasons


def test_same_notice_on_another_url_uses_same_atomic_slug():
    first = _inputs()
    second = _inputs()
    other_url = "https://official.example.org/notices/ai-competition-2027-copy"
    second["search_result"] = replace(second["search_result"], original_url=other_url)
    second["official_page"] = replace(second["official_page"], source_url=other_url)
    second["facts"]["official_url"] = _fact(other_url, "L000")
    a = preview_search_registration(**first)
    b = preview_search_registration(**second)
    assert a.eligible and b.eligible
    assert a.record["slug"] == b.record["slug"]
    assert a.record["external_id"] != b.record["external_id"]


def test_public_extraction_replays_without_granting_approval():
    page = (b"<html><head><link rel='canonical' href='" + URL.encode() +
            b"'></head><body><p>Title: 2027 AI Competition</p>"
            b"<p>Application deadline: 2027-04-13</p></body></html>")
    facts = extract_labelled_public_facts(
        page=page, page_url=URL, fetched_at=NOW, now=NOW)
    assert facts["application_deadline"].value == "2027-04-13"
    assert facts["application_deadline"].evidence_reference
    assert not hasattr(facts["application_deadline"], "access_policy_digest")


def test_public_extraction_accepts_only_two_cell_labelled_rows():
    page = ("<html><body><table>"
            "<tr><th>제목</th><td>2027 AI 경진대회</td></tr>"
            "<tr><th>주최</th><td>공식 기관</td></tr>"
            "<tr><th>신청 마감</th><td>2027-04-13</td></tr>"
            "<tr><th>신청 방법</th><td>공식 페이지에서 신청</td></tr>"
            "<tr><th>분류</th><td>경진대회</td></tr>"
            "<tr><th>행사 시작</th><td>2027-05-01</td><td>수정 예정</td></tr>"
            "</table></body></html>").encode()
    facts = extract_labelled_public_facts(
        page=page, page_url=URL, fetched_at=NOW, now=NOW)
    assert {"title", "organizer", "application_deadline", "application_method",
            "category"} <= facts.keys()
    assert facts["application_deadline"].value == "2027-04-13"
    assert "event_start" not in facts


def test_public_extraction_does_not_treat_prose_pipe_as_table():
    page = b"<html><body><p>Title | Unreviewed claim</p></body></html>"
    facts = extract_labelled_public_facts(
        page=page, page_url=URL, fetched_at=NOW, now=NOW)
    assert "title" not in facts


def test_korean_dotted_deadline_replays_only_without_an_unresolved_clock():
    page = "<html><body><p>신청 마감: 2026.10.30.(금)</p></body></html>".encode()
    facts = extract_labelled_public_facts(
        page=page, page_url=URL, fetched_at=NOW, now=NOW)
    assert facts["application_deadline"].value == "2026-10-30"
    assert "2026.10.30." in facts["application_deadline"].quote

    wrong_weekday = extract_labelled_public_facts(
        page="<p>신청 마감: 2026.10.30.(목)</p>".encode(),
        page_url=URL, fetched_at=NOW, now=NOW)
    assert "application_deadline" not in wrong_weekday

    clock = page.replace("(금)".encode(), "(금) 18:00".encode())
    held = extract_labelled_public_facts(
        page=clock, page_url=URL, fetched_at=NOW, now=NOW)
    assert "application_deadline" not in held

    range_page = "<p>신청 마감: 2026.10.21. ~ 2026.10.30.</p>".encode()
    ambiguous = extract_labelled_public_facts(
        page=range_page, page_url=URL, fetched_at=NOW, now=NOW)
    assert "application_deadline" not in ambiguous

    malformed = extract_labelled_public_facts(
        page=b"<p>Application deadline: 2026.10.30.12</p>",
        page_url=URL, fetched_at=NOW, now=NOW)
    assert "application_deadline" not in malformed


def test_korean_dotted_dates_reach_the_existing_atomic_preview_gate():
    values = _inputs()
    page = ("<html><body>"
            "<p>제목: 2027 AI Competition</p>"
            "<p>주최: Official Organizer</p>"
            "<p>신청 시작: 2027.04.02.</p>"
            "<p>신청 마감: 2027.04.13.</p>"
            "<p>신청 방법: Apply on the official page</p>"
            "<p>분류: 경진대회</p>"
            "</body></html>").encode()
    preview = preview_fetched_search_result(
        search_result=values["search_result"], page=page, page_url=URL,
        official_page=values["official_page"],
        service_sources=values["service_sources"],
        service_categories=[{"id": str(values["category_id"]),
                             "slug": "competition", "is_active": True}],
        existing_rows=[], snapshot_complete=True,
        use_review=SearchUseReview(True, "reviewed", "terms:B001",
                                    NOW + timedelta(days=1)),
        fetched_at=NOW, now=NOW)
    assert preview.eligible, preview.reasons
    assert preview.evidence["application_deadline"]["source_date"] == "2027-04-13"


def test_explicit_korean_kst_clocks_reach_atomic_preview_without_timezone_guessing():
    values = _inputs()
    values["official_page"] = replace(
        values["official_page"], recruitment_start_on=None,
        recruitment_deadline_on=None,
        recruitment_start_at=datetime.fromisoformat("2027-04-02T09:00:00+09:00"),
        recruitment_deadline_at=datetime.fromisoformat("2027-04-13T18:00:00+09:00"))
    page = ("<html><body>"
            "<p>Title: 2027 AI Competition</p>"
            "<p>Organizer: Official Organizer</p>"
            "<p>Application starts: 2027.04.02. 09:00 KST</p>"
            "<p>Application deadline: 2027.04.13. 18:00 KST</p>"
            "<p>Application method: Apply on the official page</p>"
            "<p>Category: competition</p>"
            "</body></html>").encode()
    common = dict(search_result=values["search_result"], page_url=URL,
                  official_page=values["official_page"],
                  service_sources=values["service_sources"],
                  service_categories=[{"id": str(values["category_id"]),
                                       "slug": "competition", "is_active": True}],
                  existing_rows=[], snapshot_complete=True,
                  use_review=SearchUseReview(True, "reviewed", "terms:B001",
                                              NOW + timedelta(days=1)),
                  fetched_at=NOW, now=NOW)
    preview = preview_fetched_search_result(page=page, **common)
    assert preview.eligible, preview.reasons
    assert preview.record["application_deadline_at"] == "2027-04-13T09:00:00+00:00"
    assert preview.evidence["application_deadline"]["precision"] == "EXACT_TIME"

    suffix = page.replace(b"2027.04.13. 18:00 KST",
                          "2027.04.13. 18:00 KST까지".encode())
    assert preview_fetched_search_result(page=suffix, **common).eligible

    for literal in ("2027.04.13. 18:00", "2027.04.13. 18:00 KST, 19:00",
                    "2027.04.13.(금) 18:00 KST"):
        changed = page.replace(b"2027.04.13. 18:00 KST", literal.encode())
        held = preview_fetched_search_result(page=changed, **common)
        assert not held.eligible
        assert "REQUIRED_PAGE_FACT_MISSING" in held.reasons


def test_search_result_to_atomic_preview_uses_page_not_snippet():
    values = _inputs()
    page = (b"<html><head><link rel='canonical' href='" + URL.encode() +
            b"'></head><body>"
            b"<p>Title: 2027 AI Competition</p>"
            b"<p>Organizer: Official Organizer</p>"
            b"<p>Application starts: 2027-04-02</p>"
            b"<p>Application deadline: 2027-04-13</p>"
            b"<p>Application method: Apply on the official page</p>"
            b"<p>Category: competition</p>"
            b"</body></html>")
    preview = preview_fetched_search_result(
        search_result=values["search_result"], page=page, page_url=URL,
        official_page=values["official_page"],
        service_sources=values["service_sources"],
        service_categories=[{"id": str(values["category_id"]),
                             "slug": "competition", "is_active": True}],
        existing_rows=[], snapshot_complete=True,
        use_review=SearchUseReview(True, "synthetic", "robots-and-terms:B007",
                                   NOW+timedelta(days=1)),
        fetched_at=NOW, now=NOW)
    assert preview.eligible, preview.reasons
    assert preview.record["title"] == "2027 AI Competition"
    assert "untrusted search snippet" not in str(preview.record)


def test_reviewed_scope_requires_current_pinned_documents_and_display_rights():
    officiality = b"<html><body><p>Official organizer notice</p></body></html>"
    terms = b"<html><body><p>Reviewed permission evidence</p></body></html>"
    scope = ApprovalScope(
        "synthetic", "https://official.example.org", "/notices/",
        "fixture-reviewer", NOW-timedelta(days=1), NOW+timedelta(days=1),
        "https://official.example.org/officiality",
        hashlib.sha256(officiality).hexdigest(),
        "https://official.example.org/terms", hashlib.sha256(terms).hexdigest(),
        "explicit_license")
    snapshot = {name: {"status": "ALLOWED", "evidence_reference": "B001"}
                for name in ("public_read", "automated_access", "fact_extract",
                             "fact_store", "fact_display")}
    snapshot.update({name: {"status": "UNVERIFIED"}
                     for name in ("html_store", "html_redistribute", "attachment_collect")})
    rights = MetadataUsageRights.from_snapshot(snapshot)
    inputs = dict(scope=scope, rights=rights, target_url=URL,
                  officiality_document=officiality, terms_document=terms,
                  checked_at=NOW, now=NOW)
    allowed = verify_scoped_publication_use(**inputs)
    assert allowed.permitted and scope.terms_sha256 in allowed.evidence_ref
    assert not verify_scoped_publication_use(
        **{**inputs, "terms_document": terms+b"changed"}).permitted
    assert not verify_scoped_publication_use(
        **{**inputs, "target_url": "https://official.example.org/unreviewed"}).permitted
    assert not verify_scoped_publication_use(
        **{**inputs, "now": NOW+timedelta(days=2)}).permitted
    denied = {**snapshot, "fact_display": {"status": "DENIED"}}
    assert not verify_scoped_publication_use(
        **{**inputs, "rights": MetadataUsageRights.from_snapshot(denied)}).permitted
    missing_ref = {**snapshot, "fact_display": {"status": "ALLOWED",
                                                  "evidence_reference": "B999"}}
    assert not verify_scoped_publication_use(
        **{**inputs, "rights": MetadataUsageRights.from_snapshot(missing_ref)}).permitted


def test_scoped_page_handoff_holds_without_display_rights():
    values = _inputs()
    page = (b"<html><head><link rel='canonical' href='" + URL.encode() +
            b"'></head><body><p>Title: 2027 AI Competition</p>"
            b"<p>Organizer: Official Organizer</p>"
            b"<p>Application starts: 2027-04-02</p>"
            b"<p>Application deadline: 2027-04-13</p>"
            b"<p>Application method: Apply on the official page</p>"
            b"<p>Category: competition</p></body></html>")
    officiality = b"<p>Officiality evidence</p>"
    terms = b"<p>Permission evidence</p>"
    scope = ApprovalScope(
        "synthetic", "https://official.example.org", "/notices/",
        "fixture-reviewer", NOW-timedelta(days=1), NOW+timedelta(days=1),
        "https://official.example.org/officiality",
        hashlib.sha256(officiality).hexdigest(),
        "https://official.example.org/terms", hashlib.sha256(terms).hexdigest(),
        "explicit_license")
    common = dict(search_result=values["search_result"], page=page, page_url=URL,
                  official_page=values["official_page"], scope=scope,
                  officiality_document=officiality, terms_document=terms,
                  permission_checked_at=NOW, service_sources=values["service_sources"],
                  service_categories=[{"id": str(values["category_id"]),
                                       "slug": "competition", "is_active": True}],
                  existing_rows=[], snapshot_complete=True, fetched_at=NOW, now=NOW)
    held = preview_scoped_fetched_search_result(**common, rights=None)
    assert not held.eligible and held.record is None
    assert "METADATA_DISPLAY_RIGHTS_UNCONFIRMED" in held.reasons
    snapshot = {name: {"status": "ALLOWED", "evidence_reference": "B001"}
                for name in ("public_read", "automated_access", "fact_extract",
                             "fact_store", "fact_display")}
    snapshot.update({name: {"status": "UNVERIFIED"}
                     for name in ("html_store", "html_redistribute", "attachment_collect")})
    permitted = preview_scoped_fetched_search_result(
        **common, rights=MetadataUsageRights.from_snapshot(snapshot))
    assert permitted.eligible
    assert permitted.permission_valid_until == scope.expires_at


def test_listing_requires_an_individual_followup_lead_before_registration():
    values = _inputs()
    listing = "https://official.example.org/notices/"
    result = replace(values["search_result"], original_url=listing,
                     canonical_url=listing)
    page = (b"<html><head><link rel='canonical' href='" + URL.encode() +
            b"'></head><body><p>Title: 2027 AI Competition</p>"
            b"<p>Organizer: Official Organizer</p>"
            b"<p>Application starts: 2027-04-02</p>"
            b"<p>Application deadline: 2027-04-13</p>"
            b"<p>Application method: Apply on the official page</p>"
            b"<p>Category: competition</p></body></html>")
    lead = FollowupLead(URL, listing, 1, "Apply to AI competition",
                        "INDIVIDUAL_NOTICE", None, None, "UNKNOWN", "UNKNOWN",
                        None, "checked-followup", page_sha256=hashlib.sha256(page).hexdigest())
    officiality = b"<p>Officiality evidence</p>"
    terms = b"<p>Permission evidence</p>"
    scope = ApprovalScope(
        "synthetic", "https://official.example.org", "/notices/",
        "fixture-reviewer", NOW-timedelta(days=1), NOW+timedelta(days=1),
        "https://official.example.org/officiality",
        hashlib.sha256(officiality).hexdigest(),
        "https://official.example.org/terms", hashlib.sha256(terms).hexdigest(),
        "explicit_license")
    snapshot = {name: {"status": "ALLOWED", "evidence_reference": "B001"}
                for name in ("public_read", "automated_access", "fact_extract",
                             "fact_store", "fact_display")}
    snapshot.update({name: {"status": "UNVERIFIED"}
                     for name in ("html_store", "html_redistribute", "attachment_collect")})
    common = dict(search_result=result, page=page, scope=scope,
                  rights=MetadataUsageRights.from_snapshot(snapshot),
                  officiality_document=officiality, terms_document=terms,
                  permission_checked_at=NOW,
                  service_sources=values["service_sources"],
                  service_categories=[{"id": str(values["category_id"]),
                                       "slug": "competition", "is_active": True}],
                  existing_rows=[], snapshot_complete=True, fetched_at=NOW, now=NOW)
    ready = preview_scoped_followup_lead(**common, lead=lead)
    assert ready.eligible and ready.record["official_url"] == URL
    held = preview_scoped_followup_lead(
        **common, lead=replace(lead, page_type="REVIEW_REQUIRED"))
    assert not held.eligible and "FOLLOWUP_INDIVIDUAL_UNVERIFIED" in held.reasons
    changed = preview_scoped_followup_lead(
        **{**common, "page": page.replace(b"2027-04-13", b"2027-04-14")}, lead=lead)
    assert not changed.eligible and "FOLLOWUP_PAGE_CHANGED" in changed.reasons
    unbound = preview_scoped_followup_lead(
        **common, lead=replace(lead, page_sha256=None))
    assert not unbound.eligible and "FOLLOWUP_PAGE_CHANGED" in unbound.reasons


def test_followup_can_replay_a_future_deadline_without_a_start_date():
    values = _inputs()
    listing = "https://official.example.org/notices/"
    result = replace(values["search_result"], original_url=listing,
                     canonical_url=listing)
    page = (b"<html><head><link rel='canonical' href='" + URL.encode() +
            b"'></head><body><p>Title: 2027 AI Competition</p>"
            b"<p>Organizer: Official Organizer</p>"
            b"<p>Application deadline: 2027-04-13</p>"
            b"<p>Event starts: 2027-04-16T09:00:00+09:00</p>"
            b"<p>Event ends: 2027-04-16T18:00:00+09:00</p>"
            b"<p>Application method: Apply on the official page</p>"
            b"<p>Category: competition</p></body></html>")
    lead = FollowupLead(URL, listing, 1, "Apply to AI competition",
                        "INDIVIDUAL_NOTICE", None, None, "UNKNOWN", "UNKNOWN",
                        None, "checked-followup",
                        page_sha256=hashlib.sha256(page).hexdigest())
    officiality = b"<p>Officiality evidence</p>"
    terms = b"<p>Permission evidence</p>"
    scope = ApprovalScope(
        "synthetic", "https://official.example.org", "/notices/",
        "fixture-reviewer", NOW-timedelta(days=1), NOW+timedelta(days=1),
        "https://official.example.org/officiality",
        hashlib.sha256(officiality).hexdigest(),
        "https://official.example.org/terms", hashlib.sha256(terms).hexdigest(),
        "explicit_license")
    snapshot = {name: {"status": "ALLOWED", "evidence_reference": "B001"}
                for name in ("public_read", "automated_access", "fact_extract",
                             "fact_store", "fact_display")}
    snapshot.update({name: {"status": "UNVERIFIED"}
                     for name in ("html_store", "html_redistribute", "attachment_collect")})
    common = dict(search_result=result, lead=lead, page=page, scope=scope,
                  rights=MetadataUsageRights.from_snapshot(snapshot),
                  officiality_document=officiality, terms_document=terms,
                  permission_checked_at=NOW,
                  service_sources=values["service_sources"],
                  service_categories=[{"id": str(values["category_id"]),
                                       "slug": "competition", "is_active": True}],
                  existing_rows=[], snapshot_complete=True, fetched_at=NOW, now=NOW)
    ready = preview_scoped_followup_lead(**common)
    assert ready.eligible and ready.record["status"] == "open"
    assert "application_start_at" not in ready.record
    assert ready.evidence["application_start"] == {"precision": "UNKNOWN"}
    assert ready.record["event_start_at"] == "2027-04-16T00:00:00+00:00"
    assert ready.evidence["event_end"]["precision"] == "EXACT_TIME"

    changed = page.replace(b"2027-04-13", b"2027-04-14")
    held = preview_scoped_followup_lead(**{**common, "page": changed})
    assert not held.eligible and "FOLLOWUP_PAGE_CHANGED" in held.reasons


def test_existing_robots_aware_followup_can_feed_scoped_preview_offline():
    values = _inputs()
    listing = "https://official.example.org/notices/"
    result = replace(values["search_result"], original_url=listing,
                     canonical_url=listing)
    html = ("<head><link rel='canonical' href='" + URL +
            "'></head><p>Title: 2027 AI Competition</p>"
            "<p>Organizer: Official Organizer</p>"
            "<p>Application starts: 2027-04-02</p>"
            "<p>Application deadline: 2027-04-13</p>"
            "<p>Application method: Apply on the official page</p>"
            "<p>Category: competition</p><p>Apply now to AI competition</p>")
    officiality = b"<p>Officiality evidence</p>"
    terms = b"<p>Permission evidence</p>"
    scope = ApprovalScope(
        "synthetic", "https://official.example.org", "/notices/",
        "fixture-reviewer", NOW-timedelta(days=1), NOW+timedelta(days=1),
        "https://official.example.org/officiality",
        hashlib.sha256(officiality).hexdigest(),
        "https://official.example.org/terms", hashlib.sha256(terms).hexdigest(),
        "explicit_license")
    transport = FakeTransport({
        scope.officiality_evidence_url: http_page(scope.officiality_evidence_url,
                                                  officiality.decode()),
        scope.terms_url: http_page(scope.terms_url, terms.decode()),
        listing: http_page(listing, '<a href="'+URL+'">AI competition apply</a>'),
        URL: http_page(URL, html),
    })
    leads = OfficialFollowupExplorer(transport=transport).investigate(
        result, scope=scope, now=NOW).leads
    assert len(leads) == 1 and leads[0].page_type == "INDIVIDUAL_NOTICE"
    snapshot = {name: {"status": "ALLOWED", "evidence_reference": "B001"}
                for name in ("public_read", "automated_access", "fact_extract",
                             "fact_store", "fact_display")}
    snapshot.update({name: {"status": "UNVERIFIED"}
                     for name in ("html_store", "html_redistribute", "attachment_collect")})
    preview = preview_scoped_followup_lead(
        search_result=result, lead=leads[0], page=transport.pages[URL].content,
        scope=scope, rights=MetadataUsageRights.from_snapshot(snapshot),
        officiality_document=officiality, terms_document=terms,
        permission_checked_at=NOW, service_sources=values["service_sources"],
        service_categories=[{"id": str(values["category_id"]),
                             "slug": "competition", "is_active": True}],
        existing_rows=[], snapshot_complete=True, fetched_at=NOW, now=NOW)
    assert preview.eligible and preview.record["official_url"] == URL
    assert "https://official.example.org/robots.txt" in transport.calls


def test_connect_search_registration_requires_explicit_reviewed_scope_and_rights(monkeypatch):
    from agent_collect import runtime as runtime_module

    monkeypatch.setattr(runtime_module, "create_agent_client", lambda environ: object())
    scope = {
        "scope_id": "fixture", "origin": "https://official.example.org",
        "path_prefix": "/notices/", "reviewed_by": "fixture-reviewer",
        "reviewed_at": "2027-04-01T00:00:00+00:00",
        "expires_at": "2027-04-02T00:00:00+00:00",
        "officiality_evidence_url": "https://official.example.org/officiality",
        "officiality_sha256": "a" * 64,
        "terms_url": "https://official.example.org/terms",
        "terms_sha256": "b" * 64, "permission_basis": "explicit_license",
    }
    rights = {name: {"status": "ALLOWED", "evidence_reference": "B001"}
              for name in ("public_read", "automated_access", "fact_extract",
                           "fact_store", "fact_display")}
    rights.update({name: {"status": "UNVERIFIED"}
                   for name in ("html_store", "html_redistribute", "attachment_collect")})
    assert AgentRuntime.connect({}).search_registration_permissions == ()
    payload = json.dumps([{"scope": scope, "usage_rights": rights}])
    loaded = AgentRuntime.connect({"AGENT_SEARCH_REGISTRATION_PERMISSIONS_JSON": payload})
    assert len(loaded.search_registration_permissions) == 1
    assert loaded.search_registration_permissions[0][0].scope_id == "fixture"
    assert loaded.search_registration_permissions[0][1].metadata_permitted()

    import pytest
    for invalid in ("[]", "{}", "bad json", json.dumps([{"scope": scope}]),
                    json.dumps([{"scope": scope, "usage_rights": {}}]),
                    json.dumps([{"scope": scope, "usage_rights": rights}] * 2)):
        with pytest.raises(ValueError):
            AgentRuntime.connect({"AGENT_SEARCH_REGISTRATION_PERMISSIONS_JSON": invalid})
    rights["fact_display"] = {"status": "UNVERIFIED"}
    with pytest.raises(ValueError):
        AgentRuntime.connect({"AGENT_SEARCH_REGISTRATION_PERMISSIONS_JSON":
                              json.dumps([{"scope": scope, "usage_rights": rights}])})
