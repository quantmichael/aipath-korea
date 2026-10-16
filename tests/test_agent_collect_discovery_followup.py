"""Read-only official follow-up boundaries and evidence hints."""

import unittest
from datetime import datetime, timezone
from uuid import uuid4

from agent_collect.collectors.transport import HttpResponse, SafeHttpTransport
from agent_collect.collectors.security import CollectionTransportError
from agent_collect.discovery.followup import OfficialFollowupExplorer, ReviewedOfficialRoot
from agent_collect.discovery.models import SearchResult, SearchResultStatus
from agent_collect.discovery.search_only import _NoCandidateWrites
from agent_collect.discovery.quality import SearchQualityEvaluator
from agent_collect.repositories.discovery import InMemoryDiscoveryRepository


ORIGIN = "https://official.example.org"
NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


class FakeTransport:
    def __init__(self, pages, robots=b"User-agent: *\nAllow: /\n"):
        self.pages = pages
        self.robots = robots
        self.calls = []

    def fetch(self, url, *, headers, timeout, max_bytes, max_redirects,
              return_redirect_response=False):
        self.calls.append(url)
        assert max_redirects == 0
        assert return_redirect_response
        assert max_bytes <= 1048576
        if url == ORIGIN + "/robots.txt":
            return HttpResponse(200, {}, self.robots, url)
        return self.pages[url]


def page(url, body, *, status=200, headers=None):
    return HttpResponse(status, headers or {"content-type": "text/html"},
                        body.encode(), url)


def result(url=ORIGIN + "/"):
    return SearchResult(uuid4(), uuid4(), "tavily", 1, "Official AI expo", url, "",
                        NOW, status=SearchResultStatus.CANDIDATE)


class OfficialFollowupTests(unittest.TestCase):
    def setUp(self):
        self.reviewed = ReviewedOfficialRoot(ORIGIN, "human:reviewer", "official organizer site")

    def test_transport_nofollow_mode_exposes_redirect_without_requesting_target(self):
        calls = []
        def resolver(host, port, **kwargs):
            return [(None, None, None, None, ("93.184.215.14", port))]
        def dialer(scheme, host, port, address, path, headers, timeout, max_bytes):
            calls.append(host)
            return 302, {"location": "https://other.example.org/apply"}, b""
        transport = SafeHttpTransport(resolver=resolver, dialer=dialer)
        response = transport.fetch(ORIGIN + "/", headers={}, timeout=1,
                                   max_bytes=100, max_redirects=0,
                                   return_redirect_response=True)
        self.assertEqual(response.status, 302)
        self.assertEqual(calls, ["official.example.org"])
        with self.assertRaises(CollectionTransportError):
            transport.fetch(ORIGIN + "/", headers={}, timeout=1,
                            max_bytes=100, max_redirects=0)

    def test_homepage_to_listing_to_individual_is_review_only(self):
        transport = FakeTransport({
            ORIGIN + "/": page(ORIGIN + "/", '<a href="/notices">모집 공고</a>'),
            ORIGIN + "/notices": page(ORIGIN + "/notices",
                                       '<a href="/notices/ai-2027">AI 참가 신청</a>'),
            ORIGIN + "/notices/ai-2027": page(ORIGIN + "/notices/ai-2027",
                '<h1>AI 참가 신청</h1><p>신청 기간: 2027-01-04 ~ 2027-04-13</p>'),
        })
        outcome = OfficialFollowupExplorer(transport=transport).explore(
            result(), reviewed=self.reviewed, now=NOW)
        self.assertEqual(len(outcome.visited_urls), 3)
        self.assertEqual(outcome.leads[-1].depth, 2)
        self.assertEqual(outcome.leads[-1].page_type, "INDIVIDUAL_NOTICE")
        self.assertEqual(outcome.leads[-1].temporal, "UPCOMING")
        self.assertEqual(outcome.leads[-1].time_precision, "DATE_ONLY")
        self.assertEqual(outcome.leads[-1].discovery_lead_days, 181)
        self.assertEqual(outcome.audit_record()["followup_verified"], 0)
        self.assertEqual(transport.calls[0], ORIGIN + "/robots.txt")

    def test_robots_denial_blocks_before_page_request(self):
        transport = FakeTransport({}, b"User-agent: *\nDisallow: /\n")
        outcome = OfficialFollowupExplorer(transport=transport).explore(
            result(), reviewed=self.reviewed, now=NOW)
        self.assertEqual(transport.calls, [ORIGIN + "/robots.txt"])
        self.assertEqual(outcome.blocked, ("robots_blocked",))
        self.assertEqual(outcome.audit_record()["followup_verified"], 0)

    def test_external_and_private_links_never_requested(self):
        root = ('<a href="https://other.example.org/apply">AI 신청</a>'
                '<a href="http://127.0.0.1/apply">AI 신청</a>'
                '<a href="/login/apply">AI 신청</a>'
                '<a href="/참가기업-로그인">AI 신청</a>')
        transport = FakeTransport({ORIGIN + "/": page(ORIGIN + "/", root)})
        outcome = OfficialFollowupExplorer(transport=transport).explore(
            result(), reviewed=self.reviewed, now=NOW)
        self.assertEqual(len(outcome.visited_urls), 1)
        self.assertEqual(len(transport.calls), 2)

    def test_korean_path_and_percent_case_share_one_identity(self):
        from agent_collect.discovery.followup import _request_url
        upper = _request_url(ORIGIN + "/온라인-사전등록")
        lower = upper.lower().replace("https://", "https://")
        pages = {ORIGIN + "/": page(ORIGIN + "/",
                 f'<a href="{upper}">AI 사전등록</a><a href="{lower}">AI 사전등록</a>'),
                 upper: page(upper, '<h1>AI 사전등록</h1>')}
        outcome = OfficialFollowupExplorer(transport=FakeTransport(pages)).explore(
            result(), reviewed=self.reviewed, now=NOW)
        self.assertEqual(len(outcome.visited_urls), 2)

    def test_redirect_rechecked_and_cross_origin_denied(self):
        transport = FakeTransport({
            ORIGIN + "/": page(ORIGIN + "/", "", status=302,
                               headers={"location": "https://other.example.org/apply"}),
        })
        outcome = OfficialFollowupExplorer(transport=transport).explore(
            result(), reviewed=self.reviewed, now=NOW)
        self.assertIn("outside_reviewed_origin", outcome.blocked)
        self.assertEqual(len(transport.calls), 2)

    def test_depth_link_cap_and_loop(self):
        links = "".join(f'<a href="/apply/{n}">AI 신청 {n}</a>' for n in range(15))
        pages = {ORIGIN + "/": page(ORIGIN + "/", links)}
        for n in range(15):
            url = ORIGIN + f"/apply/{n}"
            pages[url] = page(url, '<a href="/">AI 신청</a>')
        transport = FakeTransport(pages)
        outcome = OfficialFollowupExplorer(transport=transport).explore(
            result(), reviewed=self.reviewed, now=NOW)
        self.assertEqual(len(outcome.visited_urls), 11)
        self.assertEqual(len(set(outcome.visited_urls)), 11)
        self.assertEqual(max(row.depth for row in outcome.leads), 1)
        with self.assertRaises(ValueError):
            OfficialFollowupExplorer(transport=transport).explore(
                result(), reviewed=self.reviewed, now=NOW, max_links=11)

    def test_missing_or_event_dates_do_not_become_recruitment(self):
        url = ORIGIN + "/apply"
        pages = {ORIGIN + "/": page(ORIGIN + "/", '<a href="/apply">AI 신청</a>'),
                 url: page(url, '<h1>AI 신청</h1><p>행사 기간: 2026-10-14 ~ 2026-10-16</p>')}
        outcome = OfficialFollowupExplorer(transport=FakeTransport(pages)).explore(
            result(), reviewed=self.reviewed, now=NOW)
        self.assertIsNone(outcome.leads[0].recruitment_deadline)
        self.assertEqual(outcome.leads[0].temporal, "UNKNOWN")
        self.assertEqual((outcome.leads[0].event_start, outcome.leads[0].event_end),
                         ("2026-10-14", "2026-10-16"))
        self.assertEqual(outcome.audit_record()["event_lead_days"], 0)

    def test_date_only_boundary_and_expired_are_not_active(self):
        url = ORIGIN + "/apply"
        pages = {ORIGIN + "/": page(ORIGIN + "/", '<a href="/apply">AI 신청</a>'),
                 url: page(url, '<h1>AI 신청</h1><p>신청 기간: 2026-10-14 ~ 2026-10-16</p>')}
        lead = OfficialFollowupExplorer(transport=FakeTransport(pages)).explore(
            result(), reviewed=self.reviewed, now=NOW).leads[0]
        self.assertEqual(lead.temporal, "UNKNOWN")
        later = datetime(2026, 10, 17, tzinfo=timezone.utc)
        lead = OfficialFollowupExplorer(transport=FakeTransport(pages)).explore(
            result(), reviewed=self.reviewed, now=later).leads[0]
        self.assertEqual(lead.temporal, "EXPIRED")

    def test_numbered_registration_phases_use_final_deadline(self):
        url = ORIGIN + "/apply"
        pages = {ORIGIN + "/": page(ORIGIN + "/", '<a href="/apply">AI 사전등록</a>'),
                 url: page(url, '<h1>AI 사전등록</h1>'
                     '<p>사전등록 기간 : - 1차 2027년 1월 4일 ~ 2027년 1월 31일</p>'
                     '<p>- 2차 2027년 2월 1일 ~ 2027년 4월 13일</p>')}
        lead = OfficialFollowupExplorer(transport=FakeTransport(pages)).explore(
            result(), reviewed=self.reviewed, now=NOW).leads[0]
        self.assertEqual((lead.recruitment_start, lead.recruitment_deadline),
                         ("2027-01-04", "2027-04-13"))
        self.assertEqual(lead.temporal, "UPCOMING")

    def test_kst_boundary_is_used_for_date_only_windows(self):
        url = ORIGIN + "/apply"
        pages = {ORIGIN + "/": page(ORIGIN + "/", '<a href="/apply">AI 신청</a>'),
                 url: page(url, '<h1>AI 신청</h1><p>신청 기간: 2026-10-14 ~ 2026-10-16</p>')}
        before = datetime(2026, 10, 13, 14, tzinfo=timezone.utc)
        boundary = datetime(2026, 10, 13, 16, tzinfo=timezone.utc)
        self.assertEqual(OfficialFollowupExplorer(transport=FakeTransport(pages)).explore(
            result(), reviewed=self.reviewed, now=before).leads[0].temporal, "UPCOMING")
        self.assertEqual(OfficialFollowupExplorer(transport=FakeTransport(pages)).explore(
            result(), reviewed=self.reviewed, now=boundary).leads[0].temporal, "UNKNOWN")

    def test_opt_in_runner_only_creates_in_memory_review_candidate(self):
        url = ORIGIN + "/apply"
        pages = {ORIGIN + "/": page(ORIGIN + "/", '<a href="/apply">AI 신청</a>'),
                 url: page(url, '<h1>AI 신청</h1><p>신청 기간: 2027-01-04 ~ 2027-04-13</p>')}
        repository = InMemoryDiscoveryRepository()
        wrapped = _NoCandidateWrites(repository, SearchQualityEvaluator(),
             OfficialFollowupExplorer(transport=FakeTransport(pages)), (self.reviewed,))
        row = result()
        from agent_collect.discovery.models import DiscoveryQuery
        repository.save_query(DiscoveryQuery(row.query_id, "official AI application", "tavily", NOW))
        wrapped.save_result(row)
        self.assertEqual(len(wrapped.followup_candidates), 1)
        candidate = next(iter(wrapped.followup_candidates.values()))
        self.assertEqual(candidate.status.value, "REVIEW_REQUIRED")
        self.assertFalse(candidate.provenance["source_approval_eligible"])
        self.assertEqual(repository.candidates, {})


if __name__ == "__main__":
    unittest.main()
