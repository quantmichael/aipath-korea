"""Evidence requirements for metadata leads, independent of Source permission.

Service slugs are explicit. A broad Agent vertical is never guessed to be a
narrower Service category such as ``growth-program``.
"""

from dataclasses import dataclass

from agent_collect.schemas.models import Vertical


BASE_REQUIRED = (
    "title", "summary", "organizer", "official_url", "application_start",
    "application_deadline", "application_method", "category",
)
EVENT_FIELDS = ("event_start", "event_end")


@dataclass(frozen=True, slots=True)
class CategoryEvidencePolicy:
    category: Vertical
    event_window_required: bool
    service_slug: str | None = None

    @property
    def publish_gate_supported(self) -> bool:
        return self.service_slug is not None

    @property
    def required_fields(self) -> tuple[str, ...]:
        return BASE_REQUIRED + (EVENT_FIELDS if self.event_window_required else ())


_POLICIES = {
    Vertical.CONFERENCE: CategoryEvidencePolicy(Vertical.CONFERENCE, True, "conference"),
    Vertical.EXHIBITION: CategoryEvidencePolicy(Vertical.EXHIBITION, True),
    Vertical.COMPETITION: CategoryEvidencePolicy(Vertical.COMPETITION, False, "competition"),
    Vertical.HACKATHON: CategoryEvidencePolicy(Vertical.HACKATHON, True, "hackathon"),
    Vertical.EDUCATION: CategoryEvidencePolicy(Vertical.EDUCATION, True, "education"),
    Vertical.RESEARCH: CategoryEvidencePolicy(Vertical.RESEARCH, False),
    Vertical.GRANT: CategoryEvidencePolicy(Vertical.GRANT, False),
    Vertical.PROGRAM: CategoryEvidencePolicy(Vertical.PROGRAM, False),
    Vertical.MEETUP: CategoryEvidencePolicy(Vertical.MEETUP, True, "meetup"),
    Vertical.JOB: CategoryEvidencePolicy(Vertical.JOB, False),
}


def category_policy(value: str | None) -> CategoryEvidencePolicy | None:
    if not isinstance(value, str):
        return None
    try:
        return _POLICIES.get(Vertical(value.strip().casefold()))
    except ValueError:
        return None
