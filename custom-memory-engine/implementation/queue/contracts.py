"""Typed work identity and content-free queue outcomes."""

from datetime import UTC, datetime
from typing import Literal

from psycopg.errors import InvalidParameterValue, UniqueViolation
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from implementation.clock import ClockTick


class QueueError(InvalidParameterValue):
    """A static queue contract error with SQLSTATE 22023."""


class JobIdentityConflict(UniqueViolation):
    """An idempotency identity already belongs to different work."""


class Record(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)


class NewJob(Record):
    id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    turn_id: str | None = None
    payload: JsonValue | None = None
    extractor_version: str | None = None
    candidate_fingerprint: str | None = None
    not_before: AwareDatetime | None = None

    @field_validator(
        "id", "user_id", "kind", "idempotency_key", "turn_id", "extractor_version", "candidate_fingerprint"
    )
    @classmethod
    def explicit_text(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or "\x00" in value):
            raise ValueError("nonblank_identity_required")
        return value

    @field_validator("not_before")
    @classmethod
    def normalize_schedule(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def complete_extraction_identity(self) -> "NewJob":
        if (self.extractor_version is not None or self.candidate_fingerprint is not None) and not (
            self.turn_id and self.extractor_version and self.candidate_fingerprint
        ):
            raise ValueError("complete_extraction_identity_required")
        return self


class Job(Record):
    id: str
    user_id: str
    kind: str
    turn_id: str | None
    idempotency_key: str
    status: Literal["queued", "running", "done", "dead_letter"]
    attempts: int = Field(ge=0)
    next_run_at: AwareDatetime
    payload: JsonValue | None
    extractor_version: str | None
    candidate_fingerprint: str | None


class EnqueueResult(Record):
    job: Job
    created: bool


class WorkerResult(Record):
    status: Literal["idle", "done", "dead_letter"]
    job_id: str | None = None
    attempts: int = 0
    transaction_attempts: int = 0
    reason: str | None = None
    clock: ClockTick | None = None
