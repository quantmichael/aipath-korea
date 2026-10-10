"""PHASE 7.3B human Source approval is offline and fail-closed."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from agent_collect.adapters.service_db.mapping import (
    approved_source_identity, preview_approved_source_mapping,
)
from agent_collect.collectors.runner import CollectionRunner
from agent_collect.config.settings import load_settings
from agent_collect.registry.approval import (
    SourceApprovalService, SourceDecision, SourceReviewCommand,
    source_is_collectible,
)
from agent_collect.registry.service import SourceRegistryService
from agent_collect.repositories.memory import (
    InMemoryCollectionRunRepository, InMemoryRawDocumentRepository, InMemorySourceRepository,
)
from agent_collect.repositories.source_review import InMemorySourceReviewRepository
from agent_collect.repositories.source_review_supabase import SupabaseSourceReviewRepository
from agent_collect.repositories.supabase import SupabaseSourceRepository
from agent_collect.schemas.models import CollectionMethod, SourceType
from agent_collect.schemas.status import RunStatus, SourceStatus


class SourceApprovalTests(unittest.TestCase):
    def setUp(self):
        self.sources = InMemorySourceRepository()
        self.registry = SourceRegistryService(self.sources)
        self.reviews = InMemorySourceReviewRepository(self.sources)
        self.approval = SourceApprovalService(self.reviews)
        self.source = self.registry.register(
            name="Official AI Event", url="https://example.org/events",
            collection_method=CollectionMethod.HTTP_HTML,
            provenance={"discovery_officiality_signals": ["configured_official_domain_hint"],
                        "discovery_signal_is_approval": False},
        ).source

    def review(self):
        return self.approval.begin_review(self.source.id, reviewer="human:alice",
                                          reason="officiality and collection permission checked")

    def approve(self):
        self.review()
        self.approval.change_source_type(self.source.id, SourceType.OFFICIAL,
                                         reviewer="human:alice", reason="event owner confirmed")
        return self.approval.approve(self.source.id, reviewer="human:alice",
                                     reason="official site and collection allowed",
                                     officiality_confirmed=True, collection_allowed=True)

    def test_lifecycle_audit_and_discovery_signal_is_not_approval(self):
        self.assertEqual(self.source.status, SourceStatus.CANDIDATE)
        self.assertFalse(source_is_collectible(self.source))
        with self.assertRaises(ValueError):
            self.approval.activate(self.source.id, reviewer="human:alice", reason="skip")
        self.review()
        with self.assertRaises(ValueError):
            self.approval.activate(self.source.id, reviewer="human:alice", reason="skip")
        self.approval.change_source_type(self.source.id, SourceType.OFFICIAL,
                                         reviewer="human:alice", reason="owner confirmed")
        with self.assertRaises(ValueError):
            self.approval.approve(self.source.id, reviewer="human:alice",
                                  reason="collection not allowed", officiality_confirmed=True,
                                  collection_allowed=False)
        with self.assertRaises(ValueError):
            self.approval.approve(self.source.id, reviewer="human:alice",
                                  reason="officiality not confirmed", officiality_confirmed=False,
                                  collection_allowed=True)
        approved = self.approval.approve(self.source.id, reviewer="human:alice",
                                         reason="terms reviewed", officiality_confirmed=True,
                                         collection_allowed=True)
        self.assertEqual(approved.status, SourceStatus.APPROVED)
        self.assertFalse(approved.is_active)
        active = self.approval.activate(self.source.id, reviewer="human:bob",
                                        reason="start approved collection")
        self.assertTrue(source_is_collectible(active))
        events = self.reviews.list_events(self.source.id)
        self.assertEqual([item.command.decision for item in events], [
            SourceDecision.REVIEW_STARTED, SourceDecision.TYPE_CHANGED,
            SourceDecision.APPROVED, SourceDecision.ACTIVATED,
        ])
        self.assertEqual(events[-1].command.reviewer, "human:bob")
        self.assertEqual(events[-1].command.reason, "start approved collection")
        self.assertIsNotNone(events[-1].command.reviewed_at.tzinfo)
        self.assertEqual(events[-1].canonical_url, self.source.canonical_url)
        self.assertEqual(events[-1].domain, "example.org")
        self.assertEqual(events[-2].approval_decision, "APPROVED")
        self.assertTrue(events[-2].collection_allowed)
        self.assertTrue(events[-2].officiality_confirmed)
        self.assertEqual(events[-1].activation_decision, "ACTIVATED")

    def test_rejected_hold_disabled_and_untrusted_type_fail_closed(self):
        self.review()
        with self.assertRaises(ValueError):
            self.approval.approve(self.source.id, reviewer="human:alice", reason="unknown type",
                                  officiality_confirmed=False, collection_allowed=True)
        rejected = self.approval.reject(self.source.id, reviewer="human:alice",
                                        reason="cannot establish officiality")
        self.assertEqual(rejected.status, SourceStatus.REJECTED)
        with self.assertRaises(ValueError):
            self.approval.activate(self.source.id, reviewer="human:alice", reason="skip")
        self.approval.begin_review(self.source.id, reviewer="human:bob", reason="new evidence")
        held = self.approval.hold(self.source.id, reviewer="human:bob", reason="robots review pending")
        self.assertEqual(held.status, SourceStatus.HOLD)
        with self.assertRaises(ValueError):
            self.approval.activate(self.source.id, reviewer="human:bob", reason="skip")
        self.approval.begin_review(self.source.id, reviewer="human:bob", reason="new evidence")
        self.approval.change_source_type(self.source.id, SourceType.OFFICIAL,
                                         reviewer="human:bob", reason="owner confirmed")
        self.approval.approve(self.source.id, reviewer="human:bob", reason="allowed",
                              officiality_confirmed=True, collection_allowed=True)
        self.approval.activate(self.source.id, reviewer="human:bob", reason="start")
        disabled = self.approval.disable(self.source.id, reviewer="human:bob", reason="temporarily off")
        self.assertFalse(source_is_collectible(disabled))

    def test_aggregator_trust_requires_reviewed_threshold_and_snapshot(self):
        self.registry.update(self.source.id, confidence=0.7)
        self.review()
        self.approval.change_source_type(self.source.id, SourceType.AGGREGATOR,
                                         reviewer="human:alice", reason="aggregator identity")
        with self.assertRaises(ValueError):
            self.approval.approve(self.source.id, reviewer="human:alice", reason="low trust",
                                  officiality_confirmed=False, collection_allowed=True)
        self.registry.update(self.source.id, confidence=0.85)
        approved = self.approval.approve(self.source.id, reviewer="human:alice",
                                         reason="trusted aggregator and allowed collection",
                                         officiality_confirmed=False, collection_allowed=True)
        self.assertEqual(approved.provenance["source_review"]["approval"]["trust"], "TRUSTED")
        active = self.approval.activate(self.source.id, reviewer="human:alice", reason="start")
        self.assertTrue(source_is_collectible(active))
        self.assertFalse(source_is_collectible(replace(active, confidence=0.6)))

    def test_missing_human_decisions_and_snapshot_mismatch_fail_closed(self):
        self.review()
        self.approval.change_source_type(self.source.id, SourceType.OFFICIAL,
                                         reviewer="human:alice", reason="owner confirmed")
        with self.assertRaises(ValueError):
            self.approval.approve(self.source.id, reviewer="", reason="valid",
                                  officiality_confirmed=True, collection_allowed=True)
        with self.assertRaises(ValueError):
            self.approval.approve(self.source.id, reviewer="human:alice", reason="",
                                  officiality_confirmed=True, collection_allowed=True)
        with self.assertRaises(ValueError):
            self.approval.approve(self.source.id, reviewer="human:alice", reason="valid",
                                  officiality_confirmed=None, collection_allowed=True)
        with self.assertRaises(ValueError):
            self.approval.approve(self.source.id, reviewer="human:alice", reason="valid",
                                  officiality_confirmed=True, collection_allowed=None)
        approved = self.approval.approve(self.source.id, reviewer="human:alice", reason="valid",
                                         officiality_confirmed=True, collection_allowed=True)
        self.sources._save_reviewed(replace(approved, collection_config={"changed": True}))
        with self.assertRaises(ValueError):
            self.approval.activate(self.source.id, reviewer="human:alice", reason="start")

    def test_direct_primitives_and_mutation_cannot_bypass_approval(self):
        with self.assertRaises(ValueError):
            self.registry.activate(self.source.id)
        with self.assertRaises(ValueError):
            self.sources.save(replace(self.source, status=SourceStatus.ACTIVE, is_active=True))
        self.approve()
        with self.assertRaises(ValueError):
            self.registry.update(self.source.id, url="https://other.example.org")
        with self.assertRaises(ValueError):
            self.registry.update(self.source.id, collection_config={"request_url": "https://bad.example.org"})
        with self.assertRaises(ValueError):
            self.sources.save(replace(self.sources.get(self.source.id), source_type=SourceType.COMMUNITY))
        active = self.approval.activate(self.source.id, reviewer="human:alice", reason="start")
        self.assertFalse(source_is_collectible(replace(active, confidence=0.1)))
        self.assertFalse(source_is_collectible(replace(active, collection_config={"request_url": "https://bad.example.org"})))

    def test_approval_is_idempotent_under_concurrency(self):
        self.review()
        self.approval.change_source_type(self.source.id, SourceType.OFFICIAL,
                                         reviewer="human:alice", reason="owner")
        key = uuid4()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: self.approval.approve(
                self.source.id, reviewer="human:alice", reason="approved", decision_id=key,
                officiality_confirmed=True, collection_allowed=True), range(2)))
        self.assertEqual([item.status for item in outcomes], [SourceStatus.APPROVED] * 2)
        self.assertEqual(len([item for item in self.reviews.list_events(self.source.id)
                              if item.command.decision is SourceDecision.APPROVED]), 1)
        with self.assertRaises(ValueError):
            self.approval.approve(self.source.id, reviewer="human:alice", reason="different",
                                  officiality_confirmed=True, collection_allowed=True)

    def test_collection_runner_requires_reviewed_active_source(self):
        calls = []
        class Planner:
            def plan(self, source):
                calls.append(source.id)
                raise RuntimeError("stop after gate")
        class Storage:
            pass
        runner = CollectionRunner(
            sources=self.sources, runs=InMemoryCollectionRunRepository(),
            documents=InMemoryRawDocumentRepository(), storage=Storage(), planner=Planner(),
            settings=load_settings(environ={}), environ={},
        )
        failed = runner.run_source(self.source.id)
        self.assertEqual(failed.run.status, RunStatus.FAILED)
        self.assertEqual(calls, [])
        self.approve()
        approved = runner.run_source(self.source.id)
        self.assertEqual(approved.run.status, RunStatus.FAILED)
        self.assertEqual(calls, [])
        self.approval.activate(self.source.id, reviewer="human:alice", reason="start")
        runner.run_source(self.source.id)
        self.assertEqual(calls, [self.source.id])
        self.approval.hold(self.source.id, reviewer="human:alice", reason="pause")
        runner.run_source(self.source.id)
        self.assertEqual(calls, [self.source.id])

    def test_service_mapping_preview_never_creates_source(self):
        approved = self.approve()
        identity = approved_source_identity(approved)
        self.assertEqual(identity.normalized_domain, "example.org")
        self.assertEqual(identity.agent_source_id, self.source.id)
        service_id = uuid4()
        rows = [{"id": str(service_id), "homepage_url": "https://example.org/events/",
                 "name": "Official AI Event", "is_active": True}]
        self.assertEqual(preview_approved_source_mapping(approved, rows),
                         (service_id, "EXACT_HOMEPAGE"))
        domain_row = [{"id": str(service_id), "homepage_url": "https://example.org/",
                       "name": "Official AI Event", "is_active": True}]
        self.assertEqual(preview_approved_source_mapping(approved, domain_row),
                         (service_id, "DOMAIN_AND_NAME"))
        self.assertEqual(preview_approved_source_mapping(approved, domain_row * 2)[1],
                         "SOURCE_NOT_FOUND_OR_AMBIGUOUS")
        self.assertIsNone(preview_approved_source_mapping(approved, [])[0])
        self.assertIsNone(preview_approved_source_mapping(
            approved, rows, explicit_service_source_id=uuid4())[0])
        with self.assertRaises(ValueError):
            approved_source_identity(self.source)

    def test_migration_is_agent_only_and_append_only(self):
        sql = (Path(__file__).resolve().parents[1] / "agent_collect" / "migrations" /
               "004_source_review_foundation.sql").read_text()
        self.assertNotIn("public.opportunities", sql)
        self.assertNotIn("drop table", sql.lower())
        self.assertIn("public.agent_source_review_events", sql)
        self.assertIn("agent_review_source", sql)
        self.assertIn("agent_source_review_events_immutable", sql)

    def test_migration_blocks_direct_insert_update_and_audit_writes(self):
        sql = (Path(__file__).resolve().parents[1] / "agent_collect" / "migrations" /
               "004_source_review_foundation.sql").read_text().lower()
        self.assertIn("before insert or update on public.agent_sources", sql)
        self.assertIn("new.status is distinct from 'candidate'", sql)
        self.assertIn("new.is_active is distinct from false", sql)
        self.assertIn("new.provenance ?| array['source_review', 'approval'", sql)
        self.assertIn("current_user <> pg_get_userbyid", sql)
        self.assertIn("security definer set search_path = pg_catalog", sql)
        self.assertIn("grant select on public.agent_source_review_events to service_role", sql)
        self.assertNotIn("grant select, insert on public.agent_source_review_events", sql)
        self.assertIn("before update or delete on public.agent_source_review_events", sql)
        self.assertIn("where id = p_source_id for update", sql)
        self.assertIn("insert into public.agent_source_review_events", sql)
        self.assertIn("v_review #>> '{approval,domain}' is distinct from v_domain", sql)
        self.assertIn("v_review #>> '{approval,trust}' is distinct from v_trust", sql)
        self.assertIn("provenance #>> '{source_review,last_decision}' = 'activated'", sql)
        self.assertIn("provenance #>> '{source_review,approval,reason}' is not null", sql)

    def test_supabase_source_writer_uses_safe_insert_then_update(self):
        operations = []
        class Query:
            def __init__(self):
                self.row = None
            def insert(self, row):
                self.row = row
                operations.append("insert")
                return self
            def update(self, row):
                self.row = row
                operations.append("update")
                return self
            def eq(self, *_):
                return self
            def execute(self):
                return SimpleNamespace(data=[self.row])
        class Client:
            def table(self, name):
                if name != "agent_sources":
                    raise AssertionError(name)
                return Query()
        repo = SupabaseSourceRepository(Client())
        with patch.object(repo, "get", return_value=None):
            repo.save(self.source)
            for status in (SourceStatus.HOLD, SourceStatus.REVIEW,
                           SourceStatus.APPROVED, SourceStatus.ACTIVE):
                with self.assertRaisesRegex(ValueError, "inactive CANDIDATE"):
                    repo.save(replace(self.source, status=status,
                                      is_active=(status is SourceStatus.ACTIVE)))
            with self.assertRaisesRegex(ValueError, "review metadata"):
                repo.save(replace(self.source, provenance={"source_review": {"approval": {}}}))
        self.assertEqual(operations, ["insert"])
        active = self.approve()
        active = self.approval.activate(active.id, reviewer="human:alice", reason="start")
        with patch.object(repo, "get", return_value=active):
            repo.save(replace(active, name="Updated display name"))
        self.assertEqual(operations, ["insert", "update"])

    def test_supabase_port_calls_review_rpc_not_service_tables(self):
        calls = []
        source_id = self.source.id
        class Client:
            def rpc(self, name, payload):
                calls.append((name, payload))
                return self
            def execute(self):
                return SimpleNamespace(data={"id": str(source_id)})
        command = SourceReviewCommand(
            source_id, SourceStatus.CANDIDATE, SourceStatus.REVIEW,
            "human:alice", "start review", SourceDecision.REVIEW_STARTED,
            self.source.discovered_at, uuid4(),
        )
        with patch("agent_collect.repositories.source_review_supabase."
                   "SupabaseSourceRepository._from_row", return_value=self.source):
            result = SupabaseSourceReviewRepository(Client()).transition(command)
        self.assertEqual(result.id, source_id)
        self.assertEqual(calls[0][0], "agent_review_source")
        self.assertEqual(calls[0][1]["p_decision_id"], str(command.decision_id))


if __name__ == "__main__":
    unittest.main()
