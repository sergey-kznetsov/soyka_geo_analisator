from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from geoanalyzer_storage import MigrationRunner, PostgresDatabase, PostgresSettings
from soika_uds.contracts import TerritoryContext
from soika_uds.integration import AnalysisRequestV1
from soika_uds.orchestration import JobRecord, PostgresJobStore
from soika_uds.worker import ComputeClass, PostgresJobQueue

_RUN_TOKEN = uuid.uuid4().hex[:12]


@pytest.fixture(scope="module")
def database() -> PostgresDatabase:
    dsn = os.environ.get("GEOANALYZER_TEST_DATABASE_DSN", "").strip()
    if not dsn:
        pytest.skip("GEOANALYZER_TEST_DATABASE_DSN is not configured")
    database = PostgresDatabase(
        PostgresSettings(
            dsn=dsn,
            application_name=f"stage14-queue-{_RUN_TOKEN}",
            min_pool_size=0,
            max_pool_size=8,
        )
    )
    MigrationRunner(database, scope="platform").apply()
    MigrationRunner(database, scope="soika").apply()
    MigrationRunner(database, scope="worker").apply()
    try:
        yield database
    finally:
        database.close()


def _analysis_id(name: str) -> str:
    return f"stage14-{name}-{_RUN_TOKEN}"


def _create_job(database: PostgresDatabase, analysis_id: str) -> None:
    now = datetime.now(UTC)
    request = AnalysisRequestV1(
        analysis_id=analysis_id,
        requested_at=now,
        territory=TerritoryContext(analysis_id=analysis_id, city="Казань"),
    )
    PostgresJobStore(database).create_idempotent(JobRecord.new(request, now))


def test_worker_scope_is_independent_and_idempotent(
    database: PostgresDatabase,
) -> None:
    assert MigrationRunner(database, scope="worker").apply() == ()
    with database.connection() as connection:
        row = connection.execute(
            "SELECT version, name FROM ga_meta.schema_migrations "
            "WHERE scope = 'worker' ORDER BY version"
        ).fetchall()
        indexes = {
            item[0]
            for item in connection.execute(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = 'ga_core' AND tablename = 'job_queue'"
            ).fetchall()
        }

    assert row == [(1, "worker_queue")]
    assert "idx_ga_core_job_queue_claim" in indexes
    assert "idx_ga_core_job_queue_lease" in indexes


def test_cpu_gpu_routing_and_priority_are_enforced(
    database: PostgresDatabase,
) -> None:
    queue = PostgresJobQueue(database)
    cpu_low = _analysis_id("cpu-low")
    cpu_high = _analysis_id("cpu-high")
    gpu = _analysis_id("gpu")
    for analysis_id in (cpu_low, cpu_high, gpu):
        _create_job(database, analysis_id)

    queue.enqueue(cpu_low, compute_class=ComputeClass.CPU, priority=0)
    queue.enqueue(cpu_high, compute_class=ComputeClass.CPU, priority=10)
    queue.enqueue(gpu, compute_class=ComputeClass.GPU, priority=100)

    first_cpu = queue.claim(
        worker_id="stage14-cpu-1",
        compute_class=ComputeClass.CPU,
        lease_seconds=60,
    )
    first_gpu = queue.claim(
        worker_id="stage14-gpu-1",
        compute_class=ComputeClass.GPU,
        lease_seconds=60,
    )
    second_cpu = queue.claim(
        worker_id="stage14-cpu-2",
        compute_class=ComputeClass.CPU,
        lease_seconds=60,
    )

    assert first_cpu is not None and first_cpu.analysis_id == cpu_high
    assert first_gpu is not None and first_gpu.analysis_id == gpu
    assert second_cpu is not None and second_cpu.analysis_id == cpu_low

    queue.ack(cpu_high, worker_id="stage14-cpu-1")
    queue.ack(gpu, worker_id="stage14-gpu-1")
    queue.ack(cpu_low, worker_id="stage14-cpu-2")


def test_concurrent_workers_claim_distinct_jobs(
    database: PostgresDatabase,
) -> None:
    queue = PostgresJobQueue(database)
    analysis_ids = tuple(_analysis_id(f"parallel-{index}") for index in range(2))
    for analysis_id in analysis_ids:
        _create_job(database, analysis_id)
        queue.enqueue(analysis_id, compute_class=ComputeClass.CPU)

    def claim(worker_id: str):
        return queue.claim(
            worker_id=worker_id,
            compute_class=ComputeClass.CPU,
            lease_seconds=60,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed = tuple(
            executor.map(claim, ("stage14-parallel-1", "stage14-parallel-2"))
        )

    assert all(item is not None for item in claimed)
    assert {item.analysis_id for item in claimed if item is not None} == set(analysis_ids)
    for worker_id, item in zip(
        ("stage14-parallel-1", "stage14-parallel-2"),
        claimed,
        strict=True,
    ):
        assert item is not None
        queue.ack(item.analysis_id, worker_id=worker_id)


def test_retry_exhaustion_and_explicit_retry_are_durable(
    database: PostgresDatabase,
) -> None:
    queue = PostgresJobQueue(database)
    analysis_id = _analysis_id("retry")
    _create_job(database, analysis_id)
    queue.enqueue(
        analysis_id,
        compute_class=ComputeClass.CPU,
        max_attempts=2,
    )

    for attempt in (1, 2):
        item = queue.claim(
            worker_id="stage14-retry",
            compute_class=ComputeClass.CPU,
            lease_seconds=60,
        )
        assert item is not None and item.attempt == attempt
        released = queue.release(
            analysis_id,
            worker_id="stage14-retry",
            retryable=True,
            retry_delay_seconds=0,
            error={"code": "FIXTURE_FAILURE"},
        )

    assert released.exhausted is True
    assert (
        queue.claim(
            worker_id="stage14-retry",
            compute_class=ComputeClass.CPU,
            lease_seconds=60,
        )
        is None
    )

    reset = queue.retry(analysis_id)
    assert reset.attempt == 0
    claimed = queue.claim(
        worker_id="stage14-retry",
        compute_class=ComputeClass.CPU,
        lease_seconds=60,
    )
    assert claimed is not None and claimed.attempt == 1
    queue.ack(analysis_id, worker_id="stage14-retry")


def test_cancelled_queue_item_is_not_claimed(
    database: PostgresDatabase,
) -> None:
    queue = PostgresJobQueue(database)
    analysis_id = _analysis_id("cancel")
    _create_job(database, analysis_id)
    queue.enqueue(analysis_id, compute_class=ComputeClass.CPU)

    cancelled = queue.request_cancel(analysis_id)

    assert cancelled.cancel_requested is True
    assert queue.is_cancel_requested(analysis_id) is True
    assert (
        queue.claim(
            worker_id="stage14-cancel",
            compute_class=ComputeClass.CPU,
            lease_seconds=60,
        )
        is None
    )
