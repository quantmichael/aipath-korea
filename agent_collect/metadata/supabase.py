"""Supabase bridge for metadata evidence and narrow operational RPCs.

This bridge never fetches a website or enqueues a publish. A caller must
supply a transient page and matching policy document after separately
authorized access. Candidate writes use only the Migration 015 RPCs.
Operational approvals require authenticated, append-only approval events.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from agent_collect.adapters.service_db.duplicate_lookup import SupabaseServiceDuplicateLookup
from agent_collect.discovery.quality import PageType
from agent_collect.discovery.followup import FollowupLead
from agent_collect.metadata.evidence import (
    DuplicateSnapshot, EvidenceType, MetadataAccessPolicy, MetadataCandidate,
    MetadataEvidenceResolver, MetadataFact, MetadataOutcome,
)
from agent_collect.metadata.extraction import extract_labelled_metadata
from agent_collect.quality_gate.models import DuplicateRecord
from agent_collect.repositories.quality_supabase import SupabaseQualityRepository
from agent_collect.repositories.supabase import SupabaseSourceRepository


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("metadata timestamp lacks timezone")
    return parsed


def _one(query: Any) -> dict[str, Any] | None:
    return next(iter(query.limit(1).execute().data or []), None)


@dataclass(frozen=True, slots=True)
class MetadataLeadResult:
    """No stored ID means the lead was held before any database write."""

    stored_candidate_id: UUID | None
    outcome: MetadataOutcome


class SupabaseMetadataEvidence:
    def __init__(self, client: Any, *, max_records: int = 500) -> None:
        if max_records <= 0:
            raise ValueError("max_records must be positive")
        self.client = client
        self.max_records = max_records
        self.sources = SupabaseSourceRepository(client)
        self.resolver = MetadataEvidenceResolver()

    def _candidate(self, row: dict[str, Any]) -> MetadataCandidate:
        evidence = row.get("field_evidence") or {}
        fields = {}
        for name, value in (row.get("facts") or {}).items():
            item = evidence.get(name) or {}
            try:
                fields[name] = MetadataFact(
                    str(value), str(item.get("source_url") or ""),
                    EvidenceType(item.get("evidence_type")),
                    str(item.get("evidence_reference") or ""),
                    str(item.get("quote") or ""),
                    _time(item["observed_at"]), float(item.get("confidence") or 0),
                    str(item.get("access_policy_digest") or ""),
                )
            except (KeyError, TypeError, ValueError):
                # A malformed field is missing evidence, never trusted data.
                continue
        return MetadataCandidate(
            UUID(row["id"]), UUID(row["source_id"]), row["official_url"],
            fields, _time(row["discovered_at"]), row["cohort"],
        )

    def _policy(self, row: dict[str, Any], *, cohort: str) -> MetadataAccessPolicy | None:
        principal = event_id = revoked_at = None
        if cohort == "operational":
            try:
                response = (self.client.table("agent_metadata_approval_events")
                            .select("id,action,actor_uid,acted_at", count="exact")
                            .eq("approval_id", row["id"])
                            .order("acted_at").limit(self.max_records + 1).execute())
                events = response.data or []
                if (response.count is None or response.count != len(events) or
                        len(events) > self.max_records):
                    return None
                approved = next((event for event in events
                                 if event["action"] == "APPROVED"), None)
                if approved is None or not approved.get("actor_uid"):
                    return None
                principal = UUID(approved["actor_uid"])
                event_id = UUID(approved["id"])
                revocations = [event for event in events if event["action"] == "REVOKED"]
                if revocations:
                    revoked_at = _time(revocations[-1]["acted_at"])
            except Exception:
                # Migration 015 missing or an inaccessible audit is a hold.
                return None
        try:
            return MetadataAccessPolicy(
                UUID(row["source_id"]), row["official_url"], row["reviewer"],
                _time(row["reviewed_at"]), _time(row["expires_at"]),
                row["permission_url"], row["permission_digest"],
                row["organizer_relationship"], row["approved_organizer"],
                PageType(row["page_type"]), row["officiality_confirmed"],
                row["public_read"], row["automated_access"], row["fact_use"],
                row["recheck_allowed"], row["raw_storage_allowed"],
                row["redistribution_allowed"], row["scope_origin"],
                row["path_prefix"], principal, event_id, revoked_at,
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _duplicates(self, *, now: datetime) -> DuplicateSnapshot:
        agent_records: list[DuplicateRecord] = []
        service_records: list[DuplicateRecord] = []
        agent_complete = service_complete = False
        try:
            agent_records = SupabaseQualityRepository(
                self.client, max_records=self.max_records).list_candidate_records()
            response = (self.client.table("agent_metadata_candidates")
                        .select("id,status,official_url,facts", count="exact")
                        .limit(self.max_records + 1).execute())
            rows = response.data or []
            if (response.count is None or response.count != len(rows) or
                    len(rows) > self.max_records):
                raise RuntimeError("metadata duplicate scan incomplete")
            for row in rows:
                if row["status"] == "REJECTED":
                    continue
                facts = row.get("facts") or {}
                agent_records.append(DuplicateRecord(
                    row["id"], "agent", facts.get("title"),
                    facts.get("organizer"), row.get("official_url"), None,
                    row.get("official_url"), facts.get("event_start"),
                ))
            agent_complete = True
        except Exception:
            agent_complete = False
        try:
            service_records = SupabaseServiceDuplicateLookup(
                self.client, max_records=self.max_records).list_records()
            service_complete = True
        except Exception:
            service_complete = False
        return DuplicateSnapshot(tuple(agent_records), tuple(service_records), now,
                                 agent_complete, service_complete)

    def assess(self, candidate_id: UUID, *, page: bytes | None,
               page_url: str | None, fetched_at: datetime | None,
               permission_document: bytes | None,
               permission_checked_at: datetime | None,
               now: datetime | None = None) -> MetadataOutcome:
        now = now or datetime.now(timezone.utc)
        row = _one(self.client.table("agent_metadata_candidates")
                   .select("*").eq("id", str(candidate_id)))
        if row is None:
            raise KeyError("unknown metadata candidate")
        candidate = self._candidate(row)
        source = self.sources.get(candidate.source_id)
        approval = _one(self.client.table("agent_metadata_source_approvals")
                        .select("*").eq("id", row["approval_id"]))
        policy = (self._policy(approval, cohort=candidate.cohort)
                  if approval and approval["source_id"] == row["source_id"] and
                  approval["official_url"] == candidate.official_url and
                  approval["cohort"] == candidate.cohort else None)
        # No page is replayed unless the current approval is valid. The
        # resolver independently enforces scope, expiry, revocation, and hash.
        if policy is None or not policy.permits(source, candidate.official_url, now=now)[0]:
            page = None
        return self.resolver.assess(
            candidate, source=source, policy=policy, page=page,
            page_url=page_url, fetched_at=fetched_at,
            permission_document=permission_document,
            permission_checked_at=permission_checked_at,
            duplicates=self._duplicates(now=now), now=now,
        )

    def create_candidate(self, *, request_id: UUID, approval_id: UUID,
                         facts: dict[str, str],
                         field_evidence: dict[str, dict[str, Any]],
                         discovered_at: datetime) -> UUID:
        """Create a held operational Candidate through Migration 015 only.

        This does not extract facts, fetch a page, verify, or enqueue. The
        database checks current approval and deduplicates by URL and period.
        """
        if discovered_at.tzinfo is None:
            raise ValueError("discovered_at must be timezone-aware")
        if not facts or not field_evidence:
            raise ValueError("metadata facts and evidence required")
        result = self.client.rpc("agent_metadata_create_candidate", {
            "p_request_id": str(request_id),
            "p_approval_id": str(approval_id),
            "p_facts": facts,
            "p_field_evidence": field_evidence,
            "p_discovered_at": discovered_at.isoformat(),
        }).execute()
        return UUID(result.data)

    def process_followup_lead(self, *, lead: FollowupLead, approval_id: UUID,
                              facts: dict[str, MetadataFact] | None, page: bytes | None,
                              fetched_at: datetime | None,
                              permission_document: bytes | None,
                              permission_checked_at: datetime | None,
                              request_id: UUID, decision_id: UUID,
                              now: datetime | None = None) -> MetadataLeadResult:
        """Handoff of a reviewed individual Deep Search lead.

        If facts are omitted, extract only explicit labels from the supplied
        transient page after checking the current authenticated policy.
        Search snippets and lead date hints are never promoted into verified
        facts. No Candidate is written unless replay and the Gate both pass.
        """
        now = now or datetime.now(timezone.utc)
        if (now.tzinfo is None or lead.page_type != PageType.INDIVIDUAL_NOTICE.value):
            raise ValueError("reviewed individual lead and aware assessment time required")
        approval = _one(self.client.table("agent_metadata_source_approvals")
                        .select("*").eq("id", str(approval_id)))
        if approval is None or approval.get("cohort") != "operational":
            raise ValueError("operational metadata approval required")
        source = self.sources.get(UUID(approval["source_id"]))
        policy = self._policy(approval, cohort="operational")
        if (policy is None or lead.url != approval["official_url"] or
                source is None or policy.approved_principal_id is None or
                policy.approval_event_id is None or
                not policy.permits(source, lead.url, now=now)[0]):
            raise ValueError("current authenticated metadata approval required")
        if facts is None:
            if (page is None or fetched_at is None or permission_document is None or
                    permission_checked_at is None):
                raise ValueError("fresh page and permission recheck required")
            facts = extract_labelled_metadata(
                source=source, policy=policy, page=page, page_url=lead.url,
                fetched_at=fetched_at, permission_document=permission_document,
                permission_checked_at=permission_checked_at, now=now)
        candidate = MetadataCandidate(uuid4(), source.id, lead.url, facts,
                                      now, "operational")
        outcome = self.resolver.assess(
            candidate, source=source, policy=policy, page=page,
            page_url=lead.url, fetched_at=fetched_at,
            permission_document=permission_document,
            permission_checked_at=permission_checked_at,
            duplicates=self._duplicates(now=now), now=now,
        )
        if outcome.verification.status != "VERIFIED" or outcome.gate.status.value != "PUBLISH_READY":
            return MetadataLeadResult(None, outcome)
        field_evidence = {
            name: {
                "source_url": fact.source_url,
                "evidence_type": fact.evidence_type.value,
                "evidence_reference": fact.evidence_reference,
                "quote": fact.quote,
                "observed_at": fact.observed_at.isoformat(),
                "confidence": fact.confidence,
                "access_policy_digest": fact.access_policy_digest,
            } for name, fact in facts.items()
        }
        stored_id = self.create_candidate(
            request_id=request_id, approval_id=approval_id,
            facts={name: fact.value for name, fact in facts.items()},
            field_evidence=field_evidence, discovered_at=now,
        )
        recorded = self.assess_and_record(
            stored_id, decision_id=decision_id, page=page,
            page_url=lead.url, fetched_at=fetched_at,
            permission_document=permission_document,
            permission_checked_at=permission_checked_at, now=now,
        )
        return MetadataLeadResult(stored_id, recorded)

    def assess_and_record(self, candidate_id: UUID, *, decision_id: UUID,
                          page: bytes | None, page_url: str | None,
                          fetched_at: datetime | None,
                          permission_document: bytes | None,
                          permission_checked_at: datetime | None,
                          now: datetime | None = None) -> MetadataOutcome:
        """Reassess current policy, then request one audited state transition.

        Migration 015 rechecks approval and Source under DB locks at commit;
        the returned outcome alone never authorizes Service publishing.
        """
        now = now or datetime.now(timezone.utc)
        row = _one(self.client.table("agent_metadata_candidates")
                   .select("id,status,cohort").eq("id", str(candidate_id)))
        if row is None or row["cohort"] != "operational":
            raise ValueError("operational metadata Candidate required")
        outcome = self.assess(
            candidate_id, page=page, page_url=page_url, fetched_at=fetched_at,
            permission_document=permission_document,
            permission_checked_at=permission_checked_at, now=now,
        )
        self.client.rpc("agent_metadata_record_assessment", {
            "p_decision_id": str(decision_id),
            "p_candidate_id": str(candidate_id),
            "p_expected_status": row["status"],
            "p_status": outcome.gate.status.value,
            "p_verification": {
                "status": outcome.verification.status,
                "reasons": list(outcome.reasons),
                "evidence_refs": list(outcome.verification.evidence_refs),
                "time_precision": outcome.precision.value,
            },
            "p_duplicate": {
                "status": outcome.duplicate.status.value,
                "match_type": outcome.duplicate.match_type,
            },
            "p_gate": {
                "status": outcome.gate.status.value,
                "reasons": list(outcome.gate.reasons),
            },
            "p_verified_at": now.isoformat(),
        }).execute()
        return outcome
