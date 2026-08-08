from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from soika_uds.contracts import TerritoryContext
from soika_uds.integration import AnalysisRequestV1
from soika_uds.orchestration import InMemoryJobStore, SoikaOrchestrator
from soika_uds.worker import ComputeClass, OrchestratorExecutor, WorkerControl
from soika_uds.worker.cli import (
    _postgres_application_name,
    _read_secret_file,
    _validate_memory_limit,
)
from soika_uds.worker.models import WorkerConfigurationError


class ControlQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, ComputeClass]] = []
        self.cancelled: list[str] = []
        self.retried: list[str] = []

    def enqueue(
        self,
        analysis_id,
        *,
        compute_class,
        priority=0,
        max_attempts=3,
        trace_id=None,
    ):
        from soika_uds.worker import QueueItem

        now = datetime.now(UTC)
        self.enqueued.append((analysis_id, compute_class))
        return QueueItem(
            analysis_id=analysis_id,
            compute_class=compute_class,
            priority=priority,
            attempt=0,
            max_attempts=max_attempts,
            available_at=now,
            trace_id=trace_id or "1" * 32,
            enqueued_at=now,
            updated_at=now,
        )

    def request_cancel(self, analysis_id):
        self.cancelled.append(analysis_id)
        return self.enqueue(analysis_id, compute_class=ComputeClass.CPU)

    def retry(self, analysis_id):
        self.retried.append(analysis_id)
        return self.enqueue(analysis_id, compute_class=ComputeClass.CPU)


def _request(analysis_id: str) -> AnalysisRequestV1:
    return AnalysisRequestV1(
        analysis_id=analysis_id,
        requested_at=datetime.now(UTC),
        territory=TerritoryContext(analysis_id=analysis_id, city="Казань"),
    )


def test_backend_control_submit_is_idempotent_and_routes_compute_class() -> None:
    store = InMemoryJobStore()
    orchestrator = SoikaOrchestrator(store, {})
    queue = ControlQueue()
    control = WorkerControl(orchestrator, queue)
    request = _request("stage14-control-submit")

    first_record, first_item = control.submit(
        request,
        compute_class=ComputeClass.GPU,
    )
    second_record, second_item = control.submit(
        request,
        compute_class=ComputeClass.GPU,
    )

    assert first_record.analysis_id == second_record.analysis_id
    assert first_item.compute_class is ComputeClass.GPU
    assert second_item.compute_class is ComputeClass.GPU
    assert queue.enqueued == [
        (request.analysis_id, ComputeClass.GPU),
        (request.analysis_id, ComputeClass.GPU),
    ]


def test_orchestrator_executor_renews_only_owned_lease() -> None:
    store = InMemoryJobStore()
    orchestrator = SoikaOrchestrator(
        store,
        {},
        worker_id="stage14-owner",
        lease_ttl=timedelta(minutes=10),
    )
    record = orchestrator.submit(_request("stage14-renew-owned"))
    now = datetime.now(UTC)
    leased = replace(
        record,
        lease_owner="stage14-owner",
        lease_expires_at=now + timedelta(seconds=5),
        updated_at=now,
    )
    leased = store.save(leased, expected_revision=record.revision)

    OrchestratorExecutor._renew(orchestrator, leased.analysis_id)
    renewed = store.load(leased.analysis_id)

    assert renewed.lease_owner == "stage14-owner"
    assert renewed.lease_expires_at is not None
    assert renewed.lease_expires_at > leased.lease_expires_at


def test_database_dsn_is_read_from_secret_file_not_argv(tmp_path: Path) -> None:
    secret = tmp_path / "db-dsn"
    secret.write_text(
        "postgresql://worker:top-secret@database/geoanalyzer\n",
        encoding="utf-8",
    )

    assert _read_secret_file(secret).endswith("@database/geoanalyzer")
    assert _postgres_application_name("w" * 100) == "w" * 63

    empty = tmp_path / "empty"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(WorkerConfigurationError):
        _read_secret_file(empty)


def test_memory_limit_fails_closed_when_cgroup_is_unbounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "soika_uds.worker.cli._cgroup_memory_limit_bytes",
        lambda: None,
    )

    with pytest.raises(WorkerConfigurationError, match="finite"):
        _validate_memory_limit(4096)
