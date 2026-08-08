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
from soika_uds.worker import ComputeClass, PostgresJobQueue, QueueLeaseError

_RUN_TOKEN = uuid.uuid4().hex[:12]


@pytest.fixture(scope="module")
def database() -> PostgresDatabase:
    dsn = os.environ.get("GEOANALYZER_TEST_DATABASE_DSN", "").strip()
    if not dsn:
        pytest.skip("GEOANALYZER_TEST_DATABASE_DSN is not configured")
    database = PostgresDatabase(
        PostgresSettings(
            dsn=dsn,
            application_name=f"stage16-release-candidate-{_RUN_TOKEN}",
            min_pool_size=0,
            max_pool_size=16,
        )
    )
    for scope in ("platform", "soika", "worker"):
        MigrationRunner(database, scope=scope).apply()
    try:
        yield database
    finally:
        database.close()


def _analysis_id(name: str) -> str:
    return f"stage16-{name}-{_RUN_TOKEN}"


def _create_job(database: PostgresDatabase, analysis_id: str) -> None:
    now = datetime.now(UTC)
    request = AnalysisRequestV1(
        analysis_id=analysis_id,
        requested_at=now,
        territory=TerritoryContext(
            analysis_id=analysis_id,
            city="Казань",
            address="Кремль",
        ),
    )
    PostgresJobStore(database).create_idempotent(JobRecord.new(request, now))


def _enqueue_batch(database: PostgresDatabase, prefix: str, count: int) -> tuple[str, ...]:
    queue = PostgresJobQueue(database)
    analysis_ids = tuple(_analysis_id(f"{prefix}-{index:03d}") for index in range(count))
    for analysis_id in analysis_ids:
        _create_job(database, analysis_id)
        queue.enqueue(analysis_id, compute_class=ComputeClass.CPU)
    return analysis_ids


def test_parallel_queue_load_claims_each_job_exactly_once(database: PostgresDatabase) -> None:
    queue = PostgresJobQueue(database)
    expected = set(_enqueue_batch(database, "load", 64))

    def drain(worker_index: int) -> tuple[str, ...]:
        worker_id = f"stage16-load-{worker_index}"
        claimed: list[str] = []
        while True:
            item = queue.claim(
                worker_id=worker_id,
                compute_class=ComputeClass.CPU,
                lease_seconds=30,
            )
            if item is None:
                break
            claimed.append(item.analysis_id)
            queue.ack(item.analysis_id, worker_id=worker_id)
        return tuple(claimed)

    with ThreadPoolExecutor(max_workers=8) as executor:
        batches = tuple(executor.map(drain, range(8)))

    claimed = [analysis_id for batch in batches for analysis_id in batch]
    assert len(claimed) == len(expected)
    assert len(set(claimed)) == len(expected)
    assert set(claimed) == expected


def test_soak_cycles_leave_queue_healthy_and_empty(database: PostgresDatabase) -> None:
    queue = PostgresJobQueue(database)

    for cycle in range(10):
        expected = set(_enqueue_batch(database, f"soak-{cycle}", 8))
        claimed: set[str] = set()
        while True:
            item = queue.claim(
                worker_id=f"stage16-soak-{cycle}",
                compute_class=ComputeClass.CPU,
                lease_seconds=30,
            )
            if item is None:
                break
            claimed.add(item.analysis_id)
            queue.ack(item.analysis_id, worker_id=f"stage16-soak-{cycle}")
        assert claimed == expected
        assert queue.healthcheck() is True

    stats = queue.stats(ComputeClass.CPU)
    assert stats.ready == 0
    assert stats.leased == 0
    assert stats.delayed == 0


def test_expired_worker_lease_is_recovered_without_old_owner_ack(database: PostgresDatabase) -> None:
    queue = PostgresJobQueue(database)
    analysis_id = _analysis_id("failure-injection")
    _create_job(database, analysis_id)
    queue.enqueue(analysis_id, compute_class=ComputeClass.CPU, max_attempts=3)

    first = queue.claim(
        worker_id="stage16-dead-worker",
        compute_class=ComputeClass.CPU,
        lease_seconds=30,
    )
    assert first is not None and first.attempt == 1

    with database.connection() as connection:
        connection.execute(
            "UPDATE ga_core.job_queue SET lease_expires_at = "
            "clock_timestamp() - interval '1 second' "
            "WHERE application_id = 'soika' AND analysis_id = %s",
            (analysis_id,),
        )

    recovered = queue.claim(
        worker_id="stage16-recovery-worker",
        compute_class=ComputeClass.CPU,
        lease_seconds=30,
    )
    assert recovered is not None
    assert recovered.analysis_id == analysis_id
    assert recovered.attempt == 2

    with pytest.raises(QueueLeaseError):
        queue.ack(analysis_id, worker_id="stage16-dead-worker")

    queue.ack(analysis_id, worker_id="stage16-recovery-worker")
