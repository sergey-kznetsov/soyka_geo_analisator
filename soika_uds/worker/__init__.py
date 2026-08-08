"""Durable CPU/GPU worker runtime for SOIKA server deployments."""

from .control import OrchestratorExecutor, WorkerControl
from .models import (
    ComputeClass,
    QueueConflictError,
    QueueItem,
    QueueLeaseError,
    QueueStats,
    WorkerConfigurationError,
    WorkerContext,
    WorkerError,
    WorkerSettings,
    WorkerTimeoutError,
    new_trace_id,
    validate_trace_id,
    validate_worker_id,
)
from .observability import (
    AlertSink,
    JsonLogFormatter,
    LoggingAlertSink,
    TraceContext,
    TraceSpan,
    WorkerAlert,
    WorkerMetrics,
    configure_worker_logging,
    log_event,
)
from .probes import WorkerProbeServer
from .queue import JobQueue, PostgresJobQueue
from .runtime import WorkerExecutor, WorkerRuntime, WorkerSnapshot

__all__ = [
    "AlertSink",
    "ComputeClass",
    "JobQueue",
    "JsonLogFormatter",
    "LoggingAlertSink",
    "OrchestratorExecutor",
    "PostgresJobQueue",
    "QueueConflictError",
    "QueueItem",
    "QueueLeaseError",
    "QueueStats",
    "TraceContext",
    "TraceSpan",
    "WorkerAlert",
    "WorkerConfigurationError",
    "WorkerContext",
    "WorkerControl",
    "WorkerError",
    "WorkerExecutor",
    "WorkerMetrics",
    "WorkerProbeServer",
    "WorkerRuntime",
    "WorkerSettings",
    "WorkerSnapshot",
    "WorkerTimeoutError",
    "configure_worker_logging",
    "log_event",
    "new_trace_id",
    "validate_trace_id",
    "validate_worker_id",
]
