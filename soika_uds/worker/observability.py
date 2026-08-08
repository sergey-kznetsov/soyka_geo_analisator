"""Structured logs, metrics, trace correlation and alert events for workers."""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from types import TracebackType
from typing import Protocol

from .models import new_trace_id, validate_trace_id

_TRACEPARENT_RE = re.compile(
    r"^(?P<version>[0-9a-f]{2})-(?P<trace>[0-9a-f]{32})-"
    r"(?P<span>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})$"
)
_SECRET_FIELD_PARTS = ("password", "secret", "token", "dsn", "credential")


def new_span_id() -> str:
    value = secrets.token_hex(8)
    while value == "0" * 16:
        value = secrets.token_hex(8)
    return value


@dataclass(frozen=True, slots=True)
class TraceContext:
    trace_id: str
    span_id: str
    trace_flags: str = "01"

    def __post_init__(self) -> None:
        validate_trace_id(self.trace_id)
        if not re.fullmatch(r"[0-9a-f]{16}", self.span_id):
            raise ValueError("span_id must contain exactly 16 lowercase hex characters")
        if self.span_id == "0" * 16:
            raise ValueError("span_id must not be all zeroes")
        if not re.fullmatch(r"[0-9a-f]{2}", self.trace_flags):
            raise ValueError("trace_flags must contain two lowercase hex characters")

    @classmethod
    def root(cls, trace_id: str | None = None) -> TraceContext:
        return cls(trace_id=trace_id or new_trace_id(), span_id=new_span_id())

    @classmethod
    def from_traceparent(cls, value: str | None) -> TraceContext:
        if value is None:
            return cls.root()
        match = _TRACEPARENT_RE.fullmatch(value.strip().lower())
        if match is None or match.group("version") == "ff":
            raise ValueError("traceparent is not a valid W3C trace context")
        return cls(
            trace_id=match.group("trace"),
            span_id=match.group("span"),
            trace_flags=match.group("flags"),
        )

    def child(self) -> TraceContext:
        return TraceContext(
            trace_id=self.trace_id,
            span_id=new_span_id(),
            trace_flags=self.trace_flags,
        )

    @property
    def traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-{self.trace_flags}"


def _sanitize(value: object, *, key: str = "") -> object:
    lowered = key.casefold()
    if any(part in lowered for part in _SECRET_FIELD_PARTS):
        return "<redacted>"
    if value is None or isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, Mapping):
        return {str(k): _sanitize(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_sanitize(item) for item in value]
    return str(value)


class JsonLogFormatter(logging.Formatter):
    """Emit one deterministic JSON object per worker log record."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, UTC).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
        payload: dict[str, object] = {
            "timestamp": timestamp,
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        event = getattr(record, "event", None)
        if isinstance(event, str) and event:
            payload["event"] = event
        fields = getattr(record, "worker_fields", None)
        if isinstance(fields, Mapping):
            for key in sorted(fields):
                payload[str(key)] = _sanitize(fields[key], key=str(key))
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def configure_worker_logging(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("soika.worker")
    logger.setLevel(level)
    logger.propagate = False
    if not any(getattr(handler, "_soika_worker", False) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        handler._soika_worker = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    return logger


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    message: str,
    **fields: object,
) -> None:
    logger.log(
        level,
        message,
        extra={"event": event, "worker_fields": fields},
    )


class WorkerMetrics:
    """Small dependency-free Prometheus text collector for one worker process."""

    def __init__(self, *, worker_id: str, compute_class: str) -> None:
        self.worker_id = worker_id
        self.compute_class = compute_class
        self._lock = Lock()
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {"worker_up": 1.0}

    def inc(self, name: str, amount: float = 1.0) -> None:
        if amount < 0:
            raise ValueError("counter increments must be non-negative")
        with self._lock:
            self._counters[name] = self._counters.get(name, 0.0) + amount

    def set(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = float(value)

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            return {**self._counters, **self._gauges}

    def render_prometheus(self) -> str:
        labels = (
            f'worker_id="{self.worker_id}",'
            f'compute_class="{self.compute_class}"'
        )
        lines: list[str] = []
        snapshot = self.snapshot()
        for name in sorted(snapshot):
            metric = f"soika_worker_{name}"
            lines.append(f"{metric}{{{labels}}} {snapshot[name]:.6f}")
        return "\n".join(lines) + "\n"


@dataclass(frozen=True, slots=True)
class WorkerAlert:
    code: str
    message: str
    severity: str = "warning"
    analysis_id: str | None = None
    trace_id: str | None = None


class AlertSink(Protocol):
    def emit(self, alert: WorkerAlert) -> None:
        ...


class LoggingAlertSink:
    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger

    def emit(self, alert: WorkerAlert) -> None:
        log_event(
            self.logger,
            logging.ERROR if alert.severity == "error" else logging.WARNING,
            "worker.alert",
            alert.message,
            alert_code=alert.code,
            severity=alert.severity,
            analysis_id=alert.analysis_id,
            trace_id=alert.trace_id,
        )


class TraceSpan:
    """W3C-compatible span correlation emitted through structured logs."""

    def __init__(
        self,
        *,
        logger: logging.Logger,
        name: str,
        context: TraceContext,
        fields: Mapping[str, object] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.logger = logger
        self.name = name
        self.context = context
        self.fields = dict(fields or {})
        self.monotonic = monotonic
        self.started_at = 0.0

    def __enter__(self) -> TraceSpan:
        self.started_at = self.monotonic()
        log_event(
            self.logger,
            logging.INFO,
            "trace.span.start",
            self.name,
            trace_id=self.context.trace_id,
            span_id=self.context.span_id,
            **self.fields,
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        duration_ms = max(0.0, (self.monotonic() - self.started_at) * 1000.0)
        log_event(
            self.logger,
            logging.INFO if exc_type is None else logging.ERROR,
            "trace.span.end",
            self.name,
            trace_id=self.context.trace_id,
            span_id=self.context.span_id,
            duration_ms=round(duration_ms, 3),
            status="ok" if exc_type is None else "error",
            error_type=None if exc_type is None else exc_type.__name__,
            **self.fields,
        )


__all__ = [
    "AlertSink",
    "JsonLogFormatter",
    "LoggingAlertSink",
    "TraceContext",
    "TraceSpan",
    "WorkerAlert",
    "WorkerMetrics",
    "configure_worker_logging",
    "log_event",
]
