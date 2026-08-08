"""Single-job worker runtime with leases, retries and graceful shutdown."""

from __future__ import annotations

import logging
import signal
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Event, Thread
from typing import Protocol

from .models import (
    QueueItem,
    QueueLeaseError,
    WorkerContext,
    WorkerSettings,
    WorkerTimeoutError,
)
from .observability import (
    AlertSink,
    LoggingAlertSink,
    TraceContext,
    TraceSpan,
    WorkerAlert,
    WorkerMetrics,
    configure_worker_logging,
    log_event,
)
from .queue import JobQueue


class WorkerExecutor(Protocol):
    def __call__(self, context: WorkerContext) -> object:
        ...


@dataclass(frozen=True, slots=True)
class WorkerSnapshot:
    worker_id: str
    compute_class: str
    ready: bool
    stopping: bool
    active_analysis_id: str | None
    consecutive_failures: int


@contextmanager
def _wall_deadline(seconds: float):
    """Interrupt long Python work on Unix; containers remain the hard fallback."""

    if (
        threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "SIGALRM")
        or not hasattr(signal, "ITIMER_REAL")
    ):
        yield
        return

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)

    def _timeout(_signum: int, _frame: object) -> None:
        raise WorkerTimeoutError("worker wall-clock deadline expired")

    signal.signal(signal.SIGALRM, _timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer != (0.0, 0.0):
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


class WorkerRuntime:
    """Poll one compute queue and isolate failures at the job boundary."""

    def __init__(
        self,
        queue: JobQueue,
        executor: WorkerExecutor,
        settings: WorkerSettings,
        *,
        metrics: WorkerMetrics | None = None,
        logger: logging.Logger | None = None,
        alert_sink: AlertSink | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.queue = queue
        self.executor = executor
        self.settings = settings
        self.metrics = metrics or WorkerMetrics(
            worker_id=settings.worker_id,
            compute_class=settings.compute_class.value,
        )
        self.logger = logger or configure_worker_logging()
        self.alert_sink = alert_sink or LoggingAlertSink(self.logger)
        self.sleeper = sleeper
        self._stop = Event()
        self._ready = False
        self._active_analysis_id: str | None = None
        self._consecutive_failures = 0
        self._signal_handlers: dict[int, object] = {}

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    @property
    def ready(self) -> bool:
        return self._ready and not self.stopping

    def snapshot(self) -> WorkerSnapshot:
        return WorkerSnapshot(
            worker_id=self.settings.worker_id,
            compute_class=self.settings.compute_class.value,
            ready=self.ready,
            stopping=self.stopping,
            active_analysis_id=self._active_analysis_id,
            consecutive_failures=self._consecutive_failures,
        )

    def request_shutdown(self, reason: str = "requested") -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        self.metrics.set("ready", 0.0)
        log_event(
            self.logger,
            logging.INFO,
            "worker.shutdown.requested",
            "worker will stop after the active job boundary",
            worker_id=self.settings.worker_id,
            reason=reason,
            shutdown_grace_seconds=self.settings.shutdown_grace_seconds,
        )

    def install_signal_handlers(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("worker signal handlers must be installed in main thread")
        for signum in (signal.SIGTERM, signal.SIGINT):
            self._signal_handlers[int(signum)] = signal.getsignal(signum)
            signal.signal(
                signum,
                lambda received, _frame: self.request_shutdown(
                    signal.Signals(received).name
                ),
            )

    def restore_signal_handlers(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return
        for signum, handler in self._signal_handlers.items():
            signal.signal(signum, handler)
        self._signal_handlers.clear()

    def _start_heartbeat(
        self,
        item: QueueItem,
        cancellation: Event,
        finished: Event,
        lease_lost: Event,
    ) -> Thread:
        def _heartbeat() -> None:
            while not finished.wait(self.settings.heartbeat_seconds):
                try:
                    renewed = self.queue.renew(
                        item.analysis_id,
                        worker_id=self.settings.worker_id,
                        lease_seconds=self.settings.queue_lease_seconds,
                    )
                except QueueLeaseError:
                    lease_lost.set()
                    cancellation.set()
                    self.metrics.inc("lease_lost_total")
                    self.alert_sink.emit(
                        WorkerAlert(
                            code="QUEUE_LEASE_LOST",
                            message="worker lost the durable queue lease",
                            severity="error",
                            analysis_id=item.analysis_id,
                            trace_id=item.trace_id,
                        )
                    )
                    return
                if renewed.cancel_requested:
                    cancellation.set()

        thread = Thread(
            target=_heartbeat,
            name=f"soika-heartbeat-{item.analysis_id}",
            daemon=True,
        )
        thread.start()
        return thread

    @staticmethod
    def _error_payload(code: str, error: BaseException) -> dict[str, object]:
        return {
            "code": code,
            "exception_type": type(error).__name__,
        }

    def _release_failure(
        self,
        item: QueueItem,
        *,
        code: str,
        error: BaseException,
        retryable: bool,
    ) -> None:
        delay = self.settings.retry_delay(item.attempt) if retryable else 0.0
        released = self.queue.release(
            item.analysis_id,
            worker_id=self.settings.worker_id,
            retryable=retryable,
            retry_delay_seconds=delay,
            error=self._error_payload(code, error),
        )
        will_retry = retryable and not released.exhausted
        self.metrics.inc("jobs_failed_total")
        if code == "WORKER_TIMEOUT":
            self.metrics.inc("jobs_timed_out_total")
        if will_retry:
            self.metrics.inc("jobs_requeued_total")
        else:
            self.metrics.inc("jobs_exhausted_total")
        self._consecutive_failures += 1
        log_event(
            self.logger,
            logging.ERROR,
            "worker.job.failed",
            "job execution failed at worker boundary",
            analysis_id=item.analysis_id,
            worker_id=self.settings.worker_id,
            trace_id=item.trace_id,
            attempt=item.attempt,
            max_attempts=item.max_attempts,
            error_code=code,
            error_type=type(error).__name__,
            requeued=will_retry,
            retry_delay_seconds=delay if will_retry else 0.0,
        )
        if (
            not will_retry
            or self._consecutive_failures >= self.settings.failure_alert_threshold
        ):
            self.alert_sink.emit(
                WorkerAlert(
                    code="WORKER_JOB_EXHAUSTED" if not will_retry else "WORKER_FAILURE_BURST",
                    message=(
                        "job exhausted worker retry attempts"
                        if not will_retry
                        else "worker failure threshold was reached"
                    ),
                    severity="error" if not will_retry else "warning",
                    analysis_id=item.analysis_id,
                    trace_id=item.trace_id,
                )
            )

    def run_once(self) -> bool:
        if self.stopping:
            return False
        item = self.queue.claim(
            worker_id=self.settings.worker_id,
            compute_class=self.settings.compute_class,
            lease_seconds=self.settings.queue_lease_seconds,
        )
        if item is None:
            stats = self.queue.stats(self.settings.compute_class)
            self.metrics.set("queue_ready", stats.ready)
            self.metrics.set("queue_leased", stats.leased)
            self.metrics.set("queue_delayed", stats.delayed)
            self.metrics.set("queue_exhausted", stats.exhausted)
            self.metrics.set("queue_cancelled", stats.cancelled)
            self.metrics.set("queue_oldest_ready_age_seconds", stats.oldest_ready_age_seconds)
            return False

        self._active_analysis_id = item.analysis_id
        self.metrics.inc("jobs_claimed_total")
        self.metrics.set("active_jobs", 1.0)
        cancellation = Event()
        if item.cancel_requested:
            cancellation.set()
        finished = Event()
        lease_lost = Event()
        heartbeat = self._start_heartbeat(item, cancellation, finished, lease_lost)
        context = WorkerContext(
            analysis_id=item.analysis_id,
            worker_id=self.settings.worker_id,
            compute_class=item.compute_class,
            attempt=item.attempt,
            max_attempts=item.max_attempts,
            trace_id=item.trace_id,
            cancellation=cancellation,
        )
        trace = TraceContext.root(item.trace_id).child()
        log_event(
            self.logger,
            logging.INFO,
            "worker.job.claimed",
            "claimed durable job",
            analysis_id=item.analysis_id,
            worker_id=self.settings.worker_id,
            compute_class=item.compute_class.value,
            attempt=item.attempt,
            trace_id=item.trace_id,
        )

        try:
            with TraceSpan(
                logger=self.logger,
                name="worker.job.execute",
                context=trace,
                fields={
                    "analysis_id": item.analysis_id,
                    "worker_id": self.settings.worker_id,
                    "attempt": item.attempt,
                },
            ), _wall_deadline(self.settings.wall_timeout_seconds):
                self.executor(context)
            if lease_lost.is_set():
                raise QueueLeaseError(
                    f"queue lease for {item.analysis_id!r} was lost during execution"
                )
            self.queue.ack(item.analysis_id, worker_id=self.settings.worker_id)
            self.metrics.inc("jobs_completed_total")
            self._consecutive_failures = 0
            log_event(
                self.logger,
                logging.INFO,
                "worker.job.completed",
                "job left the worker boundary cleanly",
                analysis_id=item.analysis_id,
                worker_id=self.settings.worker_id,
                trace_id=item.trace_id,
                cancelled=cancellation.is_set(),
            )
        except WorkerTimeoutError as error:
            self._release_failure(
                item,
                code="WORKER_TIMEOUT",
                error=error,
                retryable=True,
            )
        except QueueLeaseError:
            self.metrics.inc("jobs_lease_conflict_total")
            raise
        except Exception as error:  # noqa: BLE001 - isolation boundary
            self._release_failure(
                item,
                code="WORKER_EXECUTION_ERROR",
                error=error,
                retryable=True,
            )
        finally:
            finished.set()
            heartbeat.join(timeout=max(1.0, self.settings.heartbeat_seconds * 2))
            self.metrics.set("active_jobs", 0.0)
            self._active_analysis_id = None
        return True

    def run_forever(self) -> int:
        self._ready = True
        self.metrics.set("ready", 1.0)
        log_event(
            self.logger,
            logging.INFO,
            "worker.started",
            "worker polling loop started",
            worker_id=self.settings.worker_id,
            compute_class=self.settings.compute_class.value,
        )
        try:
            while not self.stopping:
                try:
                    worked = self.run_once()
                except QueueLeaseError:
                    self._consecutive_failures += 1
                    worked = True
                except Exception as error:  # noqa: BLE001 - keep other jobs alive
                    self.metrics.inc("loop_errors_total")
                    self._consecutive_failures += 1
                    self.alert_sink.emit(
                        WorkerAlert(
                            code="WORKER_LOOP_ERROR",
                            message="worker loop recovered from an infrastructure error",
                            severity="error",
                        )
                    )
                    log_event(
                        self.logger,
                        logging.ERROR,
                        "worker.loop.error",
                        "worker loop recovered and will continue",
                        worker_id=self.settings.worker_id,
                        error_type=type(error).__name__,
                    )
                    worked = True
                if not worked and not self.stopping:
                    self.sleeper(self.settings.poll_seconds)
        finally:
            self._ready = False
            self.metrics.set("ready", 0.0)
            self.metrics.set("worker_up", 0.0)
            log_event(
                self.logger,
                logging.INFO,
                "worker.stopped",
                "worker polling loop stopped",
                worker_id=self.settings.worker_id,
            )
        return 0


__all__ = [
    "WorkerExecutor",
    "WorkerRuntime",
    "WorkerSnapshot",
]
