from __future__ import annotations

import io
import logging
import time
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest

from soika_uds.contracts import JobStatus
from soika_uds.worker import (
    ComputeClass,
    JsonLogFormatter,
    QueueItem,
    QueueLeaseError,
    QueueStats,
    TraceContext,
    WorkerMetrics,
    WorkerProbeServer,
    WorkerRuntime,
    WorkerSettings,
    log_event,
)


def _item(
    analysis_id: str,
    *,
    attempt: int = 1,
    max_attempts: int = 3,
    compute_class: ComputeClass = ComputeClass.CPU,
) -> QueueItem:
    now = datetime.now(UTC)
    return QueueItem(
        analysis_id=analysis_id,
        compute_class=compute_class,
        priority=0,
        attempt=attempt,
        max_attempts=max_attempts,
        available_at=now,
        lease_owner="worker-1",
        lease_expires_at=now + timedelta(minutes=10),
        cancel_requested=False,
        trace_id="1" * 32,
        last_error=None,
        enqueued_at=now,
        updated_at=now,
    )


class FakeQueue:
    def __init__(self, items: list[QueueItem]) -> None:
        self.items = list(items)
        self.released: list[tuple[str, bool]] = []
        self.acked: list[str] = []
        self.cancelled: set[str] = set()

    def enqueue(self, *args, **kwargs):  # pragma: no cover - protocol completeness
        raise NotImplementedError

    def claim(self, *, worker_id, compute_class, lease_seconds):
        assert worker_id == "worker-1"
        assert compute_class is ComputeClass.CPU
        assert lease_seconds == 600.0
        if not self.items:
            return None
        return self.items.pop(0)

    def renew(self, analysis_id, *, worker_id, lease_seconds):
        return _item(analysis_id)

    def release(
        self,
        analysis_id,
        *,
        worker_id,
        retryable,
        retry_delay_seconds,
        error,
    ):
        assert worker_id == "worker-1"
        assert retry_delay_seconds >= 0
        assert "code" in error
        self.released.append((analysis_id, retryable))
        source = _item(analysis_id)
        return QueueItem(
            analysis_id=source.analysis_id,
            compute_class=source.compute_class,
            priority=source.priority,
            attempt=source.attempt,
            max_attempts=source.max_attempts,
            available_at=source.available_at,
            lease_owner=None,
            lease_expires_at=None,
            cancel_requested=False,
            trace_id=source.trace_id,
            last_error=dict(error),
            enqueued_at=source.enqueued_at,
            updated_at=source.updated_at,
        )

    def ack(self, analysis_id, *, worker_id):
        if worker_id != "worker-1":
            raise QueueLeaseError("wrong owner")
        self.acked.append(analysis_id)

    def request_cancel(self, analysis_id):
        self.cancelled.add(analysis_id)
        return _item(analysis_id)

    def is_cancel_requested(self, analysis_id):
        return analysis_id in self.cancelled

    def retry(self, analysis_id):  # pragma: no cover - protocol completeness
        return _item(analysis_id)

    def stats(self, compute_class):
        assert compute_class is ComputeClass.CPU
        return QueueStats(ready=len(self.items))


class RecordingAlertSink:
    def __init__(self) -> None:
        self.alerts = []

    def emit(self, alert) -> None:
        self.alerts.append(alert)


def _settings(**changes) -> WorkerSettings:
    values = {
        "worker_id": "worker-1",
        "compute_class": ComputeClass.CPU,
        "queue_lease_seconds": 600.0,
        "heartbeat_seconds": 1.0,
        "poll_seconds": 0.01,
        "wall_timeout_seconds": 1.0,
        "shutdown_grace_seconds": 10.0,
        "retry_initial_seconds": 0.0,
        "retry_max_seconds": 0.0,
    }
    values.update(changes)
    return WorkerSettings(**values)


def test_worker_settings_keep_cpu_and_gpu_queues_separate() -> None:
    assert _settings().compute_class is ComputeClass.CPU
    assert _settings(compute_class=ComputeClass.GPU).compute_class is ComputeClass.GPU

    with pytest.raises(ValueError):
        _settings(queue_lease_seconds=2.0, heartbeat_seconds=1.0)


def test_w3c_trace_context_is_validated_and_child_preserves_trace() -> None:
    parent = TraceContext.from_traceparent(
        "00-11111111111111111111111111111111-2222222222222222-01"
    )
    child = parent.child()

    assert child.trace_id == parent.trace_id
    assert child.span_id != parent.span_id
    assert child.traceparent.startswith("00-11111111111111111111111111111111-")

    with pytest.raises(ValueError):
        TraceContext.from_traceparent("00-bad")


def test_json_logs_redact_secret_like_fields() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    logger = logging.getLogger("test.worker.stage14")
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    log_event(
        logger,
        logging.INFO,
        "test.event",
        "safe",
        analysis_id="analysis-1",
        database_dsn="postgresql://user:password@db/example",
    )

    rendered = stream.getvalue()
    assert '"event":"test.event"' in rendered
    assert '"database_dsn":"<redacted>"' in rendered
    assert "postgresql://" not in rendered


def test_prometheus_metrics_are_deterministic() -> None:
    metrics = WorkerMetrics(worker_id="worker-1", compute_class="cpu")
    metrics.inc("jobs_claimed_total")
    metrics.set("queue_ready", 2)

    rendered = metrics.render_prometheus()

    assert "soika_worker_jobs_claimed_total" in rendered
    assert "soika_worker_queue_ready" in rendered
    assert 'worker_id="worker-1",compute_class="cpu"' in rendered


def test_one_failed_job_does_not_block_the_next_job() -> None:
    queue = FakeQueue([_item("analysis-bad"), _item("analysis-good")])
    calls: list[str] = []

    def executor(context) -> None:
        calls.append(context.analysis_id)
        if context.analysis_id == "analysis-bad":
            raise RuntimeError("fixture failure")

    runtime = WorkerRuntime(
        queue,
        executor,
        _settings(),
        alert_sink=RecordingAlertSink(),
    )

    assert runtime.run_once() is True
    assert runtime.run_once() is True
    assert calls == ["analysis-bad", "analysis-good"]
    assert queue.released == [("analysis-bad", True)]
    assert queue.acked == ["analysis-good"]


def test_canonical_failed_result_is_parked_for_explicit_retry() -> None:
    queue = FakeQueue([_item("analysis-domain-failed")])

    class FailedResult:
        status = JobStatus.FAILED

    runtime = WorkerRuntime(
        queue,
        lambda _context: FailedResult(),
        _settings(),
        alert_sink=RecordingAlertSink(),
    )

    assert runtime.run_once() is True
    assert queue.released == [("analysis-domain-failed", False)]
    assert queue.acked == []
    assert runtime.metrics.snapshot()["jobs_domain_failed_total"] == 1.0


def test_wall_timeout_requeues_only_the_timed_out_job() -> None:
    queue = FakeQueue([_item("analysis-timeout")])

    def executor(_context) -> None:
        time.sleep(0.1)

    runtime = WorkerRuntime(
        queue,
        executor,
        _settings(wall_timeout_seconds=0.02),
        alert_sink=RecordingAlertSink(),
    )

    assert runtime.run_once() is True
    assert queue.released == [("analysis-timeout", True)]
    assert queue.acked == []
    assert runtime.metrics.snapshot()["jobs_timed_out_total"] == 1.0


def test_graceful_shutdown_stops_claiming_new_jobs() -> None:
    queue = FakeQueue([_item("analysis-pending")])
    called = Event()
    runtime = WorkerRuntime(
        queue,
        lambda _context: called.set(),
        _settings(),
        alert_sink=RecordingAlertSink(),
    )

    runtime.request_shutdown("test")

    assert runtime.run_once() is False
    assert not called.is_set()
    assert queue.acked == []


def test_worker_probes_are_loopback_only_by_default() -> None:
    queue = FakeQueue([])
    runtime = WorkerRuntime(
        queue,
        lambda _context: None,
        _settings(),
        alert_sink=RecordingAlertSink(),
    )

    with pytest.raises(ValueError):
        WorkerProbeServer(runtime, runtime.metrics, host="0.0.0.0")
