"""Small manual entrypoint for registration, collection, and inspection."""

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID, uuid4

from agent_collect.config.settings import load_settings
from agent_collect.registry.service import SourceRegistryService
from agent_collect.runtime import AgentRuntime, resolve_supabase_config
from agent_collect.schemas.models import CollectionMethod, SourceType


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m agent_collect")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("config-check", help="Validate local settings and credential presence; no network")

    register = commands.add_parser("register-source", help="Register an Agent Source")
    register.add_argument("--name", required=True)
    register.add_argument("--url", required=True)
    register.add_argument("--method", choices=[item.value for item in CollectionMethod],
                          default=CollectionMethod.HTTP_HTML.value)
    register.add_argument("--type", choices=[item.value for item in SourceType],
                          default=SourceType.UNKNOWN.value)
    register.add_argument("--activate", action="store_true",
                          help="Deprecated and refused; approval workflow is required")
    register.add_argument("--interval-minutes", type=int,
                          help="Fixed collection interval proposed before human approval; default 720")

    collect = commands.add_parser("collect-source", help="Collect one registered Source")
    collect.add_argument("--source-id", required=True, type=UUID)
    collect.add_argument("--raw-dir", type=Path,
                         help="Private local raw directory; not needed for Supabase provider")

    due = commands.add_parser("collection-due", help="List bounded, approved ACTIVE due Sources")
    due.add_argument("--limit", type=int, default=None)
    worker = commands.add_parser("collection-worker", help="Run one bounded collection worker pass")
    mode = worker.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--once", action="store_true")
    worker.add_argument("--raw-dir", type=Path,
                        help="Persistent private raw storage; required with --once")
    worker.add_argument("--worker-id")

    processing_due = commands.add_parser("processing-due", help="List bounded queued RawDocuments")
    processing_due.add_argument("--limit", type=int, default=None)
    processing_worker = commands.add_parser("processing-worker", help="Run one bounded processing pass")
    processing_mode = processing_worker.add_mutually_exclusive_group(required=True)
    processing_mode.add_argument("--dry-run", action="store_true")
    processing_mode.add_argument("--once", action="store_true")
    processing_worker.add_argument("--raw-dir", type=Path,
                                   help="Local raw directory when local provider is configured")
    processing_worker.add_argument("--worker-id")

    discovery_due = commands.add_parser("discovery-due", help="List bounded approved Conference queries due for search")
    discovery_due.add_argument("--limit", type=int, default=None)
    discovery_worker = commands.add_parser("discovery-worker", help="Run one bounded Discovery worker pass")
    discovery_mode = discovery_worker.add_mutually_exclusive_group(required=True)
    discovery_mode.add_argument("--dry-run", action="store_true")
    discovery_mode.add_argument("--once", action="store_true")
    discovery_worker.add_argument("--worker-id")
    discovery_register = commands.add_parser("discovery-query-register", help="Register a disabled reviewed Conference query")
    discovery_register.add_argument("--query", required=True)
    discovery_register.add_argument("--language", required=True)
    discovery_register.add_argument("--country", required=True)
    discovery_register.add_argument("--interval-minutes", type=int)
    discovery_register.add_argument("--reviewer", required=True)
    discovery_register.add_argument("--reason", required=True)
    discovery_review = commands.add_parser("discovery-query-review", help="Human review to enable/disable one query")
    discovery_review.add_argument("--schedule-id", required=True, type=UUID)
    discovery_decision = discovery_review.add_mutually_exclusive_group(required=True)
    discovery_decision.add_argument("--enable", action="store_true")
    discovery_decision.add_argument("--disable", action="store_true")
    discovery_review.add_argument("--reviewer", required=True)
    discovery_review.add_argument("--reason", required=True)

    publish_due = commands.add_parser("publish-due", help="List bounded eligible publish queue jobs")
    publish_due.add_argument("--limit", type=int, default=None)
    publish_worker = commands.add_parser("publish-worker", help="Run one bounded publish pass")
    publish_mode = publish_worker.add_mutually_exclusive_group(required=True)
    publish_mode.add_argument("--dry-run", action="store_true")
    publish_mode.add_argument("--once", action="store_true")
    publish_worker.add_argument("--worker-id")

    automation_status = commands.add_parser("automation-status", help="Show fail-closed automation configuration")
    automation_status.add_argument("--live", action="store_true", help="Include bounded read-only DB due/lease status")
    automation_run = commands.add_parser("automation-run", help="Run enabled workers once")
    automation_mode = automation_run.add_mutually_exclusive_group(required=True)
    automation_mode.add_argument("--dry-run", action="store_true")
    automation_mode.add_argument("--once", action="store_true")
    automation_run.add_argument("--raw-dir", type=Path)
    automation_run.add_argument("--worker-id")

    run = commands.add_parser("show-run", help="Read one Agent CollectionRun")
    run.add_argument("--run-id", required=True, type=UUID)
    document = commands.add_parser("show-document", help="Read one Agent RawDocument metadata row")
    document.add_argument("--document-id", required=True, type=UUID)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "config-check":
            load_settings()
            try:
                resolve_supabase_config()
                credential_state = "present"
            except RuntimeError:
                credential_state = "missing"
            print(json.dumps({"settings": "valid", "credentials": credential_state}))
            return 0

        if args.command == "automation-status":
            settings = load_settings()
            status = {
                "automation_enabled": settings.automation_enabled,
                "discovery_worker_enabled": settings.discovery_worker_enabled and settings.discovery_enabled,
                "collection_worker_enabled": settings.collection_worker_enabled,
                "processing_worker_enabled": settings.processing_worker_enabled,
                "publish_worker_enabled": settings.publish_worker_enabled and settings.publish_enabled,
                "publish_enabled": settings.publish_enabled,
                "discovery_search_budget": {
                    "daily_limit": settings.discovery_daily_search_limit,
                    "monthly_limit": settings.discovery_monthly_search_limit,
                    "cooldown_hours": settings.discovery_query_cooldown_hours,
                },
            }
            if args.live:
                runtime = AgentRuntime.connect()
                status["live"] = runtime.automation_status_live()
            print(json.dumps(status))
            return 0
        if args.command == "automation-run" and not load_settings().automation_enabled:
            print(json.dumps({"status": "DISABLED", "dry_run": args.dry_run,
                              "workers": [], "errors": {}}))
            return 0

        runtime = AgentRuntime.connect()
        if args.command == "discovery-query-register":
            row = runtime.discovery_schedule_repository().register_reviewed(
                query_text=args.query, language=args.language, country=args.country,
                interval_minutes=args.interval_minutes or runtime.settings.discovery_default_interval_minutes,
                reviewer=args.reviewer,
                reason=args.reason)
            print(json.dumps({"schedule_id": str(row.id), "enabled": row.enabled,
                              "query": row.query_text, "next_run_at": row.next_run_at.isoformat()}))
            return 0
        if args.command == "discovery-query-review":
            changed = runtime.discovery_schedule_repository().set_enabled(
                args.schedule_id, enabled=args.enable, reviewer=args.reviewer, reason=args.reason)
            print(json.dumps({"schedule_id": str(args.schedule_id),
                              "enabled": args.enable, "changed": changed}))
            return 0 if changed else 1
        if args.command == "discovery-due":
            limit = runtime.settings.discovery_max_jobs_per_run if args.limit is None else args.limit
            due = runtime.discovery_schedule_repository().list_due(limit)
            print(json.dumps({"due": [
                {"schedule_id": str(item.id), "query": item.query_text,
                 "vertical": item.vertical.value, "language": item.language,
                 "country": item.country, "interval_minutes": item.interval_minutes,
                 "next_run_at": item.next_run_at.isoformat()}
                for item in due
            ]}))
            return 0
        if args.command == "discovery-worker":
            summary = runtime.discovery_worker(
                worker_id=args.worker_id or f"discovery-cli-{uuid4()}").run_once(dry_run=args.dry_run)
            print(json.dumps({
                "worker_id": summary.worker_id, "dry_run": summary.dry_run,
                "due_count": summary.due_count, "claimed_count": summary.claimed_count,
                "success_count": summary.success_count, "failure_count": summary.failure_count,
                "budget_blocked_count": summary.budget_blocked_count,
                "skipped_count": summary.skipped_count, "previews": list(summary.previews),
            }))
            return 0 if summary.failure_count == 0 else 1
        if args.command == "register-source":
            if args.activate:
                raise ValueError("register-source --activate is disabled; use reviewed approval workflow")
            registry = SourceRegistryService(runtime.sources)
            registration = registry.register(
                name=args.name, url=args.url,
                source_type=SourceType(args.type),
                collection_method=CollectionMethod(args.method),
                collection_interval_minutes=args.interval_minutes,
                discovery_method="manual_cli",
                provenance={"registration_method": "manual_cli"},
            )
            source = registration.source
            print(json.dumps({"source_id": str(source.id), "created": registration.created,
                              "status": source.status.value, "active": source.is_active}))
            return 0
        if args.command == "collect-source":
            if runtime.settings.raw_storage_provider == "local" and args.raw_dir is None:
                raise ValueError("--raw-dir is required for local raw storage")
            outcome = runtime.collection_runner(args.raw_dir or Path("agent_raw_content")).run_source(args.source_id)
            print(json.dumps({
                "run_id": str(outcome.run.id), "status": outcome.run.status.value,
                "raw_document_id": str(outcome.raw_document.id) if outcome.raw_document else None,
                "content_ref": outcome.raw_document.content_ref if outcome.raw_document else None,
                "error": outcome.error,
            }))
            return 0 if outcome.succeeded else 1
        if args.command == "collection-due":
            limit = (runtime.collection_schedule_policy().max_jobs_per_run
                     if args.limit is None else args.limit)
            due = runtime.collection_schedule_repository().list_due(limit)
            print(json.dumps({"due": [
                {"source_id": str(item.source.id), "name": item.source.name,
                 "method": item.source.collection_method.value,
                 "interval_minutes": item.interval_minutes,
                 "next_collection_at": item.next_collection_at.isoformat()}
                for item in due
            ]}))
            return 0
        if args.command == "collection-worker":
            if args.once and runtime.settings.raw_storage_provider == "local" and args.raw_dir is None:
                raise ValueError("--raw-dir is required with --once for local raw storage")
            # Storage construction is side-effect free; dry-run never claims,
            # creates a Run, or calls the HTTP collector.
            raw_dir = args.raw_dir or Path("agent_raw_content")
            worker = runtime.collection_worker(raw_dir, worker_id=args.worker_id or f"cli-{uuid4()}")
            summary = worker.run_once(dry_run=args.dry_run)
            print(json.dumps({
                "worker_id": summary.worker_id, "dry_run": summary.dry_run,
                "due_count": summary.due_count, "claimed_count": summary.claimed_count,
                "success_count": summary.success_count, "failure_count": summary.failure_count,
                "skipped_count": summary.skipped_count,
                "due": [{"source_id": str(item.source_id), "name": item.source_name,
                         "method": item.collection_method.value,
                         "interval_minutes": item.interval_minutes,
                         "next_collection_at": item.next_collection_at.isoformat()}
                        for item in summary.due],
            }))
            return 0 if summary.failure_count == 0 else 1
        if args.command == "processing-due":
            limit = args.limit or runtime.processing_policy().max_jobs_per_run
            prefix = (f"supabase:{runtime.settings.raw_storage_bucket}:"
                      if runtime.settings.raw_storage_provider == "supabase" else "local:")
            due = runtime.processing_repository().preview(
                runtime.processing_policy().version, prefix, limit)
            print(json.dumps({"due": [{"job_id": str(item.id),
                                      "raw_document_id": str(item.raw_document_id),
                                      "processing_version": item.version,
                                      "status": item.status.value} for item in due]}))
            return 0
        if args.command == "processing-worker":
            if runtime.settings.raw_storage_provider == "local" and args.raw_dir is None:
                raise ValueError("--raw-dir is required for local raw storage")
            worker = runtime.processing_worker(
                args.raw_dir or Path("agent_raw_content"),
                worker_id=args.worker_id or f"processing-cli-{uuid4()}",
            )
            summary = worker.run_once(dry_run=args.dry_run)
            print(json.dumps({
                "worker_id": summary.worker_id, "dry_run": summary.dry_run,
                "due_count": summary.due_count, "claimed_count": summary.claimed_count,
                "processed_count": summary.processed_count,
                "publish_ready_count": summary.publish_ready_count,
                "review_count": summary.review_count,
                "rejected_count": summary.rejected_count,
                "failed_count": summary.failed_count,
                "budget_blocked_count": summary.budget_blocked_count,
                "due": [{"job_id": str(item.id), "raw_document_id": str(item.raw_document_id),
                         "processing_version": item.version, "status": item.status.value}
                        for item in summary.due],
            }))
            return 0 if summary.failed_count == 0 else 1
        if args.command == "publish-due":
            limit = args.limit or runtime.settings.publish_max_jobs_per_run
            due = runtime.publish_worker(worker_id=f"publish-cli-{uuid4()}").jobs.preview(limit)
            print(json.dumps({"due": [{"job_id": str(job.id),
                                       "candidate_id": str(job.candidate_id)} for job in due]}))
            return 0
        if args.command == "publish-worker":
            summary = runtime.publish_worker(
                worker_id=args.worker_id or f"publish-cli-{uuid4()}").run_once(dry_run=args.dry_run)
            print(json.dumps({"worker_id": summary.worker_id, "dry_run": summary.dry_run,
                              "due_count": summary.due_count, "claimed_count": summary.claimed_count,
                              "published_count": summary.published_count,
                              "duplicate_count": summary.duplicate_count,
                              "review_count": summary.review_count,
                              "failed_count": summary.failed_count,
                              "previews": list(summary.previews)}))
            return 0 if summary.failed_count == 0 else 1
        if args.command == "automation-run":
            if (args.once and runtime.settings.raw_storage_provider == "local" and
                    (runtime.settings.collection_worker_enabled or
                     runtime.settings.processing_worker_enabled) and args.raw_dir is None):
                raise ValueError("--raw-dir is required for enabled local storage workers")
            summary = runtime.automation_runtime(
                args.raw_dir or Path("agent_raw_content"),
                worker_id=args.worker_id or f"automation-cli-{uuid4()}",
            ).run_once(dry_run=args.dry_run)
            print(json.dumps({"run_id": str(summary.run_id), "status": summary.status,
                              "dry_run": summary.dry_run,
                              "workers": list(summary.results), "errors": summary.errors}))
            return 0 if not summary.errors else 1
        if args.command == "show-run":
            run = runtime.runs.get(args.run_id)
            if run is None:
                raise KeyError("run not found")
            print(json.dumps({"run_id": str(run.id), "source_id": str(run.source_id),
                              "status": run.status.value, "stored_count": run.stored_count,
                              "error_summary": run.error_summary}))
            return 0
        document = runtime.documents.get(args.document_id)
        if document is None:
            raise KeyError("document not found")
        print(json.dumps({"document_id": str(document.id), "run_id": str(document.run_id),
                          "source_id": str(document.source_id), "content_ref": document.content_ref,
                          "content_sha256": document.content_sha256}))
        return 0
    except Exception as error:
        # API/transport exceptions can contain request metadata; never echo them to stdout/stderr.
        if isinstance(error, RuntimeError) and str(error).startswith("missing AGENT_SUPABASE"):
            print("missing Agent Supabase environment variables; run config-check", file=sys.stderr)
        else:
            print(f"operation failed ({type(error).__name__}); inspect private logs", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
