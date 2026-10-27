"""File contracts and deterministic gates. No network calls or publication authority."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

SECTIONS = {
    "ai_now": ("ai-now", "AI NOW", "AI 소식과 변화"),
    "ai_tips": ("ai-tips", "AI TIPS", "따라 하는 AI 활용법"),
    "prompt_lab": ("prompt-lab", "PROMPT LAB", "복사해서 쓰는 프롬프트"),
}
OPERATIONS = {"access", "metadata", "excerpt", "derive", "redistribute", "llm_input"}
CHECKS = {"rights", "facts", "tests", "privacy", "rendering", "freshness"}
TARGET = "local-fixture-only"


def utcnow():
    return datetime.now(timezone.utc)


def instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("UTC timestamp with timezone required")
    return parsed


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def https_url(value: str | None):
    if value is None:
        return
    url = urlsplit(value)
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise ValueError("HTTPS URL without credentials required")


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def dates(self):
        for name in type(self).model_fields:
            value = getattr(self, name)
            if name.endswith("_at") and value is not None:
                instant(value)
        return self


class RightsRecord(Contract):
    operations: dict[str, Literal["pending", "allowed", "denied"]]
    evidence_refs: list[str]
    scope_note: str
    license_id: str | None = None
    attribution_requirements: list[str]
    reviewed_by: str | None
    reviewed_at: str | None
    review_due_at: str | None
    retention_rule: str
    revoked_at: str | None = None
    revocation_reason: str | None = None


class Source(Contract):
    id: str
    name: str
    kind: Literal["official_blog", "github", "reddit", "other", "owned"]
    base_url: str | None
    access_method: Literal["manual", "api", "rss", "owned"]
    enabled: bool = False
    rights: RightsRecord


class SourceItem(Contract):
    id: str
    source_id: str
    canonical_url: str | None
    owned_record_id: str | None
    title: str | None
    candidate_note: str | None = Field(default=None, max_length=300)
    author: str | None
    source_published_at: str | None
    retrieved_at: str | None
    status: Literal["candidate", "held", "accepted", "rejected"]
    rights: RightsRecord
    hold_reason: str | None = None


class Content(Contract):
    id: str
    section: Literal["ai_now", "ai_tips", "prompt_lab"]
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    publication_status: Literal["unpublished", "published", "withdrawn"] = "unpublished"
    public_revision_id: str | None = None
    published_at: str | None = None
    updated_at: str | None = None
    withdrawn_at: str | None = None
    withdrawal_reason: str | None = None


class Claim(Contract):
    id: str
    text: str
    kind: Literal["fact", "vendor_claim", "inference", "experiment"]
    evidence_ids: list[str]
    verification_status: Literal["unverified", "verified", "rejected"]
    verified_by: str | None
    verified_at: str | None


class Revision(Contract):
    id: str
    content_id: str
    revision_number: int = Field(ge=1)
    status: Literal["draft", "verifying", "in_review", "approved", "rejected", "invalidated"] = "draft"
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    body_markdown: str = Field(min_length=1)
    ai_assisted: bool
    is_fixture: bool = True
    claims: list[Claim]
    evidence_ids: list[str]
    verified_at: str | None
    next_review_at: str | None
    related_content_ids: list[str] = Field(max_length=2)
    section_payload: dict
    correction_note: str | None = None
    content_hash: str
    created_at: str
    created_by: str


class Attribution(Contract):
    title: str
    author: str | None
    url: str | None
    license: str | None
    changes: str | None


class Evidence(Contract):
    id: str
    revision_id: str
    source_item_id: str
    claim_ids: list[str]
    locator: str
    verification_note: str
    excerpt: str | None = None
    required_operations: list[str]
    public_attribution: Attribution
    checked_by: str
    checked_at: str


class Review(Contract):
    id: str
    revision_id: str
    reviewer_id: str
    decision: Literal["approve", "reject"]
    reviewed_content_hash: str
    reviewed_evidence_hash: str
    publication_target_id: str
    checklist: dict[str, bool]
    reason: str
    reviewed_at: str


class Bundle(Contract):
    sources: list[Source]
    source_items: list[SourceItem]
    contents: list[Content]
    revisions: list[Revision]
    evidence: list[Evidence]
    reviews: list[Review]
    audit_events: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def relationships(self):
        for name in ("sources", "source_items", "contents", "revisions", "evidence", "reviews"):
            ids = [v.id for v in getattr(self, name)]
            if len(ids) != len(set(ids)):
                raise ValueError(f"{name}: duplicate ID")
        cs, rs, ss, items, es = ({v.id: v for v in getattr(self, name)} for name in
                               ("contents", "revisions", "sources", "source_items", "evidence"))
        slugs = [(c.section, c.slug) for c in self.contents]
        if len(slugs) != len(set(slugs)):
            raise ValueError("duplicate section/slug")
        versions = [(r.content_id, r.revision_number) for r in self.revisions]
        if len(versions) != len(set(versions)):
            raise ValueError("duplicate revision number")
        for s in self.sources:
            https_url(s.base_url)
            if s.kind != "owned" and not s.base_url:
                raise ValueError(f"{s.id}: external source URL missing")
        for item in self.source_items:
            if item.source_id not in ss:
                raise ValueError(f"{item.id}: missing source")
            https_url(item.canonical_url)
            if ss[item.source_id].kind == "owned":
                if not item.owned_record_id:
                    raise ValueError(f"{item.id}: owned record missing")
            elif not item.canonical_url:
                raise ValueError(f"{item.id}: external item URL missing")
            if item.status in {"held", "rejected"} and not item.hold_reason:
                raise ValueError(f"{item.id}: hold reason missing")
        keys = [(i.source_id, i.canonical_url or i.owned_record_id) for i in self.source_items]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate source item")
        for c in self.contents:
            if c.publication_status == "published":
                r = rs.get(c.public_revision_id)
                if not r or r.content_id != c.id or r.status != "approved":
                    raise ValueError(f"{c.id}: invalid public pointer")
                if not c.published_at or not c.updated_at:
                    raise ValueError(f"{c.id}: publication dates missing")
            elif c.public_revision_id is not None:
                raise ValueError(f"{c.id}: nonpublic pointer must be null")
        for r in self.revisions:
            if r.content_id not in cs or any(i not in cs for i in r.related_content_ids):
                raise ValueError(f"{r.id}: missing content reference")
            claims = {x.id for x in r.claims}
            if len(claims) != len(r.claims):
                raise ValueError(f"{r.id}: duplicate claim")
            for eid in r.evidence_ids:
                if eid not in es or es[eid].revision_id != r.id:
                    raise ValueError(f"{r.id}: missing/mismatched evidence {eid}")
            for claim in r.claims:
                if any(eid not in r.evidence_ids or claim.id not in es[eid].claim_ids
                       for eid in claim.evidence_ids):
                    raise ValueError(f"{claim.id}: missing claim/evidence link")
        for e in self.evidence:
            if e.revision_id not in rs or e.source_item_id not in items:
                raise ValueError(f"{e.id}: missing reference")
            if any(cid not in {c.id for c in rs[e.revision_id].claims} for cid in e.claim_ids):
                raise ValueError(f"{e.id}: missing claim reference")
            https_url(e.public_attribution.url)
            item = items[e.source_item_id]
            if e.public_attribution.url != item.canonical_url:
                raise ValueError(f"{e.id}: attribution URL does not match source item")
        for review in self.reviews:
            if review.revision_id not in rs:
                raise ValueError(f"{review.id}: missing revision")
        return self


def content_hash(c: Content, r: Revision):
    data = r.model_dump(exclude={"status", "content_hash", "created_at", "created_by"})
    return digest({"section": c.section, "slug": c.slug, "revision": data})


def evidence_hash(b: Bundle, r: Revision):
    evidence = sorted((e for e in b.evidence if e.id in r.evidence_ids), key=lambda e: e.id)
    items = sorted((i for i in b.source_items if i.id in {e.source_item_id for e in evidence}), key=lambda i: i.id)
    sources = sorted((s for s in b.sources if s.id in {i.source_id for i in items}), key=lambda s: s.id)
    return digest([v.model_dump() for v in [*evidence, *items, *sources]])


class GateError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise GateError(message)


def rights_gate(record: RightsRecord, operations: set[str], now: datetime, label: str):
    require(not record.revoked_at, f"{label}: rights revoked")
    require(record.review_due_at and instant(record.review_due_at) > now, f"{label}: rights expired")
    require(record.evidence_refs and record.scope_note and record.retention_rule and
            record.reviewed_by and record.reviewed_at and instant(record.reviewed_at) <= now,
            f"{label}: rights evidence missing/future")
    for operation in operations:
        require(record.operations.get(operation, "pending") == "allowed", f"{label}: {operation} not allowed")


def payload_gate(section: str, payload: dict):
    fields = {
        "ai_now": ["what_changed", "why_it_matters", "who_is_affected", "availability", "limitations", "next_action"],
        "ai_tips": ["task", "prerequisites", "steps", "example_input", "example_result", "troubleshooting", "test_record"],
        "prompt_lab": ["purpose", "not_for", "template", "variables", "example_input", "example_output", "test_records", "limitations"],
    }[section]
    require(set(payload) == set(fields), f"{section}: section fields mismatch")
    for name in fields:
        require(bool(payload[name]), f"{section}: {name} missing")
    list_fields = {"prerequisites", "steps", "troubleshooting", "not_for", "limitations"} if section != "ai_now" else set()
    for key in list_fields & payload.keys():
        require(isinstance(payload[key], list) and all(isinstance(v, str) and v for v in payload[key]), f"{key}: string list required")
    for key in set(fields) - list_fields - {"variables", "test_record", "test_records"}:
        require(isinstance(payload[key], str), f"{key}: string required")
    records = []
    if section == "ai_tips":
        require(3 <= len(payload["steps"]) <= 7, "tips: 3–7 steps required")
        records = [payload["test_record"]]
    if section == "prompt_lab":
        variables = payload["variables"]
        require(isinstance(variables, list) and all(isinstance(v, dict) for v in variables), "variables: list required")
        for v in variables:
            require(set(v) == {"name", "description", "required", "example"} and
                    all(isinstance(v[k], str) and v[k] for k in ("name", "description", "example")) and
                    isinstance(v["required"], bool), "variable schema invalid")
        names = [v["name"] for v in variables]
        require(len(names) == len(set(names)) and set(re.findall(r"\{\{([^{}]+)\}\}", payload["template"])) == set(names), "prompt: variables mismatch")
        records = payload["test_records"]
        require(isinstance(records, list) and len(records) >= 2, "prompt: two test records required")
    record_keys = {"tested_at", "tester_id", "tool_name", "model_version", "plan_or_environment", "input_summary", "observed_result", "limitations"}
    for record in records:
        require(isinstance(record, dict) and set(record) == record_keys and all(isinstance(v, str) and v for v in record.values()), "test record incomplete")
        instant(record["tested_at"])
    if section == "prompt_lab":
        require(len({r["input_summary"] for r in records}) >= 2, "prompt: distinct inputs required")


def validation_gate(b: Bundle, c: Content, r: Revision, now: datetime, *, fresh: bool):
    require(r.content_id == c.id, "content/revision mismatch")
    require(r.content_hash == content_hash(c, r), f"{r.id}: content hash mismatch")
    require(r.verified_at and instant(r.verified_at) <= now and r.next_review_at, f"{r.id}: verification date invalid")
    if fresh:
        require(instant(r.next_review_at) > now, f"{r.id}: content review overdue")
    payload_gate(c.section, r.section_payload)
    require(r.claims and r.evidence_ids, f"{r.id}: claims/evidence required")
    for claim in r.claims:
        require(claim.verification_status == "verified" and claim.verified_by and claim.verified_at and
                instant(claim.verified_at) <= now and claim.evidence_ids,
                f"{claim.id}: verified claim evidence/reviewer required")
    for eid in r.evidence_ids:
        e = next(e for e in b.evidence if e.id == eid)
        item = next(i for i in b.source_items if i.id == e.source_item_id)
        source = next(s for s in b.sources if s.id == item.source_id)
        require(source.enabled and item.status == "accepted", f"{eid}: source/item unavailable")
        require(e.checked_by and e.locator and e.verification_note and instant(e.checked_at) <= now,
                f"{eid}: verification evidence missing/future")
        operations = set(e.required_operations)
        require(operations <= OPERATIONS and {"access", "metadata", "derive"} <= operations,
                f"{eid}: operation plan incomplete")
        if r.ai_assisted and not r.is_fixture:
            require("llm_input" in operations, f"{eid}: AI-assisted real content requires llm_input rights")
        if e.excerpt:
            require({"excerpt", "redistribute"} <= operations, f"{eid}: excerpt rights missing")
        rights_gate(source.rights, operations, now, source.id)
        rights_gate(item.rights, operations, now, item.id)


def exposure_gate(b: Bundle, c: Content, r: Revision, now: datetime, *, demo: bool):
    require(demo and r.is_fixture, "This build serves fixtures only at /demo; real publication is disabled")
    require(c.publication_status == "published" and c.public_revision_id == r.id and r.status == "approved", "not an approved public pointer")
    validation_gate(b, c, r, now, fresh=False)
    reviews = [v for v in b.reviews if v.revision_id == r.id]
    require(bool(reviews), "approval missing")
    review = reviews[-1]
    require(review.decision == "approve" and review.reviewer_id == "fixture-user" and
            review.publication_target_id == TARGET and review.reviewed_content_hash == r.content_hash and
            review.reviewed_evidence_hash == evidence_hash(b, r) and
            all(review.checklist.get(k) is True for k in CHECKS), "approval hash/target/checklist mismatch")


def pilot_preview_gate(b: Bundle, c: Content, r: Revision, now: datetime):
    """Allow a real draft to be viewed locally for review, without publication authority."""
    require(not r.is_fixture and c.publication_status == "unpublished" and c.public_revision_id is None,
            "Pilot preview requires an unpublished real draft")
    require(r.status in {"draft", "verifying", "in_review"}, "Pilot preview requires an unapproved revision")
    validation_gate(b, c, r, now, fresh=True)
    evidence_ids = set(r.evidence_ids)
    item_ids = {record.source_item_id for record in b.evidence if record.id in evidence_ids}
    source_ids = {item.source_id for item in b.source_items if item.id in item_ids}
    rights = [source.rights for source in b.sources if source.id in source_ids]
    rights += [item.rights for item in b.source_items if item.id in item_ids]
    require(all(not ref.startswith("fixture:") for record in rights for ref in record.evidence_refs),
            "Fixture rights cannot support real-content preview")


def projection(b: Bundle, c: Content, r: Revision, now: datetime, *, prefix="/demo"):
    """Explicit allowlist; never pass operational objects into templates."""
    public_fields = {
        "ai_now": ("what_changed", "why_it_matters", "who_is_affected", "availability", "limitations", "next_action"),
        "ai_tips": ("task", "prerequisites", "steps", "example_input", "example_result", "troubleshooting"),
        "prompt_lab": ("purpose", "not_for", "template", "variables", "example_input", "example_output", "limitations"),
    }[c.section]
    payload = {key: r.section_payload[key] for key in public_fields}
    records = r.section_payload.get("test_records", [r.section_payload.get("test_record")])
    payload["tests"] = [{k: record[k] for k in ("tested_at", "tool_name", "model_version", "plan_or_environment", "input_summary", "observed_result", "limitations")} for record in records if record]
    sources = []
    for e in b.evidence:
        if e.id in r.evidence_ids:
            item = next(i for i in b.source_items if i.id == e.source_item_id)
            sources.append({**e.public_attribution.model_dump(), "source_published_at": item.source_published_at})
    return {"id": c.id, "section": c.section, "path": prefix + "/" + SECTIONS[c.section][0] + "/" + c.slug,
            "title": r.title, "summary": r.summary, "body_markdown": r.body_markdown,
            "published_at": c.published_at, "updated_at": c.updated_at, "verified_at": r.verified_at,
            "ai_assisted": r.ai_assisted, "correction_note": r.correction_note,
            "stale": instant(r.next_review_at) <= now, "section_payload": payload,
            "related_content_ids": r.related_content_ids, "sources": sources}
