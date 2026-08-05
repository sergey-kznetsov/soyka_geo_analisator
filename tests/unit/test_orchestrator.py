from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from soika_uds.contracts import JobStatus, TerritoryContext
from soika_uds.integration import (
    AnalysisRequestV1,
    ContractIssue,
    IdempotencyConflictError,
    ResultProvenance,
)
from soika_uds.orchestration import (
    PIPELINE_STAGES,
    CheckpointState,
    ConcurrentUpdateError,
    FileJobStore,
    InMemoryJobStore,
    JobLeaseError,
    JobRecord,
    PermanentStageError,
    PipelineStage,
    RetryableStageError,
    RetryPolicy,
    SoikaOrchestrator,
    StageResult,
)


@dataclass
class MutableClock:
    current: datetime

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: int) -> None:
        self.current += timedelta(**kwargs)


def make_request(
    analysis_id: str = "analysis-1",
    *,
    radius_meters: int = 1500,
    idempotency_key: str | None = None,
) -> AnalysisRequestV1:
    return AnalysisRequestV1(
        analysis_id=analysis_id,
        requested_at=datetime(2026, 8, 5, 8, 0, tzinfo=UTC),
        territory=TerritoryContext(
            analysis_id=analysis_id,
            city="Ижевск",
            address="Пушкинская улица, 277",
            latitude=56.8701,
            longitude=53.2143,
            radius_meters=radius_meters,
        ),
        sources=("fixture",),
        options={"language": "ru"},
        idempotency_key=idempotency_key,
    )


def successful_handlers(calls: list[PipelineStage] | None = None):
    calls = calls if calls is not None else []

    def handler(context):
        calls.append(context.stage)
        output = {"stage": context.stage.value}
        if context.stage is PipelineStage.COLLECTION:
            output["coverage"] = {
                "sources_requested": 1,
                "sources_available": 1,
                "messages_collected": 2,
                "messages_relevant": 2,
                "messages_geocoded": 2,
                "messages_low_confidence": 0,
            }
        if context.stage is PipelineStage.FINALIZING:
            output.update(
                {
                    "categories": [{"name": "ЖКХ", "count": 2}],
                    "risk_summary": {"score": 1.5},
                    "geojson": {"type": "FeatureCollection", "features": []},
                    "metadata": {"source": "fixture"},
                }
            )
        return StageResult(output=output, processed_items=1, total_items=1)

    return {stage: handler for stage in PIPELINE_STAGES}


def test_full_pipeline_completes_and_persists_every_checkpoint():
    clock = MutableClock(datetime(2026, 8, 5, 8, 0, tzinfo=UTC))
    calls: list[PipelineStage] = []
    store = InMemoryJobStore()
    orchestrator = SoikaOrchestrator(
        store,
        successful_handlers(calls),
        worker_id="worker-a",
        clock=clock,
    )

    record = orchestrator.run(make_request())

    assert record.status is JobStatus.COMPLETED
    assert record.progress_percent == 100
    assert calls == list(PIPELINE_STAGES)
    assert all(
        checkpoint.state is CheckpointState.COMPLETED
        for checkpoint in record.checkpoints
    )
    assert record.lease_owner is None
    assert orchestrator.status(record.analysis_id).status is JobStatus.COMPLETED


def test_duplicate_submission_is_idempotent_and_does_not_rerun_handlers():
    calls: list[PipelineStage] = []
    orchestrator = SoikaOrchestrator(
        InMemoryJobStore(),
        successful_handlers(calls),
        worker_id="worker-a",
    )
    request = make_request()

    first = orchestrator.run(request)
    second = orchestrator.run(request)

    assert first.revision == second.revision
    assert calls == list(PIPELINE_STAGES)


def test_changed_request_with_same_analysis_id_is_rejected():
    orchestrator = SoikaOrchestrator(
        InMemoryJobStore(),
        successful_handlers(),
        worker_id="worker-a",
    )
    orchestrator.submit(make_request(radius_meters=1000))

    with pytest.raises(IdempotencyConflictError):
        orchestrator.submit(make_request(radius_meters=2000))


def test_same_explicit_idempotency_key_cannot_create_another_analysis():
    orchestrator = SoikaOrchestrator(
        InMemoryJobStore(),
        successful_handlers(),
        worker_id="worker-a",
    )
    key = "shared-idempotency-key-0001"
    orchestrator.submit(make_request("analysis-1", idempotency_key=key))

    with pytest.raises(IdempotencyConflictError):
        orchestrator.submit(make_request("analysis-2", idempotency_key=key))


def test_retryable_stage_uses_policy_and_then_succeeds():
    attempts: list[int] = []
    delays: list[float] = []
    handlers = successful_handlers()

    def collection(context):
        attempts.append(context.attempt)
        if context.attempt < 3:
            raise RetryableStageError(
                "SOURCE_TEMPORARILY_UNAVAILABLE",
                "temporary source failure",
            )
        return StageResult(output={"messages": []})

    handlers[PipelineStage.COLLECTION] = collection
    orchestrator = SoikaOrchestrator(
        InMemoryJobStore(),
        handlers,
        worker_id="worker-a",
        retry_policies={
            PipelineStage.COLLECTION: RetryPolicy(
                max_attempts=3,
                initial_delay_seconds=2,
                multiplier=2,
            )
        },
        sleeper=delays.append,
    )

    record = orchestrator.run(make_request())

    assert record.status is JobStatus.COMPLETED
    assert attempts == [1, 2, 3]
    assert delays == [2, 4]
    assert record.checkpoint(PipelineStage.COLLECTION).attempt == 3


def test_permanent_stage_error_terminates_job_with_structured_issue():
    handlers = successful_handlers()

    def geolocation(_context):
        raise PermanentStageError(
            "GEOCODING_CONFIGURATION_ERROR",
            "geocoder is not configured",
            details={"component": "geocoder"},
        )

    handlers[PipelineStage.GEOLOCATION] = geolocation
    orchestrator = SoikaOrchestrator(
        InMemoryJobStore(),
        handlers,
        worker_id="worker-a",
    )

    record = orchestrator.run(make_request())

    assert record.status is JobStatus.FAILED
    assert record.current_stage is PipelineStage.GEOLOCATION
    assert record.errors[0].code == "GEOCODING_CONFIGURATION_ERROR"
    assert record.errors[0].details["component"] == "geocoder"
    assert (
        record.checkpoint(PipelineStage.GEOLOCATION).state
        is CheckpointState.FAILED
    )


def test_explicit_retry_restarts_only_failed_and_later_stages():
    calls: list[PipelineStage] = []
    fail_once = True

    def handler(context):
        nonlocal fail_once
        calls.append(context.stage)
        if context.stage is PipelineStage.NLP and fail_once:
            fail_once = False
            raise PermanentStageError("NLP_FAILURE", "first attempt failed")
        return StageResult(output={"stage": context.stage.value})

    handlers = {stage: handler for stage in PIPELINE_STAGES}
    orchestrator = SoikaOrchestrator(
        InMemoryJobStore(),
        handlers,
        worker_id="worker-a",
    )

    failed = orchestrator.run(make_request())
    retried = orchestrator.retry_failed(failed.analysis_id)
    completed = orchestrator.resume(retried.analysis_id)

    assert failed.status is JobStatus.FAILED
    assert completed.status is JobStatus.COMPLETED
    assert completed.job_attempt == 2
    assert calls.count(PipelineStage.PREPARING) == 1
    assert calls.count(PipelineStage.NLP) == 2


def test_worker_crash_is_recovered_from_running_checkpoint_after_lease_expiry():
    clock = MutableClock(datetime(2026, 8, 5, 8, 0, tzinfo=UTC))
    crashed = False
    calls: list[PipelineStage] = []

    def handler(context):
        nonlocal crashed
        calls.append(context.stage)
        if context.stage is PipelineStage.PREPROCESSING and not crashed:
            crashed = True
            raise KeyboardInterrupt("simulated worker termination")
        return StageResult(output={"stage": context.stage.value})

    handlers = {stage: handler for stage in PIPELINE_STAGES}
    store = InMemoryJobStore()
    first_worker = SoikaOrchestrator(
        store,
        handlers,
        worker_id="worker-a",
        lease_ttl=timedelta(seconds=30),
        clock=clock,
    )

    with pytest.raises(KeyboardInterrupt):
        first_worker.run(make_request())

    crashed_record = store.load("analysis-1")
    assert crashed_record.current_stage is PipelineStage.PREPROCESSING
    assert (
        crashed_record.checkpoint(PipelineStage.PREPROCESSING).state
        is CheckpointState.RUNNING
    )

    clock.advance(seconds=31)
    second_worker = SoikaOrchestrator(
        store,
        handlers,
        worker_id="worker-b",
        lease_ttl=timedelta(seconds=30),
        clock=clock,
    )
    recovered = second_worker.resume("analysis-1")

    assert recovered.status is JobStatus.COMPLETED
    assert calls.count(PipelineStage.PREPARING) == 1
    assert calls.count(PipelineStage.COLLECTION) == 1
    assert calls.count(PipelineStage.PREPROCESSING) == 2


def test_live_lease_prevents_second_worker_from_running_same_job():
    clock = MutableClock(datetime(2026, 8, 5, 8, 0, tzinfo=UTC))
    store = InMemoryJobStore()
    handlers = successful_handlers()
    first = SoikaOrchestrator(
        store,
        handlers,
        worker_id="worker-a",
        lease_ttl=timedelta(minutes=5),
        clock=clock,
    )
    record = first.submit(make_request())
    leased = first._acquire_lease(record)
    assert leased.lease_owner == "worker-a"

    second = SoikaOrchestrator(
        store,
        handlers,
        worker_id="worker-b",
        lease_ttl=timedelta(minutes=5),
        clock=clock,
    )
    with pytest.raises(JobLeaseError):
        second.resume(record.analysis_id)


def test_cancel_requested_during_stage_is_preserved_after_checkpoint_commit():
    store = InMemoryJobStore()
    orchestrator: SoikaOrchestrator

    def handler(context):
        if context.stage is PipelineStage.PREPARING:
            orchestrator.request_cancel(context.request.analysis_id)
        return StageResult(output={"stage": context.stage.value})

    orchestrator = SoikaOrchestrator(
        store,
        {stage: handler for stage in PIPELINE_STAGES},
        worker_id="worker-a",
    )

    record = orchestrator.run(make_request())

    assert record.status is JobStatus.CANCELLED
    assert record.checkpoint(PipelineStage.PREPARING).state is CheckpointState.COMPLETED
    assert record.checkpoint(PipelineStage.COLLECTION).state is CheckpointState.PENDING


def test_non_json_stage_output_is_rejected_without_dataframe_leakage():
    class FakeDataFrame:
        pass

    handlers = successful_handlers()
    handlers[PipelineStage.NLP] = lambda _context: StageResult(
        output={"frame": FakeDataFrame()}
    )
    orchestrator = SoikaOrchestrator(
        InMemoryJobStore(),
        handlers,
        worker_id="worker-a",
    )

    record = orchestrator.run(make_request())

    assert record.status is JobStatus.FAILED
    assert record.errors[0].code == "INVALID_STAGE_OUTPUT"


def test_file_store_survives_new_store_instance(tmp_path):
    request = make_request()
    first_store = FileJobStore(tmp_path)
    created = first_store.create(
        JobRecord.new(request, datetime(2026, 8, 5, 8, 0, tzinfo=UTC))
    )

    restored = FileJobStore(tmp_path).load(request.analysis_id)

    assert restored.to_dict() == created.to_dict()


def test_store_rejects_stale_revision():
    store = InMemoryJobStore()
    record = store.create(
        JobRecord.new(
            make_request(), datetime(2026, 8, 5, 8, 0, tzinfo=UTC)
        )
    )
    updated = store.save(record, expected_revision=record.revision)

    with pytest.raises(ConcurrentUpdateError):
        store.save(record, expected_revision=record.revision)

    assert updated.revision == record.revision + 1


def test_materialized_result_uses_structured_stage_outputs():
    orchestrator = SoikaOrchestrator(
        InMemoryJobStore(),
        successful_handlers(),
        worker_id="worker-a",
    )
    record = orchestrator.run(make_request())
    provenance = ResultProvenance(
        soika_version="0.5.0",
        schema_digest="a" * 64,
    )

    result = orchestrator.materialize_result(record.analysis_id, provenance)

    assert result.status is JobStatus.COMPLETED
    assert result.coverage.messages_collected == 2
    assert result.categories[0]["name"] == "ЖКХ"
    assert result.metadata["orchestration"]["completed_stages"] == [
        stage.value for stage in PIPELINE_STAGES
    ]


def test_stage_warning_produces_completed_with_warnings():
    handlers = successful_handlers()
    handlers[PipelineStage.COLLECTION] = lambda _context: StageResult(
        output={},
        warnings=(
            ContractIssue(
                code="SOURCE_PARTIAL",
                message="source returned partial data",
                retryable=False,
                stage="collection",
            ),
        ),
    )
    orchestrator = SoikaOrchestrator(
        InMemoryJobStore(),
        handlers,
        worker_id="worker-a",
    )

    record = orchestrator.run(make_request())

    assert record.status is JobStatus.COMPLETED_WITH_WARNINGS
    assert record.warnings[0].code == "SOURCE_PARTIAL"
