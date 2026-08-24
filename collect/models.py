from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


CollectionMethod = Literal[
    "manual",
    "api",
    "rss",
    "html",
    "ai_extract",
    "ai_search",
    "submission",
]

CandidateStatus = Literal[
    "pending",
    "needs_review",
    "verified",
    "rejected",
    "promoted",
    "duplicate",
]


@dataclass
class Source:
    id: str
    name: str
    homepage_url: str | None
    collection_url: str | None
    collection_method: CollectionMethod
    source_type: str = "regular"


@dataclass
class OpportunityCandidate:
    source_id: str | None = None
    category_slug: str | None = None
    category_id: str | None = None
    external_id: str | None = None
    title: str | None = None
    slug: str | None = None
    summary: str | None = None
    description: str | None = None
    organizer: str | None = None
    target_audience: str | None = None
    difficulty: str | None = None
    format: str | None = None
    region: str | None = None
    venue: str | None = None
    price_type: str | None = "unknown"
    price_text: str | None = None
    application_start_at: str | None = None
    application_deadline_at: str | None = None
    event_start_at: str | None = None
    event_end_at: str | None = None
    official_url: str | None = None
    image_url: str | None = None
    candidate_status: CandidateStatus = "pending"
    validation_errors: list[str] = field(default_factory=list)
    raw_payload: dict[str, Any] = field(default_factory=dict)
    last_verified_at: str | None = None

    def to_record(self, collection_run_id: str | None = None) -> dict[str, Any]:
        record = asdict(self)
        record.pop("category_slug", None)
        record["collection_run_id"] = collection_run_id
        return record


@dataclass
class CollectionRunSummary:
    source_id: str | None = None
    collection_method: str = "manual"
    status: str = "success"
    found_count: int = 0
    inserted_count: int = 0
    updated_count: int = 0
    duplicate_count: int = 0
    failed_count: int = 0
    error_message: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None


@dataclass
class CollectionResult:
    runs: list[CollectionRunSummary] = field(default_factory=list)

    @property
    def found_count(self) -> int:
        return sum(run.found_count for run in self.runs)

    @property
    def inserted_count(self) -> int:
        return sum(run.inserted_count for run in self.runs)

    @property
    def updated_count(self) -> int:
        return sum(run.updated_count for run in self.runs)

    @property
    def duplicate_count(self) -> int:
        return sum(run.duplicate_count for run in self.runs)

    @property
    def failed_count(self) -> int:
        return sum(run.failed_count for run in self.runs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_count": len(self.runs),
            "found_count": self.found_count,
            "inserted_count": self.inserted_count,
            "updated_count": self.updated_count,
            "duplicate_count": self.duplicate_count,
            "failed_count": self.failed_count,
            "runs": [
                {
                    **asdict(run),
                    "started_at": run.started_at.isoformat(),
                    "finished_at": (
                        run.finished_at.isoformat()
                        if run.finished_at
                        else None
                    ),
                }
                for run in self.runs
            ],
        }


@dataclass
class PromotionResult:
    selected_count: int = 0
    promoted_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    promoted_candidates: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
