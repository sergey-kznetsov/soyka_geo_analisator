"""Typed contracts for durable SOIKA worker execution."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from threading import Event
from uuid import uuid4

_TRACE_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_WORKER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,126}$")


class WorkerError(RuntimeError):
    """Base worker-runtime failure."""


class QueueConflictError(WorkerError):
    """A queue item already exists with incompatible immutable routing."""


class QueueLeaseError(WorkerError):
    """The worker no longer owns a live queue lease."""


class WorkerTimeoutError(BaseException):
    """Control-flow signal used to escape nested stage Exception handlers."""


class WorkerConfigurationError(WorkerError):
    """Worker configuration is incomplete or unsafe."""


class ComputeClass(str, Enum):
    CPU = "cpu"
    GPU = "gpu"


def new_trace_id() -> str:
    return uuid4().hex


def validate_trace_id(value: str) -> str:
    if not isinstance(value, str) or _TRACE_ID_RE.fullmatch(value) is None:
        raise ValueError("trace_id must contain exactly 32 lowercase hex characters")
    if value == "0" * 32:
        raise ValueError("trace_id must not be all zeroes")
    return value


def validate_worker_id(value: str) -> str:
    if not isinstance(value, str) or _WORKER_ID_RE.fullmatch(value) is None:
        raise ValueError("worker_id contains unsupported characters")
    return value


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class QueueItem:
    analysis_id: str
    compute_class: ComputeClass
    priority: int
    attempt: int
    max_attempts: int
    available_at: datetime
    trace_id: str
    enqueued_at: datetime
    updated_at: datetime
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    cancel_requested: bool = False
    last_error: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.analysis_id, str) or not self.analysis_id.strip():
            raise ValueError("analysis_id must be non-empty")
        object.__setattr__(self, "compute_class", ComputeClass(self.compute_class))
        if type(self.priority) is not int or not -100 <= self.priority <= 100:
            raise ValueError("priority must be an integer in [-100, 100]")
        if type(self.attempt) is not int or self.attempt < 0:
            raise ValueError("attempt must be a non-negative integer")
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        if self.attempt > self.max_attempts:
            raise ValueError("attempt cannot exceed max_attempts")
        object.__setattr__(
            self,
            "available_at",
            _aware_utc(self.available_at, "available_at"),
        )
        object.__setattr__(
            self,
            "enqueued_at",
            _aware_utc(self.enqueued_at, "enqueued_at"),
        )
        object.__setattr__(
            self,
            "updated_at",
            _aware_utc(self.updated_at, "updated_at"),
        )
        if self.lease_expires_at is not None:
            object.__setattr__(
                self,
                "lease_expires_at",
                _aware_utc(self.lease_expires_at, "lease_expires_at"),
            )
        if self.lease_owner is not None:
            validate_worker_id(self.lease_owner)
        validate_trace_id(self.trace_id)
        if not isinstance(self.cancel_requested, bool):
            raise TypeError("cancel_requested must be bool")

    @property
    def exhausted(self) -> bool:
        return self.attempt >= self.max_attempts


@dataclass(frozen=True, slots=True)
class QueueStats:
    ready: int = 0
    leased: int = 0
    delayed: int = 0
    exhausted: int = 0
    cancelled: int = 0
    oldest_ready_age_seconds: float = 0.0

    def __post_init__(self) -> None:
        for name in ("ready", "leased", "delayed", "exhausted", "cancelled"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if (
            not isinstance(self.oldest_ready_age_seconds, int | float)
            or not math.isfinite(float(self.oldest_ready_age_seconds))
            or self.oldest_ready_age_seconds < 0
        ):
            raise ValueError("oldest_ready_age_seconds must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    worker_id: str
    compute_class: ComputeClass = ComputeClass.CPU
    queue_lease_seconds: float = 600.0
    heartbeat_seconds: float = 30.0
    poll_seconds: float = 1.0
    wall_timeout_seconds: float = 3600.0
    retry_initial_seconds: float = 5.0
    retry_multiplier: float = 2.0
    retry_max_seconds: float = 300.0
    failure_alert_threshold: int = 3

    def __post_init__(self) -> None:
        validate_worker_id(self.worker_id)
        object.__setattr__(self, "compute_class", ComputeClass(self.compute_class))
        for name in (
            "queue_lease_seconds",
            "heartbeat_seconds",
            "poll_seconds",
            "wall_timeout_seconds",
        ):
            value = getattr(self, name)
            if not isinstance(value, int | float) or not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.queue_lease_seconds <= self.heartbeat_seconds * 2:
            raise ValueError("queue lease must exceed two heartbeat intervals")
        for name in ("retry_initial_seconds", "retry_max_seconds"):
            value = getattr(self, name)
            if not isinstance(value, int | float) or value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.retry_multiplier < 1:
            raise ValueError("retry_multiplier must be at least 1")
        if self.retry_max_seconds < self.retry_initial_seconds:
            raise ValueError("retry_max_seconds cannot be smaller than initial delay")
        if type(self.failure_alert_threshold) is not int or self.failure_alert_threshold < 1:
            raise ValueError("failure_alert_threshold must be a positive integer")

    def retry_delay(self, attempt: int) -> float:
        if attempt < 1:
            raise ValueError("attempt must be positive")
        delay = self.retry_initial_seconds * self.retry_multiplier ** (attempt - 1)
        return min(delay, self.retry_max_seconds)


@dataclass(frozen=True, slots=True)
class WorkerContext:
    analysis_id: str
    worker_id: str
    compute_class: ComputeClass
    attempt: int
    max_attempts: int
    trace_id: str
    cancellation: Event = field(compare=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.analysis_id, str) or not self.analysis_id.strip():
            raise ValueError("analysis_id must be non-empty")
        validate_worker_id(self.worker_id)
        object.__setattr__(self, "compute_class", ComputeClass(self.compute_class))
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("attempt must be a positive integer")
        if type(self.max_attempts) is not int or self.max_attempts < self.attempt:
            raise ValueError("max_attempts must be >= attempt")
        validate_trace_id(self.trace_id)
        if not isinstance(self.cancellation, Event):
            raise TypeError("cancellation must be threading.Event")

    @property
    def cancel_requested(self) -> bool:
        return self.cancellation.is_set()
