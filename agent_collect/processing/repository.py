"""Processing queue port; all state mutations must be atomic in the DB port."""

from typing import Protocol
from uuid import UUID

from agent_collect.processing.models import (
    ProcessingClaim, ProcessingJob, ProcessingPolicy, ProcessingStatus, ProcessingSummary,
)


class ProcessingRepository(Protocol):
    def preview(self, version: str, ref_prefix: str, limit: int) -> list[ProcessingJob]: ...
    def seed(self, version: str, ref_prefix: str, limit: int) -> int: ...
    def list_due(self, version: str, ref_prefix: str, limit: int) -> list[ProcessingJob]: ...
    def claim(self, job_id: UUID, *, worker_id: str, claim_id: UUID,
              policy: ProcessingPolicy) -> ProcessingClaim | None: ...
    def checkpoint(self, claim: ProcessingClaim, *, candidate_id: UUID,
                   verification_id: UUID | None = None,
                   gate_status: str | None = None) -> None: ...
    def complete(self, claim: ProcessingClaim, *, outcome: ProcessingStatus,
                 policy: ProcessingPolicy, candidate_id: UUID | None = None,
                 verification_id: UUID | None = None, gate_status: str | None = None,
                 error_code: str | None = None) -> ProcessingStatus: ...
    def record_summary(self, summary: ProcessingSummary) -> None: ...
