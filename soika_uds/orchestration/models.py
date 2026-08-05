"""Persistent orchestration models for the SOIKA processing pipeline."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any

from ..contracts import JobStatus
from ..integration import (
    AnalysisRequestV1,
    ContractIssue,
    ContractValidationError,
    JobStatusV1,
)


class OrchestrationError(RuntimeError):
    """Base class for recoverable orchestration infrastructure errors."""


class JobNotFoundError(OrchestrationError):
    """Raised when a persisted job does not exist."""


class ConcurrentUpdateError(OrchestrationError):
    """Raised when optimistic revision validation fails."""


class JobLeaseError(OrchestrationError):
    """Raised when another worker owns a live job lease."""


class MissingStageHandlerError(OrchestrationError):
    """Raised when a pipeline stage has no registered handler."""


class InvalidStageOutputError(OrchestrationError):
    """Raised when a stage returns a non-JSON or inconsistent result."""


class StageExecutionError(OrchestrationError):
    """Structured stage failure that may be eligible for retry."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = dict(details or {})


class RetryableStageError(StageExecutionError):
    """Stage failure that may be retried according to its policy."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, retryable=True, details=details)


class PermanentStageError(StageExecutionError):
    """Stage failure that must terminate the current job attempt."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, retryable=False, details=details)


class PipelineStage(str, Enum):
    """Ordered, durable stages of one SOIKA analysis job."""

    PREPARING = "preparing"
    COLLECTION = "collection"
    PREPROCESSING = "preprocessing"
    NLP = "nlp"
    GEOLOCATION = "geolocation"
    EVENTS = "events"
    SCORING = "scoring"
    FINALIZING = "finalizing"


PIPELINE_STAGES = tuple(PipelineStage)

_STAGE_STATUS: Mapping[PipelineStage, JobStatus] = MappingProxyType(
    {
        PipelineStage.PREPARING: JobStatus.PREPARING,
        PipelineStage.COLLECTION: JobStatus.COLLECTING,
        PipelineStage.PREPROCESSING: JobStatus.PREPROCESSING,
        PipelineStage.NLP: JobStatus.CLASSIFYING,
        PipelineStage.GEOLOCATION: JobStatus.GEOCODING,
        PipelineStage.EVENTS: JobStatus.DETECTING_EVENTS,
        PipelineStage.SCORING: JobStatus.CALCULATING,
        PipelineStage.FINALIZING: JobStatus.CALCULATING,
    }
)

_STAGE_PROGRESS: Mapping[PipelineStage, int] = MappingProxyType(
    {
        PipelineStage.PREPARING: 5,
        PipelineStage.COLLECTION: 20,
        PipelineStage.PREPROCESSING: 35,
        PipelineStage.NLP: 55,
        PipelineStage.GEOLOCATION: 72,
        PipelineStage.EVENTS: 88,
        PipelineStage.SCORING: 97,
        PipelineStage.FINALIZING: 99,
    }
)


class CheckpointState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


TERMINAL_JOB_STATUSES = frozenset(
    {
        JobStatus.COMPLETED,
        JobStatus.COMPLETED_WITH_WARNINGS,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    }
)


def stage_job_status(stage: PipelineStage) -> JobStatus:
    return _STAGE_STATUS[stage]


def stage_progress(stage: PipelineStage) -> int:
    return _STAGE_PROGRESS[stage]


def _parse_datetime(value: object, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = f"{candidate[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError as error:
            raise ContractValidationError(
                f"{field_name} must be an ISO 8601 datetime"
            ) from error
    else:
        raise ContractValidationError(f"{field_name} must be a datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractValidationError(f"{field_name} must include a UTC offset")
    return parsed.astimezone(UTC)


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    parsed = _parse_datetime(value, "datetime")
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _json_value(value: object, field_name: str) -> Any:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidStageOutputError(f"{field_name} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise InvalidStageOutputError(f"{field_name} keys must be strings")
            normalized[key] = _json_value(item, f"{field_name}.{key}")
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [
            _json_value(item, f"{field_name}[{index}]")
            for index, item in enumerate(value)
        ]
    raise InvalidStageOutputError(
        f"{field_name} contains unsupported value {type(value).__name__}"
    )


def immutable_json_mapping(
    value: Mapping[str, Any] | None,
    field_name: str,
) -> Mapping[str, Any]:
    return MappingProxyType(_json_value(value or {}, field_name))


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_delay_seconds: float = 0.0
    multiplier: float = 2.0
    max_delay_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.initial_delay_seconds < 0:
            raise ValueError("initial_delay_seconds must not be negative")
        if self.multiplier < 1:
            raise ValueError("multiplier must be at least 1")
        if self.max_delay_seconds < 0:
            raise ValueError("max_delay_seconds must not be negative")

    def delay_after(self, failed_attempt: int) -> float:
        if failed_attempt < 1:
            raise ValueError("failed_attempt must be positive")
        delay = self.initial_delay_seconds * self.multiplier ** (failed_attempt - 1)
        return min(delay, self.max_delay_seconds)


@dataclass(frozen=True, slots=True)
class StageResult:
    """Structured checkpoint output. Arbitrary Python objects are prohibited."""

    output: Mapping[str, Any] = field(default_factory=dict)
    processed_items: int | None = None
    total_items: int | None = None
    warnings: tuple[ContractIssue, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "output",
            immutable_json_mapping(self.output, "stage_result.output"),
        )
        for name in ("processed_items", "total_items"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or value < 0):
                raise InvalidStageOutputError(f"{name} must be a non-negative integer")
        if (
            self.processed_items is not None
            and self.total_items is not None
            and self.processed_items > self.total_items
        ):
            raise InvalidStageOutputError(
                "processed_items cannot exceed total_items"
            )
        if not all(isinstance(issue, ContractIssue) for issue in self.warnings):
            raise InvalidStageOutputError("warnings must contain ContractIssue values")


@dataclass(frozen=True, slots=True)
class StageCheckpoint:
    stage: PipelineStage
    state: CheckpointState = CheckpointState.PENDING
    attempt: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime | None = None
    processed_items: int | None = None
    total_items: int | None = None
    output: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[ContractIssue, ...] = ()
    error: ContractIssue | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.stage, PipelineStage):
            object.__setattr__(self, "stage", PipelineStage(self.stage))
        if not isinstance(self.state, CheckpointState):
            object.__setattr__(self, "state", CheckpointState(self.state))
        if self.attempt < 0:
            raise ValueError("checkpoint attempt must not be negative")
        for field_name in ("started_at", "completed_at", "updated_at"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _parse_datetime(value, field_name))
        object.__setattr__(
            self,
            "output",
            immutable_json_mapping(self.output, "checkpoint.output"),
        )
        for name in ("processed_items", "total_items"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer")
        if (
            self.processed_items is not None
            and self.total_items is not None
            and self.processed_items > self.total_items
        ):
            raise ValueError("processed_items cannot exceed total_items")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "stage": self.stage.value,
            "state": self.state.value,
            "attempt": self.attempt,
            "output": dict(self.output),
            "warnings": [issue.to_dict() for issue in self.warnings],
        }
        for name in ("started_at", "completed_at", "updated_at"):
            value = _format_datetime(getattr(self, name))
            if value is not None:
                payload[name] = value
        if self.processed_items is not None:
            payload["processed_items"] = self.processed_items
        if self.total_items is not None:
            payload["total_items"] = self.total_items
        if self.error is not None:
            payload["error"] = self.error.to_dict()
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> StageCheckpoint:
        warnings = tuple(
            ContractIssue.from_dict(item) for item in payload.get("warnings", [])
        )
        error_payload = payload.get("error")
        return cls(
            stage=PipelineStage(payload["stage"]),
            state=CheckpointState(payload["state"]),
            attempt=payload.get("attempt", 0),
            started_at=payload.get("started_at"),
            completed_at=payload.get("completed_at"),
            updated_at=payload.get("updated_at"),
            processed_items=payload.get("processed_items"),
            total_items=payload.get("total_items"),
            output=payload.get("output", {}),
            warnings=warnings,
            error=(
                ContractIssue.from_dict(error_payload)
                if isinstance(error_payload, Mapping)
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class JobRecord:
    analysis_id: str
    request_payload: Mapping[str, Any]
    request_fingerprint: str
    idempotency_key: str
    status: JobStatus = JobStatus.QUEUED
    current_stage: PipelineStage | None = None
    progress_percent: int = 0
    checkpoints: tuple[StageCheckpoint, ...] = ()
    warnings: tuple[ContractIssue, ...] = ()
    errors: tuple[ContractIssue, ...] = ()
    cancel_requested: bool = False
    job_attempt: int = 1
    revision: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None

    def __post_init__(self) -> None:
        request = AnalysisRequestV1.from_dict(self.request_payload)
        if request.analysis_id != self.analysis_id:
            raise ContractValidationError(
                "request_payload analysis_id must equal job analysis_id"
            )
        object.__setattr__(
            self,
            "request_payload",
            immutable_json_mapping(request.to_dict(), "request_payload"),
        )
        if request.fingerprint != self.request_fingerprint:
            raise ContractValidationError("request_fingerprint does not match request")
        if request.effective_idempotency_key != self.idempotency_key:
            raise ContractValidationError("idempotency_key does not match request")
        if not isinstance(self.status, JobStatus):
            object.__setattr__(self, "status", JobStatus(self.status))
        if self.current_stage is not None and not isinstance(
            self.current_stage, PipelineStage
        ):
            object.__setattr__(self, "current_stage", PipelineStage(self.current_stage))
        if not 0 <= self.progress_percent <= 100:
            raise ValueError("progress_percent must be in [0, 100]")
        if self.job_attempt < 1:
            raise ValueError("job_attempt must be positive")
        if self.revision < 0:
            raise ValueError("revision must not be negative")
        object.__setattr__(
            self,
            "created_at",
            _parse_datetime(self.created_at, "created_at"),
        )
        object.__setattr__(
            self,
            "updated_at",
            _parse_datetime(self.updated_at, "updated_at"),
        )
        if self.lease_expires_at is not None:
            object.__setattr__(
                self,
                "lease_expires_at",
                _parse_datetime(self.lease_expires_at, "lease_expires_at"),
            )
        if self.checkpoints:
            stages = tuple(checkpoint.stage for checkpoint in self.checkpoints)
            if stages != PIPELINE_STAGES:
                raise ValueError("checkpoints must follow the complete pipeline order")
        else:
            object.__setattr__(
                self,
                "checkpoints",
                tuple(StageCheckpoint(stage=stage) for stage in PIPELINE_STAGES),
            )

    @property
    def request(self) -> AnalysisRequestV1:
        return AnalysisRequestV1.from_dict(self.request_payload)

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_JOB_STATUSES

    @property
    def next_stage(self) -> PipelineStage | None:
        for checkpoint in self.checkpoints:
            if checkpoint.state is not CheckpointState.COMPLETED:
                return checkpoint.stage
        return None

    @property
    def completed_outputs(self) -> Mapping[str, Mapping[str, Any]]:
        outputs = {
            checkpoint.stage.value: checkpoint.output
            for checkpoint in self.checkpoints
            if checkpoint.state is CheckpointState.COMPLETED
        }
        return MappingProxyType(outputs)

    def checkpoint(self, stage: PipelineStage) -> StageCheckpoint:
        return self.checkpoints[PIPELINE_STAGES.index(stage)]

    def replace_checkpoint(self, checkpoint: StageCheckpoint) -> JobRecord:
        checkpoints = list(self.checkpoints)
        checkpoints[PIPELINE_STAGES.index(checkpoint.stage)] = checkpoint
        return replace(self, checkpoints=tuple(checkpoints))

    def to_status(self) -> JobStatusV1:
        checkpoint = (
            self.checkpoint(self.current_stage)
            if self.current_stage is not None
            else None
        )
        return JobStatusV1(
            analysis_id=self.analysis_id,
            status=self.status,
            updated_at=self.updated_at,
            progress_percent=self.progress_percent,
            stage=self.current_stage.value if self.current_stage else self.status.value,
            message=(
                "cancellation requested"
                if self.cancel_requested and not self.terminal
                else None
            ),
            attempt=self.job_attempt,
            processed_items=(checkpoint.processed_items if checkpoint else None),
            total_items=(checkpoint.total_items if checkpoint else None),
            warnings=self.warnings,
            errors=self.errors,
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "analysis_id": self.analysis_id,
            "request_payload": dict(self.request_payload),
            "request_fingerprint": self.request_fingerprint,
            "idempotency_key": self.idempotency_key,
            "status": self.status.value,
            "progress_percent": self.progress_percent,
            "checkpoints": [item.to_dict() for item in self.checkpoints],
            "warnings": [issue.to_dict() for issue in self.warnings],
            "errors": [issue.to_dict() for issue in self.errors],
            "cancel_requested": self.cancel_requested,
            "job_attempt": self.job_attempt,
            "revision": self.revision,
            "created_at": _format_datetime(self.created_at),
            "updated_at": _format_datetime(self.updated_at),
        }
        if self.current_stage is not None:
            payload["current_stage"] = self.current_stage.value
        if self.lease_owner is not None:
            payload["lease_owner"] = self.lease_owner
        lease_expires_at = _format_datetime(self.lease_expires_at)
        if lease_expires_at is not None:
            payload["lease_expires_at"] = lease_expires_at
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> JobRecord:
        return cls(
            analysis_id=payload["analysis_id"],
            request_payload=payload["request_payload"],
            request_fingerprint=payload["request_fingerprint"],
            idempotency_key=payload["idempotency_key"],
            status=JobStatus(payload.get("status", JobStatus.QUEUED.value)),
            current_stage=(
                PipelineStage(payload["current_stage"])
                if payload.get("current_stage") is not None
                else None
            ),
            progress_percent=payload.get("progress_percent", 0),
            checkpoints=tuple(
                StageCheckpoint.from_dict(item)
                for item in payload.get("checkpoints", [])
            ),
            warnings=tuple(
                ContractIssue.from_dict(item) for item in payload.get("warnings", [])
            ),
            errors=tuple(
                ContractIssue.from_dict(item) for item in payload.get("errors", [])
            ),
            cancel_requested=payload.get("cancel_requested", False),
            job_attempt=payload.get("job_attempt", 1),
            revision=payload.get("revision", 0),
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
            lease_owner=payload.get("lease_owner"),
            lease_expires_at=payload.get("lease_expires_at"),
        )

    @classmethod
    def new(cls, request: AnalysisRequestV1, now: datetime) -> JobRecord:
        return cls(
            analysis_id=request.analysis_id,
            request_payload=request.to_dict(),
            request_fingerprint=request.fingerprint,
            idempotency_key=request.effective_idempotency_key,
            created_at=now,
            updated_at=now,
        )
