"""Offline proof of the opt-in Discovery -> audited Source activation path."""

import hashlib
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from uuid import uuid4

from agent_collect.collectors.transport import HttpResponse
from agent_collect.collectors.planner import CollectionPlanner
from agent_collect.collectors.runner import CollectionRunner
from agent_collect.config.settings import load_settings
from agent_collect.discovery.auto_approval import (
    AUTO_REVIEWER, ApprovalScope, AutoApprovalAssessor, AutoApprovalExecutor,
    load_approval_scopes,
)
from agent_collect.discovery.evaluation import RuleBasedEvaluator
from agent_collect.discovery.models import DiscoveryQuery, SourceCandidateStatus
from agent_collect.discovery.providers import FakeSearchProvider, SearchHit
from agent_collect.discovery.runner import DiscoveryRunner, PromotionGate
from agent_collect.registry.approval import SourceApprovalService, SourceDecision, source_is_collectible
from agent_collect.registry.service import SourceRegistryService
from agent_collect.repositories.discovery import InMemoryDiscoveryRepository
from agent_collect.repositories.memory import (
    InMemoryCollectionRunRepository, InMemoryRawDocumentRepository,
    InMemorySourceRepository,
)
from agent_collect.repositories.source_review import InMemorySourceReviewRepository
from agent_collect.schemas.status import SourceStatus
from agent_collect.schemas.models import CollectionMethod, SourceType
from agent_collect.scheduling.policy import CollectionSchedulePolicy
from agent_collect.scheduling.repository import InMemoryCollectionScheduleRepository
from agent_collect.scheduling.worker import CollectionWorker
from agent_collect.storage.raw import LocalRawContentStorage


NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
ORIGIN = "https://official.example.org"
URL = ORIGIN + "/calls/ai-conference"
TERMS_URL = ORIGIN + "/terms"
TERMS = b"Explicitly permits automated private collection of public conference calls."
OFFICIALITY = b"Official AI Conference organization website."


class FakeTransport:
    def __init__(self, *, robots=b"User-agent: *\nAllow: /\n", terms=TERMS,
                 terms_status=200, page=b"<h1>AI Conference</h1>",
                 officiality=OFFICIALITY):
        self.robots, self.terms, self.terms_status = robots, terms, terms_status
        self.page, self.officiality = page, officiality
        self.calls = []

    def fetch(self, url, *, headers, timeout, max_bytes, max_redirects):
        self.calls.append(url)
        assert max_redirects == 0
        if url.endswith("/robots.txt"):
            return HttpResponse(200, {}, self.robots, url)
        if url == URL:
            return HttpResponse(200, {"content-type": "text/html"}, self.page, url)
        if url == ORIGIN + "/about":
            return HttpResponse(200, {}, self.officiality, url)
        return HttpResponse(self.terms_status, {}, self.terms, url)


def scope(**overrides):
    values = dict(scope_id="official-calls-v1", origin=ORIGIN,
                  path_prefix="/calls/", reviewed_by="Human Reviewer",
                  reviewed_at=NOW - timedelta(days=1),
                  expires_at=NOW + timedelta(days=30),
                  officiality_evidence_url=ORIGIN + "/about",
                  officiality_sha256=hashlib.sha256(OFFICIALITY).hexdigest(),
                  terms_url=TERMS_URL, terms_sha256=hashlib.sha256(TERMS).hexdigest(),
                  permission_basis="explicit_license")
    values.update(overrides)
    return ApprovalScope(**values)


def make_runner(*, transport=None, scopes=None, failing_review=False, official_hint=False):
    sources = InMemorySourceRepository()
    registry = SourceRegistryService(sources)
    discovery = InMemoryDiscoveryRepository()

    class FailingReview(InMemorySourceReviewRepository):
        def transition(self, command):
            if command.decision is SourceDecision.APPROVED:
                raise RuntimeError("approval RPC failed")
            return super().transition(command)

    review_repo = (FailingReview(sources) if failing_review
                   else InMemorySourceReviewRepository(sources))
    assessor = AutoApprovalAssessor(scopes or (scope(),), user_agent="AIPATH-AgentCollect/0.1",
                                    transport=transport or FakeTransport(), clock=lambda: NOW)
    runner = DiscoveryRunner(
        provider=FakeSearchProvider([SearchHit("AI Conference", URL, "AI conference call")]),
        repository=discovery, registry=registry,
        evaluator=RuleBasedEvaluator(official_domain_hints=("example.org",) if official_hint else ()),
        gate=PromotionGate(registry, threshold=0.9), auto_assessor=assessor,
        auto_executor=AutoApprovalExecutor(SourceApprovalService(review_repo)),
    )
    return runner, sources, discovery, review_repo


class AutoApprovalTests(unittest.TestCase):
    def test_broad_official_results_are_candidates_before_permission(self):
        titles = (
            "AI education course", "AI competition", "AI hackathon",
            "AI conference", "AI research program", "AI government grant",
            "AI recruitment application",
        )
        for title in titles:
            with self.subTest(title=title):
                runner, sources, discovery, _ = make_runner()
                runner.provider.hits = [SearchHit(title, "https://new-official.example.net/calls/notice", title)]
                result = runner.run(DiscoveryQuery(uuid4(), title, "fake", NOW))
                self.assertEqual(result.query.candidate_count, 1)
                self.assertEqual(result.auto_approved_count, 0)
                self.assertEqual(result.review_required_count, 1)
                self.assertFalse(sources._rows)
                row = next(iter(discovery.candidates.values()))
                self.assertEqual(row.provenance["auto_approval"]["reason"],
                                 "scope_not_unique_or_not_reviewed")

    def test_schedule_confirmation_collection_raw_and_permission_revocation(self):
        runner, sources, discovery, reviews = make_runner()
        schedules = InMemoryCollectionScheduleRepository(sources, clock=lambda: NOW + timedelta(minutes=1))
        original_transition = reviews.transition

        def transition(command):
            source = original_transition(command)
            if command.decision is SourceDecision.ACTIVATED:
                schedules.seed(source, next_collection_at=NOW)
            return source

        reviews.transition = transition
        runner.auto_executor.schedule_verifier = lambda source: schedules.get(source.id) is not None
        result = runner.run(DiscoveryQuery(uuid4(), "AI conference", "fake", NOW))
        self.assertEqual((result.auto_approved_count, result.collection_scheduled_count), (1, 1))
        source = next(iter(sources._rows.values()))
        runs = InMemoryCollectionRunRepository()
        documents = InMemoryRawDocumentRepository()
        permission_transport = FakeTransport()

        class CollectionTransport:
            page = b"<html><title>AI Conference</title><body>AI Conference call</body></html>"

            def fetch(self, url, *, headers, timeout, max_bytes, max_redirects):
                return HttpResponse(200, {"content-type": "text/html"}, self.page, url)

        transport = CollectionTransport()
        with tempfile.TemporaryDirectory() as folder:
            storage = LocalRawContentStorage(Path(folder))
            collector = CollectionRunner(
                sources=sources, runs=runs, documents=documents, storage=storage,
                planner=CollectionPlanner(transport=transport),
                settings=load_settings(environ={}), environ={},
                auto_permission_checker=runner.auto_assessor.revalidate_source,
            )
            worker = CollectionWorker(schedules=schedules, runner=collector,
                                      policy=CollectionSchedulePolicy(), worker_id="offline",
                                      clock=lambda: NOW + timedelta(minutes=1))
            summary = worker.run_once(now=NOW + timedelta(minutes=1))
            self.assertEqual(summary.success_count, 1)
            self.assertEqual(len(documents._rows), 1)
            raw = next(iter(documents._rows.values()))
            self.assertEqual(storage.get(raw.content_ref), transport.page)
            permission_transport.terms = b"Collection permission withdrawn"
            runner.auto_assessor.transport = permission_transport
            blocked = collector.run_source(source.id)
            self.assertFalse(blocked.succeeded)
            self.assertEqual(len(documents._rows), 1)

    def test_schedule_verification_failure_holds_after_activation(self):
        runner, sources, discovery, _ = make_runner()
        runner.auto_executor.schedule_verifier = lambda source: False
        result = runner.run(DiscoveryQuery(uuid4(), "AI conference", "fake", NOW))
        self.assertEqual(result.auto_approved_count, 0)
        self.assertEqual(result.collection_scheduled_count, 0)
        self.assertIn("activation_failed:RuntimeError", result.approval_failure_reasons)
        self.assertEqual(next(iter(sources._rows.values())).status, SourceStatus.HOLD)

    def test_permission_probe_exception_is_classified_and_held(self):
        runner, sources, discovery, _ = make_runner()

        def unavailable(candidate, evaluation):
            raise RuntimeError("offline permission fixture failed")

        runner.auto_assessor.assess = unavailable
        result = runner.run(DiscoveryQuery(uuid4(), "AI conference", "fake", NOW))
        self.assertEqual(result.review_required_count, 1)
        self.assertIn("permission_check_failed", result.approval_failure_reasons)
        self.assertFalse(sources._rows)
        row = next(iter(discovery.candidates.values()))
        self.assertEqual(row.provenance["auto_approval"]["reason"], "permission_check_failed")

    def test_duplicate_machine_approval_is_idempotent(self):
        runner, sources, discovery, reviews = make_runner()
        original_activate = runner.auto_executor.activate
        calls = []
        pending = []

        def activate_twice(source, assessment):
            pending.append((source, assessment))
            first = original_activate(source, assessment)
            second = original_activate(source, assessment)
            calls.extend((first, second))
            return second

        runner.auto_executor.activate = activate_twice
        runner.run(DiscoveryQuery(uuid4(), "AI conference", "fake", NOW))
        self.assertEqual(calls[0].id, calls[1].id)
        self.assertEqual(next(iter(sources._rows.values())).status, SourceStatus.ACTIVE)
        self.assertEqual(len(reviews.list_events(calls[0].id)), 3)
        runner.auto_executor.schedule_verifier = lambda source: False
        with self.assertRaisesRegex(RuntimeError, "collection_schedule_not_confirmed"):
            original_activate(*pending[0])
        self.assertEqual(next(iter(sources._rows.values())).status, SourceStatus.HOLD)

    def test_concurrent_machine_approval_cannot_duplicate_audit(self):
        runner, sources, discovery, reviews = make_runner()
        executor = runner.auto_executor
        pending = []
        runner.auto_executor = type("DeferredApproval", (), {
            "schedule_verifier": None,
            "activate": lambda self, source, assessment: pending.append((source, assessment)),
        })()
        runner.run(DiscoveryQuery(uuid4(), "AI conference", "fake", NOW))
        source, assessment = pending[0]
        reviewed = Event()
        continue_first = Event()
        original_transition = reviews.transition

        def controlled_transition(command):
            result = original_transition(command)
            if command.decision is SourceDecision.REVIEW_STARTED:
                reviewed.set()
                self.assertTrue(continue_first.wait(2))
            return result

        reviews.transition = controlled_transition
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(executor.activate, source, assessment)
            self.assertTrue(reviewed.wait(2))
            second = pool.submit(executor.activate, source, assessment)
            with self.assertRaises(ValueError):
                second.result(timeout=2)
            continue_first.set()
            self.assertEqual(first.result(timeout=2).status, SourceStatus.ACTIVE)
        self.assertEqual(next(iter(sources._rows.values())).status, SourceStatus.ACTIVE)
        self.assertEqual(len(reviews.list_events(source.id)), 3)

    def test_default_off_and_empty_enabled_policy_rejected(self):
        self.assertFalse(load_settings(environ={}).discovery_auto_approval_enabled)
        with self.assertRaisesRegex(ValueError, "reviewed scopes"):
            load_settings(environ={"AGENT_DISCOVERY_AUTO_APPROVAL_ENABLED": "true"})

    def test_scope_validation_and_expiry(self):
        for changes in (
            {"permission_basis": "robots_only"},
            {"origin": "http://official.example.org"},
            {"terms_url": "https://other.example.org/terms"},
            {"path_prefix": "/calls"},
            {"path_prefix": "/"},
            {"terms_url": TERMS_URL + "?token=secret"},
            {"expires_at": NOW + timedelta(days=100)},
        ):
            with self.assertRaises(ValueError):
                scope(**changes)
        self.assertFalse(scope().covers(ORIGIN + "/calls-other/ai"))
        self.assertFalse(scope().covers(ORIGIN + "/calls/ai?token=1"))
        self.assertFalse(scope().covers(ORIGIN + "/calls/%2e%2e/secret"))
        self.assertFalse(scope().covers("https://evil.example.org/calls/ai"))
        self.assertTrue(scope().covers(URL))
        reviewed = scope()
        item = {key: getattr(reviewed, key).isoformat()
                if isinstance(getattr(reviewed, key), datetime) else getattr(reviewed, key)
                for key in reviewed.__dataclass_fields__}
        self.assertEqual(load_approval_scopes(json.dumps([item])), (scope(),))

    def test_official_scope_robots_and_terms_activate_with_machine_audit(self):
        runner, sources, discovery, reviews = make_runner()
        outcome = runner.run(DiscoveryQuery(uuid4(), "AI conference", "fake", NOW))
        self.assertEqual((outcome.query.result_count, outcome.query.candidate_count,
                          outcome.query.promoted_count), (1, 1, 1))
        source = sources.get(next(iter(sources._rows)))
        self.assertEqual(source.status, SourceStatus.ACTIVE)
        self.assertTrue(source_is_collectible(source))
        self.assertEqual([event.command.decision for event in reviews.list_events(source.id)],
                         [SourceDecision.REVIEW_STARTED, SourceDecision.APPROVED,
                          SourceDecision.ACTIVATED])
        self.assertTrue(all(event.command.reviewer == AUTO_REVIEWER
                            for event in reviews.list_events(source.id)))
        candidate = discovery.get_candidate(next(iter(discovery.candidates)))
        self.assertEqual(candidate.status, SourceCandidateStatus.PROMOTED)
        self.assertEqual(candidate.promoted_source_id, source.id)
        self.assertTrue(candidate.provenance["auto_approval"]["eligible"])
        self.assertEqual(source.provenance["source_candidate_id"], str(candidate.id))
        again = runner.run(DiscoveryQuery(uuid4(), "AI conference again", "fake", NOW))
        self.assertEqual(again.query.promoted_count, 0)
        self.assertEqual(len(sources._rows), 1)

    def test_disallowed_robots_changed_terms_and_expired_scope_hold_for_review(self):
        cases = (
            (FakeTransport(robots=b"User-agent: *\nDisallow: /calls/\n"),
             (scope(),), "robots_disallow"),
            (FakeTransport(robots=b"User-agent: AIPATH-AgentCollect\nDisallow: /calls/\n"
                                    b"User-agent: *\nAllow: /\n"),
             (scope(),), "robots_disallow"),
            (FakeTransport(terms=b"Changed terms"), (scope(),),
             "terms_unavailable_or_changed"),
            (FakeTransport(officiality=b"Changed organizer"), (scope(),),
             "officiality_evidence_unavailable_or_changed"),
            (FakeTransport(), (scope(expires_at=NOW),), "scope_expired_or_not_yet_valid"),
            (FakeTransport(page=b"<h1>Cooking recipes</h1>"), (scope(),),
             "page_relevance_unconfirmed"),
            (FakeTransport(robots=b"<html>robots unavailable</html>"), (scope(),),
             "robots_invalid"),
        )
        for transport, scopes, reason in cases:
            with self.subTest(reason=reason):
                runner, sources, discovery, _ = make_runner(transport=transport, scopes=scopes)
                outcome = runner.run(DiscoveryQuery(uuid4(), "AI conference", "fake", NOW))
                self.assertEqual(outcome.query.promoted_count, 0)
                self.assertEqual(len(sources._rows), 0)
                candidate = discovery.get_candidate(next(iter(discovery.candidates)))
                self.assertEqual(candidate.status, SourceCandidateStatus.REVIEW_REQUIRED)
                self.assertEqual(candidate.provenance["auto_approval"]["reason"], reason)

    def test_official_domain_hint_cannot_override_changed_terms(self):
        runner, sources, discovery, _ = make_runner(
            transport=FakeTransport(terms=b"restricted"), official_hint=True)
        runner.run(DiscoveryQuery(uuid4(), "AI conference", "fake", NOW))
        self.assertFalse(sources._rows)
        self.assertEqual(next(iter(discovery.candidates.values())).status,
                         SourceCandidateStatus.REVIEW_REQUIRED)

    def test_existing_human_approved_source_is_untouched(self):
        runner, sources, _, reviews = make_runner()
        registered = SourceRegistryService(sources).register(
            name="Existing", url=URL, source_type=SourceType.OFFICIAL,
            collection_method=CollectionMethod.HTTP_HTML, confidence=1.0).source
        manual = SourceApprovalService(reviews)
        manual.begin_review(registered.id, reviewer="Human Reviewer", reason="official page")
        manual.approve(registered.id, reviewer="Human Reviewer", reason="terms reviewed",
                       officiality_confirmed=True, collection_allowed=True)
        manual.activate(registered.id, reviewer="Human Reviewer", reason="approved")
        outcome = runner.run(DiscoveryQuery(uuid4(), "AI conference", "fake", NOW))
        self.assertEqual((outcome.query.candidate_count, outcome.query.promoted_count), (0, 0))
        self.assertEqual(len(reviews.list_events(registered.id)), 3)
        self.assertTrue(source_is_collectible(sources.get(registered.id)))

    def test_approval_rpc_failure_holds_source_and_records_reason(self):
        runner, sources, discovery, reviews = make_runner(failing_review=True)
        outcome = runner.run(DiscoveryQuery(uuid4(), "AI conference", "fake", NOW))
        self.assertEqual(outcome.query.promoted_count, 1)
        source = next(iter(sources._rows.values()))
        self.assertEqual(source.status, SourceStatus.HOLD)
        self.assertFalse(source_is_collectible(source))
        self.assertEqual([event.command.decision for event in reviews.list_events(source.id)],
                         [SourceDecision.REVIEW_STARTED, SourceDecision.HELD])
        candidate = next(iter(discovery.candidates.values()))
        self.assertEqual(candidate.provenance["auto_approval"]["failure"], "RuntimeError")

    def test_later_collection_requires_fresh_permission_check(self):
        runner, sources, _, _ = make_runner()
        runner.run(DiscoveryQuery(uuid4(), "AI conference", "fake", NOW))
        source = next(iter(sources._rows.values()))
        valid = AutoApprovalAssessor((scope(),), user_agent="AIPATH-AgentCollect/0.1",
                                     transport=FakeTransport(), clock=lambda: NOW)
        self.assertTrue(valid.revalidate_source(source))
        changed = AutoApprovalAssessor((scope(),), user_agent="AIPATH-AgentCollect/0.1",
                                       transport=FakeTransport(terms=b"restricted"), clock=lambda: NOW)
        self.assertFalse(changed.revalidate_source(source))
        self.assertFalse(AutoApprovalAssessor(
            (scope(),), user_agent="AIPATH-AgentCollect/0.1",
            transport=FakeTransport(), clock=lambda: NOW + timedelta(days=31),
        ).revalidate_source(source))
        collection = CollectionRunner(
            sources=sources, runs=InMemoryCollectionRunRepository(),
            documents=InMemoryRawDocumentRepository(), storage=object(),
            planner=CollectionPlanner(), settings=load_settings(environ={}), environ={},
        )
        outcome = collection.run_source(source.id)
        self.assertFalse(outcome.succeeded)
        self.assertIn("permission is no longer verified", outcome.error)


if __name__ == "__main__":
    unittest.main()
