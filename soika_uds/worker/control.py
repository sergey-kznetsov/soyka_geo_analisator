"""Backend-only worker control primitives and orchestration adapter."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event, Thread

from ..integration import AnalysisRequestV1
from ..orchestration import ConcurrentUpdateError, JobLeaseError, JobRecord, SoikaOrchestrator
from .models import ComputeClass, QueueItem, WorkerContext
from .queue import JobQueue


class WorkerControl:
    """Transport-neutral control surface for the Geo Analyzer backend."""

    def __init__(self, orchestrator: SoikaOrchestrator, queue: JobQueue) -> None:
        if not isinstance(orchestrator, SoikaOrchestrator):
            raise TypeError("orchestrator must be SoikaOrchestrator")
        self.orchestrator = orchestrator
        self.queue = queue

    def submit(
        self,
        request: AnalysisRequestV1,
        *,
        compute_class: ComputeClass = ComputeClass.CPU,
        priority: int = 0,
        max_worker_attempts: int = 3,
        trace_id: str | None = None,
    ) -> tuple[JobRecord, QueueItem]:
        record = self.orchestrator.submit(request)
        item = self.queue.enqueue(
            record.analysis_id,
            compute_class=compute_class,
            priority=priority,
            max_attempts=max_worker_attempts,
            trace_id=trace_id,
        )
        return record, item

    def cancel(self, analysis_id: str) -> JobRecord:
        record = self.orchestrator.request_cancel(analysis_id)
        try:
            self.queue.request_cancel(analysis_id)
        except Exception:  # noqa: BLE001 - job cancellation remains canonical
            if not record.terminal:
                raise
        return record

    def retry(self, analysis_id: str) -> JobRecord:
        record = self.orchestrator.retry_failed(analysis_id)
        self.queue.retry(analysis_id)
        return record


class OrchestratorExecutor:
    """Run one durable job while renewing its orchestration lease."""

    def __init__(
        self,
        factory: Callable[[WorkerContext], SoikaOrchestrator],
        *,
        heartbeat_seconds: float = 30.0,
    ) -> None:
        if not callable(factory):
            raise TypeError("factory must be callable")
        if heartbeat_seconds <= 0:
            raise ValueError("heartbeat_seconds must be positive")
        self.factory = factory
        self.heartbeat_seconds = float(heartbeat_seconds)

    @staticmethod
    def _request_cancel(orchestrator: SoikaOrchestrator, analysis_id: str) -> None:
        for _ in range(3):
            try:
                orchestrator.request_cancel(analysis_id)
                return
            except ConcurrentUpdateError:
                continue

    def __call__(self, context: WorkerContext) -> JobRecord:
        orchestrator = self.factory(context)
        if not isinstance(orchestrator, SoikaOrchestrator):
            raise TypeError("orchestrator factory must return SoikaOrchestrator")
        finished = Event()

        def _monitor() -> None:
            while not finished.wait(self.heartbeat_seconds):
                if context.cancel_requested:
                    self._request_cancel(orchestrator, context.analysis_id)
                try:
                    orchestrator.renew_lease(context.analysis_id)
                except JobLeaseError:
                    continue
                except ConcurrentUpdateError:
                    continue

        monitor = Thread(
            target=_monitor,
            name=f"soika-job-lease-{context.analysis_id}",
            daemon=True,
        )
        monitor.start()
        try:
            if context.cancel_requested:
                self._request_cancel(orchestrator, context.analysis_id)
            return orchestrator.resume(context.analysis_id)
        finally:
            finished.set()
            monitor.join(timeout=max(1.0, self.heartbeat_seconds * 2))


__all__ = ["OrchestratorExecutor", "WorkerControl"]
