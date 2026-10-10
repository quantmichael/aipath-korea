"""Offline integration and safety tests for PHASE 3 raw collection."""

import hashlib
import os
import socket
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from agent_collect.collectors.contracts import CollectionRequest
from agent_collect.collectors.engine import ApiCollector, HtmlCollector, RssCollector
from agent_collect.collectors.planner import (
    CollectionPlanner,
    UnsupportedCollectionMethodError,
)
from agent_collect.collectors.runner import CollectionRunner
from agent_collect.collectors.security import (
    CollectionTransportError,
    UnsafeTargetError,
    resolve_public_address,
    validate_target_url,
)
from agent_collect.collectors.transport import HttpResponse, SafeHttpTransport
from agent_collect.config.settings import load_settings
from agent_collect.registry.service import SourceRegistryService, canonicalize_url
from agent_collect.registry.approval import SourceApprovalService
from agent_collect.repositories.source_review import InMemorySourceReviewRepository
from agent_collect.repositories.memory import (
    InMemoryCollectionRunRepository,
    InMemoryRawDocumentRepository,
    InMemorySourceRepository,
)
from agent_collect.repositories.supabase import (
    SupabaseCollectionRunRepository,
    SupabaseRawDocumentRepository,
    SupabaseSourceRepository,
)
from agent_collect.schemas.models import CollectionMethod, CollectionRun, RawDocument, SourceType
from agent_collect.schemas.status import RunStatus, SourceStatus
from agent_collect.storage.raw import LocalRawContentStorage


NOW = datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc)


class FakeTransport:
    def __init__(self, *responses: HttpResponse | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def fetch(self, url, *, headers, timeout, max_bytes, max_redirects):
        self.calls.append((url, dict(headers)))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def response(content: bytes, content_type: str, *, status: int = 200) -> HttpResponse:
    return HttpResponse(status, {"content-type": content_type}, content,
                        "https://example.org/final")


def request(url: str = "https://example.org/events") -> CollectionRequest:
    return CollectionRequest(
        requested_url=url, timeout_seconds=2, max_response_bytes=1024 * 1024,
        max_redirects=2, max_retries=0, retry_backoff_seconds=0,
        user_agent="AIPATH-Test/1", headers={}, query_params={},
    )


def registered_source(method: CollectionMethod = CollectionMethod.HTTP_HTML):
    repo = InMemorySourceRepository()
    registry = SourceRegistryService(repo)
    source = registry.register(
        name="Example", url="https://example.org/events", source_type=SourceType.OFFICIAL,
        collection_method=method, discovered_at=NOW,
        provenance={"found_at": "https://example.org"},
    ).source
    approval = SourceApprovalService(InMemorySourceReviewRepository(repo))
    approval.begin_review(source.id, reviewer="test_human", reason="fixture review")
    approval.approve(source.id, reviewer="test_human", reason="fixture official source",
                     officiality_confirmed=True, collection_allowed=True)
    return approval.activate(source.id, reviewer="test_human", reason="fixture activation"), repo, registry


class RegistryTests(unittest.TestCase):
    def test_canonical_url_and_duplicate_source(self) -> None:
        repo = InMemorySourceRepository()
        registry = SourceRegistryService(repo)
        first = registry.register(name="One", url="HTTPS://EXAMPLE.ORG:443/events/?utm_source=x&b=2&a=1",
                                  discovered_at=NOW)
        second = registry.register(name="Two", url="https://example.org/events?a=1&b=2#top",
                                   discovered_at=NOW)
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.source.id, second.source.id)
        self.assertEqual(first.source.canonical_url, "https://example.org/events?a=1&b=2")

    def test_update_activation_and_collection_tracking(self) -> None:
        repo = InMemorySourceRepository()
        registry = SourceRegistryService(repo)
        source = registry.register(name="Feed", url="https://example.org/rss", discovered_at=NOW).source
        with self.assertRaises(ValueError):
            registry.activate(source.id)
        source = registry.update(source.id, collection_method=CollectionMethod.RSS)
        self.assertEqual(source.collection_method, CollectionMethod.RSS)
        approval = SourceApprovalService(InMemorySourceReviewRepository(repo))
        approval.begin_review(source.id, reviewer="test_human", reason="fixture review")
        approval.change_source_type(source.id, SourceType.OFFICIAL,
                                    reviewer="test_human", reason="official source confirmed")
        approval.approve(source.id, reviewer="test_human", reason="fixture approved",
                         officiality_confirmed=True, collection_allowed=True)
        source = approval.activate(source.id, reviewer="test_human", reason="fixture activated")
        self.assertTrue(source.is_active)
        source = registry.record_collection_result(source.id, succeeded=False, collected_at=NOW)
        self.assertEqual(source.last_failure_at, NOW)
        source = registry.record_collection_result(source.id, succeeded=True,
                                                   collected_at=NOW + timedelta(minutes=1))
        self.assertEqual(source.last_success_at, NOW + timedelta(minutes=1))
        with self.assertRaises(ValueError):
            registry.deactivate(source.id)
        self.assertFalse(approval.hold(source.id, reviewer="test_human",
                                       reason="fixture paused").is_active)

    def test_update_detects_url_conflict(self) -> None:
        repo = InMemorySourceRepository()
        registry = SourceRegistryService(repo)
        first = registry.register(name="One", url="https://example.org/one", discovered_at=NOW).source
        second = registry.register(name="Two", url="https://example.org/two", discovered_at=NOW).source
        with self.assertRaises(ValueError):
            registry.update(second.id, url=first.url)


class PlannerTests(unittest.TestCase):
    def test_supported_methods_map_to_generic_collectors(self) -> None:
        planner = CollectionPlanner(FakeTransport())
        for method, expected in (
            (CollectionMethod.HTTP_HTML, HtmlCollector),
            (CollectionMethod.RSS, RssCollector),
            (CollectionMethod.REST_API, ApiCollector),
            (CollectionMethod.HTML, HtmlCollector),
        ):
            source, _, _ = registered_source(method)
            self.assertIsInstance(planner.plan(source), expected)

    def test_unsupported_method_is_explicit(self) -> None:
        source, _, _ = registered_source(CollectionMethod.BROWSER)
        with self.assertRaises(UnsupportedCollectionMethodError):
            CollectionPlanner(FakeTransport()).plan(source)


class CollectorTests(unittest.TestCase):
    def test_html_collects_bytes_hash_title_and_encoding(self) -> None:
        body = '<html><head><meta charset="euc-kr"><title>인공지능 행사</title></head></html>'.encode("euc-kr")
        transport = FakeTransport(response(body, "text/html"))
        source, _, _ = registered_source()
        result = HtmlCollector(transport).collect(source, request())
        self.assertTrue(result.succeeded)
        self.assertEqual(result.content, body)
        self.assertEqual(result.content_hash, hashlib.sha256(body).hexdigest())
        self.assertEqual(result.metadata["title"], "인공지능 행사")

    def test_rss_and_atom_keep_feed_item_metadata(self) -> None:
        rss = b'<rss version="2.0"><channel><title>Feed</title><link>https://example.org</link><item><title>Talk</title><link>https://example.org/talk</link><guid>1</guid></item></channel></rss>'
        atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom</title><entry><title>Entry</title><id>x</id><link href="https://example.org/x"/></entry></feed>'
        source, _, _ = registered_source(CollectionMethod.RSS)
        rss_result = RssCollector(FakeTransport(response(rss, "application/rss+xml"))).collect(source, request())
        atom_result = RssCollector(FakeTransport(response(atom, "application/atom+xml"))).collect(source, request())
        self.assertEqual(rss_result.metadata["items_sample"][0]["link"], "https://example.org/talk")
        self.assertEqual(atom_result.metadata["items_sample"][0]["link"], "https://example.org/x")
        self.assertEqual(rss_result.metadata["item_count"], 1)

    def test_api_get_query_headers_and_json(self) -> None:
        transport = FakeTransport(response(b'{"items": [1, 2]}', "application/json; charset=utf-8"))
        source, _, _ = registered_source(CollectionMethod.REST_API)
        modified = replace(request("https://example.org/api?existing=1"),
                           headers={"X-Test": "value"}, query_params={"page": "2"})
        result = ApiCollector(transport).collect(source, modified)
        self.assertTrue(result.succeeded)
        self.assertEqual(result.metadata["top_level_keys"], ["items"])
        self.assertEqual(transport.calls[0][0], "https://example.org/api?existing=1&page=2")
        self.assertEqual(transport.calls[0][1]["X-Test"], "value")

    def test_timeout_retry_and_invalid_content(self) -> None:
        source, _, _ = registered_source()
        transport = FakeTransport(CollectionTransportError("timeout", "timed out"),
                                  response(b"<title>Recovered</title>", "text/html"))
        retried = HtmlCollector(transport, sleep=lambda _: None).collect(
            source, replace(request(), max_retries=1)
        )
        self.assertTrue(retried.succeeded)
        self.assertEqual(len(transport.calls), 2)
        failed = HtmlCollector(FakeTransport(response(b"{}", "application/json"))).collect(source, request())
        self.assertEqual(failed.error.code, "invalid_content")


class SecurityTests(unittest.TestCase):
    def test_rejects_local_private_and_malformed_targets(self) -> None:
        for url in (
            "http://localhost/", "http://127.0.0.1/", "http://10.1.2.3/",
            "http://169.254.169.254/latest", "file:///etc/passwd",
            "http://[broken", "http://user:pass@example.org/",
        ):
            with self.subTest(url=url), self.assertRaises(UnsafeTargetError):
                validate_target_url(url)

    def test_dns_rejects_mixed_public_private_answers(self) -> None:
        def resolver(host, port, *, type):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.215.14", port)),
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.2", port)),
            ]
        with self.assertRaises(UnsafeTargetError):
            resolve_public_address("example.org", 443, resolver=resolver)

    def test_redirect_to_private_target_is_blocked(self) -> None:
        calls = []
        def resolver(host, port, *, type):
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.215.14", port))]
        def dialer(scheme, host, port, address, path, headers, timeout, max_bytes):
            calls.append((host, address))
            return 302, {"location": "http://127.0.0.1/private"}, b""
        transport = SafeHttpTransport(resolver=resolver, dialer=dialer)
        with self.assertRaises(UnsafeTargetError):
            transport.fetch("https://example.org/start", headers={}, timeout=1,
                            max_bytes=100, max_redirects=2)
        self.assertEqual(len(calls), 1)

    def test_redirect_drops_secret_header_and_checks_size(self) -> None:
        def resolver(host, port, *, type):
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.215.14", port))]
        calls = []
        def dialer(scheme, host, port, address, path, headers, timeout, max_bytes):
            calls.append((host, dict(headers)))
            if len(calls) == 1:
                return 302, {"location": "https://other.example.org/next"}, b""
            return 200, {"content-type": "text/html"}, b"ok"
        transport = SafeHttpTransport(resolver=resolver, dialer=dialer)
        fetched = transport.fetch("https://example.org/start", headers={"Authorization": "secret"},
                                  timeout=1, max_bytes=100, max_redirects=2)
        self.assertEqual(fetched.content, b"ok")
        self.assertNotIn("Authorization", calls[1][1])
        with self.assertRaises(CollectionTransportError) as context:
            SafeHttpTransport(resolver=resolver,
                              dialer=lambda *args: (200, {}, b"too big")).fetch(
                                  "https://example.org/", headers={}, timeout=1,
                                  max_bytes=2, max_redirects=0)
        self.assertEqual(context.exception.code, "response_too_large")


class StorageTests(unittest.TestCase):
    def test_storage_round_trip_collision_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalRawContentStorage(Path(directory), retention_days=90)
            data = b"raw response"
            digest = hashlib.sha256(data).hexdigest()
            first = storage.put(data, expected_sha256=digest, stored_at=NOW)
            second = storage.put(data, expected_sha256=digest, stored_at=NOW)
            self.assertNotEqual(first.ref, second.ref)
            self.assertEqual(storage.get(first.ref), data)
            with self.assertRaises(ValueError):
                storage.put(data, expected_sha256="0" * 64)
            with self.assertRaises(ValueError):
                storage.get("local:../../etc/passwd")

    def test_retention_removes_only_expired_managed_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalRawContentStorage(Path(directory), retention_days=90)
            old = storage.put(b"old", stored_at=NOW - timedelta(days=91))
            fresh = storage.put(b"fresh", stored_at=NOW)
            old_path = storage._path(old.ref)
            old_time = (NOW - timedelta(days=91)).timestamp()
            os.utime(old_path, (old_time, old_time))
            self.assertEqual(storage.purge_expired(now=NOW), 1)
            self.assertEqual(storage.get(fresh.ref), b"fresh")


class RunnerTests(unittest.TestCase):
    def test_success_records_source_run_raw_and_content(self) -> None:
        source, sources, _ = registered_source()
        runs = InMemoryCollectionRunRepository()
        documents = InMemoryRawDocumentRepository()
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalRawContentStorage(Path(directory))
            runner = CollectionRunner(
                sources=sources, runs=runs, documents=documents, storage=storage,
                planner=CollectionPlanner(FakeTransport(response(b"<title>Talk</title>", "text/html"))),
                settings=load_settings(environ={}),
            )
            outcome = runner.run_source(source.id)
            self.assertTrue(outcome.succeeded)
            self.assertEqual(runs.get(outcome.run.id).stored_count, 1)
            raw = documents.get(outcome.raw_document.id)
            self.assertEqual(raw.run_id, outcome.run.id)
            self.assertEqual(raw.source_id, source.id)
            self.assertEqual(raw.provenance["collection_method"], "http_html")
            self.assertEqual(raw.provenance["retention_policy"]["days"], 90)
            self.assertIsNotNone(raw.provenance["retention_policy"]["expires_at"])
            self.assertEqual(storage.get(raw.content_ref), b"<title>Talk</title>")
            self.assertIsNotNone(sources.get(source.id).last_success_at)

    def test_failure_and_unsupported_method_leave_run_record(self) -> None:
        source, sources, registry = registered_source()
        runs = InMemoryCollectionRunRepository()
        documents = InMemoryRawDocumentRepository()
        with tempfile.TemporaryDirectory() as directory:
            runner = CollectionRunner(
                sources=sources, runs=runs, documents=documents,
                storage=LocalRawContentStorage(Path(directory)),
                planner=CollectionPlanner(FakeTransport(CollectionTransportError("timeout", "timed out"))),
                settings=load_settings(environ={"AGENT_MAX_RETRIES": "0"}),
            )
            failed = runner.run_source(source.id)
            self.assertEqual(failed.run.status, RunStatus.FAILED)
            self.assertIsNotNone(runs.get(failed.run.id).error_summary)
            self.assertIsNone(failed.raw_document)
            self.assertIsNotNone(sources.get(source.id).last_failure_at)
            review = SourceApprovalService(InMemorySourceReviewRepository(sources))
            review.hold(source.id, reviewer="test_human", reason="change collection method")
            review.begin_review(source.id, reviewer="test_human", reason="method review")
            unsupported = registry.update(source.id, collection_method=CollectionMethod.BROWSER)
            review.approve(source.id, reviewer="test_human", reason="method approved for test",
                           officiality_confirmed=True, collection_allowed=True)
            review.activate(source.id, reviewer="test_human", reason="test unsupported planner")
            other = runner.run_source(source.id)
            self.assertEqual(other.run.status, RunStatus.FAILED)
            self.assertIn("unsupported", other.error)

    def test_raw_metadata_failure_cleans_unreferenced_content_and_records_run(self) -> None:
        class FailingRawRepository(InMemoryRawDocumentRepository):
            def save(self, document):
                raise RuntimeError("metadata unavailable")

        source, sources, _ = registered_source()
        runs = InMemoryCollectionRunRepository()
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalRawContentStorage(Path(directory))
            runner = CollectionRunner(
                sources=sources, runs=runs, documents=FailingRawRepository(),
                storage=storage,
                planner=CollectionPlanner(FakeTransport(response(b"<title>Talk</title>", "text/html"))),
                settings=load_settings(environ={}),
            )
            outcome = runner.run_source(source.id)
            self.assertEqual(outcome.run.status, RunStatus.FAILED)
            self.assertIn("metadata unavailable", outcome.error)
            self.assertIsNone(outcome.raw_document)
            self.assertEqual(list(Path(directory).rglob("*.raw")), [])
            self.assertIsNotNone(runs.get(outcome.run.id))


class _FakeQuery:
    def __init__(self, store, table):
        self.store = store
        self.table = table
        self.filters = {}
        self.record = None

    def select(self, columns):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, count):
        return self

    def upsert(self, record, *, on_conflict):
        self.record = record
        return self

    def insert(self, record):
        self.record = record
        return self

    def update(self, record):
        self.record = record
        return self

    def execute(self):
        if self.record is not None:
            self.store.setdefault(self.table, {})[self.record["id"]] = self.record
            data = [self.record]
        else:
            data = [row for row in self.store.get(self.table, {}).values()
                    if all(row.get(key) == value for key, value in self.filters.items())]
        return type("Result", (), {"data": data})()


class _FakeSupabase:
    def __init__(self):
        self.rows = {}

    def table(self, name):
        return _FakeQuery(self.rows, name)


class SupabaseRepositoryContractTests(unittest.TestCase):
    def test_agent_table_round_trips_with_injected_client(self) -> None:
        client = _FakeSupabase()
        sources = SupabaseSourceRepository(client)
        runs = SupabaseCollectionRunRepository(client)
        documents = SupabaseRawDocumentRepository(client)
        source = SourceRegistryService(sources).register(
            name="Example", url="https://example.org/events", discovered_at=NOW,
            collection_method=CollectionMethod.RSS,
        ).source
        self.assertEqual(sources.get(source.id).canonical_url, source.canonical_url)
        self.assertEqual(sources.find_by_canonical_url(source.canonical_url).id, source.id)
        run = CollectionRun(uuid4(), source.id, RunStatus.RUNNING, NOW,
                            collection_method=CollectionMethod.RSS)
        runs.save(run)
        self.assertEqual(runs.get(run.id).source_id, source.id)
        raw = RawDocument(uuid4(), run.id, source.id, source.url, "local:ref", "a" * 64,
                          NOW, media_type="application/rss+xml")
        documents.save(raw)
        self.assertEqual(documents.get(raw.id).content_sha256, "a" * 64)
        self.assertEqual(set(client.rows), {
            "agent_sources", "agent_collection_runs", "agent_raw_documents",
        })


if __name__ == "__main__":
    unittest.main()
