"""Crash-recoverable finite-state orchestrator for SOIKA analysis jobs."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, Protocol
from uuid import uuid4

from ..contracts import CoverageSummary, JobStatus
from ..integration import (
    AnalysisRequestV1,
    AnalysisResultV1,
    ContractIssue,
    IdempotencyConflictError,
    ResultProvenance,
)
from .models import (
    PIPELINE_STAGES,
    TERMINAL_JOB_STATUSES,
    CheckpointState,
    InvalidStageOutputError,
    ConcurrentUpdateError,
    JobLeaseError,
    JobRecord,
    MissingStageHandlerError,
    OrchestrationError,
    PermanentStageError,
    PipelineStage,
    RetryPolicy,
    RetryableStageError,
    StageCheckpoint,
    StageExecutionError,
    StageResult,
    stage_job_status,
    stage_progress,
)


class JobStoreProtocol(Protocol):
    def create(self, record: JobRecord) -> JobRecord:
        ...

    def create_idempotent(self, record: JobRecord) -> JobRecord:
        ...

    def load(self, analysis_id: str) -> JobRecord:
        ...

    def save(self, record: JobRecord, *, expected_revision: int) -> JobRecord:
        ...

    def list_records(self) -> tuple[JobRecord, ...]:
        ...

    def find_by_idempotency_key(self, key: str) -> JobRecord | None:
        ...


@dataclass(frozen=True, slots=True)
class StageContext:
    request: AnalysisRequestV1
    stage: PipelineStage
    attempt: int
    worker_id: str
    previous_outputs: Mapping[str, Mapping[str, Any]]


class StageHandler(Protocol):
    def run(self, context: StageContext) -> StageResult:
        ...


@dataclass(frozen=True, slots=True)
class CallableStageHandler:
    function: Callable[[StageContext], StageResult]

    def run(self, context: StageContext) -> StageResult:
        return self.function(context)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SoikaOrchestrator:
    """Persist every stage boundary and resume from the last checkpoint."""

    def __init__(
        self,
        store: JobStoreProtocol,
        handlers: Mapping[
            PipelineStage, StageHandler | Callable[[StageContext], StageResult]
        ],
        *,
        retry_policies: Mapping[PipelineStage, RetryPolicy] | None = None,
        worker_id: str | None = None,
        lease_ttl: timedelta | None = None,
        clock: Callable[[], datetime] = _utc_now,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        resolved_lease_ttl = lease_ttl or timedelta(minutes=5)
        if resolved_lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be positive")
        self.store = store
        self.worker_id = worker_id or f"soika-worker-{uuid4().hex}"
        self.lease_ttl = resolved_lease_ttl
        self.clock = clock
        self.sleeper = sleeper
        self.handlers = MappingProxyType(
            {
                PipelineStage(stage): (
                    handler
                    if hasattr(handler, "run")
                    else CallableStageHandler(handler)
                )
                for stage, handler in handlers.items()
            }
        )
        self.retry_policies = MappingProxyType(
            {
                stage: (retry_policies or {}).get(stage, RetryPolicy())
                for stage in PIPELINE_STAGES
            }
        )

    def validate_pipeline(self) -> None:
        missing = [
            stage.value for stage in PIPELINE_STAGES if stage not in self.handlers
        ]
        if missing:
            raise MissingStageHandlerError(
                f"missing stage handlers: {', '.join(missing)}"
            )

    def submit(self, request: AnalysisRequestV1) -> JobRecord:
        candidate = JobRecord.new(request, self._now())
        persisted = self.store.create_idempotent(candidate)
        self._assert_same_request(persisted, request)
        return persisted

    @staticmethod
    def _assert_same_request(record: JobRecord, request: AnalysisRequestV1) -> None:
        if (
            record.request_fingerprint != request.fingerprint
            or record.idempotency_key != request.effective_idempotency_key
        ):
            raise IdempotencyConflictError(
                "persisted analysis_id or idempotency_key belongs to a "
                "different request"
            )

    def run(self, request: AnalysisRequestV1) -> JobRecord:
        record = self.submit(request)
        if record.terminal:
            return record
        return self.resume(record.analysis_id)

    def resume(self, analysis_id: str) -> JobRecord:
        self.validate_pipeline()
        record = self._acquire_lease(self.store.load(analysis_id))
        if record.terminal:
            return self._release_lease(record)

        while True:
            if record.cancel_requested:
                return self._cancel(record)

            stage = record.next_stage
            if stage is None:
                return self._complete(record)

            checkpoint = record.checkpoint(stage)
            policy = self.retry_policies[stage]
            if checkpoint.attempt >= policy.max_attempts:
                issue = checkpoint.error or ContractIssue(
                    code="STAGE_ATTEMPTS_EXHAUSTED",
                    message=f"stage {stage.value} exhausted its retry policy",
                    retryable=False,
                    stage=stage.value,
                    details={"max_attempts": policy.max_attempts},
                )
                return self._fail(record, checkpoint, issue)

            record = self._start_stage(record, checkpoint)
            checkpoint = record.checkpoint(stage)
            context = StageContext(
                request=record.request,
                stage=stage,
                attempt=checkpoint.attempt,
                worker_id=self.worker_id,
                previous_outputs=record.completed_outputs,
            )

            try:
                result = self.handlers[stage].run(context)
                record = self._latest_stage_record(record, stage, checkpoint.attempt)
                checkpoint = record.checkpoint(stage)
                if not isinstance(result, StageResult):
                    raise InvalidStageOutputError(
                        f"stage {stage.value} must return StageResult"
                    )
            except RetryableStageError as error:
                record = self._latest_stage_record(record, stage, checkpoint.attempt)
                checkpoint = record.checkpoint(stage)
                record = self._record_stage_error(record, checkpoint, error)
                if checkpoint.attempt < policy.max_attempts:
                    self.sleeper(policy.delay_after(checkpoint.attempt))
                    continue
                return self._fail(
                    record,
                    record.checkpoint(stage),
                    self._issue_from_stage_error(stage, error, retryable=False),
                )
            except (PermanentStageError, InvalidStageOutputError) as error:
                record = self._latest_stage_record(record, stage, checkpoint.attempt)
                checkpoint = record.checkpoint(stage)
                stage_error = (
                    error
                    if isinstance(error, StageExecutionError)
                    else PermanentStageError(
                        "INVALID_STAGE_OUTPUT",
                        str(error),
                    )
                )
                record = self._record_stage_error(record, checkpoint, stage_error)
                return self._fail(
                    record,
                    record.checkpoint(stage),
                    self._issue_from_stage_error(stage, stage_error, retryable=False),
                )
            except OrchestrationError:
                raise
            except Exception as error:  # noqa: BLE001
                record = self._latest_stage_record(record, stage, checkpoint.attempt)
                checkpoint = record.checkpoint(stage)
                stage_error = PermanentStageError(
                    "UNHANDLED_STAGE_ERROR",
                    str(error) or type(error).__name__,
                    details={"exception_type": type(error).__name__},
                )
                record = self._record_stage_error(record, checkpoint, stage_error)
                return self._fail(
                    record,
                    record.checkpoint(stage),
                    self._issue_from_stage_error(stage, stage_error, retryable=False),
                )

            record = self._complete_stage(record, checkpoint, result)

    def request_cancel(self, analysis_id: str) -> JobRecord:
        record = self.store.load(analysis_id)
        if record.terminal:
            return record
        now = self._now()
        updated = replace(record, cancel_requested=True, updated_at=now)
        if record.status is JobStatus.QUEUED and record.lease_owner is None:
            updated = replace(
                updated,
                status=JobStatus.CANCELLED,
                current_stage=None,
            )
        return self.store.save(updated, expected_revision=record.revision)

    def retry_failed(self, analysis_id: str) -> JobRecord:
        record = self.store.load(analysis_id)
        if record.status is not JobStatus.FAILED:
            raise OrchestrationError("only a failed job can be retried explicitly")
        failed = next(
            (
                checkpoint
                for checkpoint in record.checkpoints
                if checkpoint.state is CheckpointState.FAILED
            ),
            None,
        )
        if failed is None:
            raise OrchestrationError("failed job has no failed checkpoint")
        reset = replace(
            failed,
            state=CheckpointState.PENDING,
            attempt=0,
            started_at=None,
            completed_at=None,
            updated_at=self._now(),
            processed_items=None,
            total_items=None,
            output={},
            warnings=(),
            error=None,
        )
        updated = record.replace_checkpoint(reset)
        updated = replace(
            updated,
            status=JobStatus.QUEUED,
            current_stage=None,
            cancel_requested=False,
            errors=(),
            job_attempt=record.job_attempt + 1,
            updated_at=self._now(),
            lease_owner=None,
            lease_expires_at=None,
        )
        return self.store.save(updated, expected_revision=record.revision)

    def status(self, analysis_id: str):
        return self.store.load(analysis_id).to_status()

    def list_jobs(self) -> tuple[JobRecord, ...]:
        return self.store.list_records()

    def materialize_result(
        self,
        analysis_id: str,
        provenance: ResultProvenance,
    ) -> AnalysisResultV1:
        record = self.store.load(analysis_id)
        if record.status not in TERMINAL_JOB_STATUSES:
            raise OrchestrationError(
                "analysis result is unavailable before termination"
            )

        merged: dict[str, Any] = {}
        for checkpoint in record.checkpoints:
            if checkpoint.state is CheckpointState.COMPLETED:
                merged.update(checkpoint.output)

        coverage_payload = merged.get("coverage", {})
        if not isinstance(coverage_payload, Mapping):
            raise InvalidStageOutputError("coverage output must be an object")
        coverage = CoverageSummary(
            **{
                name: coverage_payload.get(name, 0)
                for name in CoverageSummary.__dataclass_fields__
            }
        )
        partial = bool(merged.get("partial", False))
        if record.status is JobStatus.COMPLETED:
            partial = False
        elif record.status is not JobStatus.COMPLETED_WITH_WARNINGS:
            partial = partial or any(
                checkpoint.state is CheckpointState.COMPLETED
                for checkpoint in record.checkpoints
            )

        metadata_payload = merged.get("metadata", {})
        if not isinstance(metadata_payload, Mapping):
            raise InvalidStageOutputError("metadata output must be an object")
        metadata = dict(metadata_payload)
        metadata["orchestration"] = {
            "job_attempt": record.job_attempt,
            "revision": record.revision,
            "completed_stages": [
                checkpoint.stage.value
                for checkpoint in record.checkpoints
                if checkpoint.state is CheckpointState.COMPLETED
            ],
        }
        return AnalysisResultV1(
            analysis_id=record.analysis_id,
            status=record.status,
            generated_at=record.updated_at,
            provenance=provenance,
            coverage=coverage,
            categories=tuple(merged.get("categories", [])),
            topics=tuple(merged.get("topics", [])),
            events=tuple(merged.get("events", [])),
            connections=tuple(merged.get("connections", [])),
            timeline=tuple(merged.get("timeline", [])),
            messages=tuple(merged.get("messages", [])),
            risk_summary=merged.get("risk_summary", {}),
            geojson=merged.get(
                "geojson",
                {"type": "FeatureCollection", "features": []},
            ),
            metadata=metadata,
            warnings=record.warnings,
            errors=record.errors,
            partial=partial,
        )

    def _latest_stage_record(
        self,
        record: JobRecord,
        stage: PipelineStage,
        attempt: int,
    ) -> JobRecord:
        latest = self.store.load(record.analysis_id)
        if latest.revision == record.revision:
            return record
        checkpoint = latest.checkpoint(stage)
        if (
            latest.lease_owner != self.worker_id
            or latest.current_stage is not stage
            or checkpoint.state is not CheckpointState.RUNNING
            or checkpoint.attempt != attempt
        ):
            raise ConcurrentUpdateError(
                f"job {record.analysis_id} changed while stage "
                f"{stage.value} was running"
            )
        return latest

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("orchestrator clock must return a timezone-aware datetime")
        return now.astimezone(UTC)

    def _acquire_lease(self, record: JobRecord) -> JobRecord:
        if record.terminal:
            return record
        now = self._now()
        if (
            record.lease_owner is not None
            and record.lease_owner != self.worker_id
            and record.lease_expires_at is not None
            and record.lease_expires_at > now
        ):
            raise JobLeaseError(
                f"job {record.analysis_id} is leased by {record.lease_owner}"
            )
        updated = replace(
            record,
            lease_owner=self.worker_id,
            lease_expires_at=now + self.lease_ttl,
            updated_at=now,
        )
        return self.store.save(updated, expected_revision=record.revision)

    def _renewed(self, record: JobRecord, **changes: Any) -> JobRecord:
        now = self._now()
        return replace(
            record,
            updated_at=now,
            lease_owner=self.worker_id,
            lease_expires_at=now + self.lease_ttl,
            **changes,
        )

    def _start_stage(
        self,
        record: JobRecord,
        checkpoint: StageCheckpoint,
    ) -> JobRecord:
        now = self._now()
        running = replace(
            checkpoint,
            state=CheckpointState.RUNNING,
            attempt=checkpoint.attempt + 1,
            started_at=now,
            completed_at=None,
            updated_at=now,
            error=None,
        )
        updated = record.replace_checkpoint(running)
        updated = self._renewed(
            updated,
            status=stage_job_status(checkpoint.stage),
            current_stage=checkpoint.stage,
        )
        return self.store.save(updated, expected_revision=record.revision)

    def _complete_stage(
        self,
        record: JobRecord,
        checkpoint: StageCheckpoint,
        result: StageResult,
    ) -> JobRecord:
        now = self._now()
        completed = replace(
            record.checkpoint(checkpoint.stage),
            state=CheckpointState.COMPLETED,
            completed_at=now,
            updated_at=now,
            processed_items=result.processed_items,
            total_items=result.total_items,
            output=result.output,
            warnings=result.warnings,
            error=None,
        )
        updated = record.replace_checkpoint(completed)
        updated = self._renewed(
            updated,
            progress_percent=stage_progress(checkpoint.stage),
            warnings=record.warnings + result.warnings,
        )
        return self.store.save(updated, expected_revision=record.revision)

    def _record_stage_error(
        self,
        record: JobRecord,
        checkpoint: StageCheckpoint,
        error: StageExecutionError,
    ) -> JobRecord:
        now = self._now()
        issue = self._issue_from_stage_error(
            checkpoint.stage,
            error,
            retryable=error.retryable,
        )
        failed = replace(
            record.checkpoint(checkpoint.stage),
            state=CheckpointState.FAILED,
            updated_at=now,
            error=issue,
        )
        updated = record.replace_checkpoint(failed)
        updated = self._renewed(updated)
        return self.store.save(updated, expected_revision=record.revision)

    @staticmethod
    def _issue_from_stage_error(
        stage: PipelineStage,
        error: StageExecutionError,
        *,
        retryable: bool,
    ) -> ContractIssue:
        return ContractIssue(
            code=error.code,
            message=str(error),
            retryable=retryable,
            stage=stage.value,
            details=error.details,
        )

    def _fail(
        self,
        record: JobRecord,
        checkpoint: StageCheckpoint,
        issue: ContractIssue,
    ) -> JobRecord:
        failed_checkpoint = replace(
            checkpoint,
            state=CheckpointState.FAILED,
            updated_at=self._now(),
            error=issue,
        )
        updated = record.replace_checkpoint(failed_checkpoint)
        updated = replace(
            updated,
            status=JobStatus.FAILED,
            current_stage=checkpoint.stage,
            errors=record.errors + (issue,),
            updated_at=self._now(),
            lease_owner=None,
            lease_expires_at=None,
        )
        return self.store.save(updated, expected_revision=record.revision)

    def _complete(self, record: JobRecord) -> JobRecord:
        status = (
            JobStatus.COMPLETED_WITH_WARNINGS
            if record.warnings
            else JobStatus.COMPLETED
        )
        updated = replace(
            record,
            status=status,
            current_stage=None,
            progress_percent=100,
            updated_at=self._now(),
            lease_owner=None,
            lease_expires_at=None,
        )
        return self.store.save(updated, expected_revision=record.revision)

    def _cancel(self, record: JobRecord) -> JobRecord:
        issue = ContractIssue(
            code="JOB_CANCELLED",
            message="job cancellation was requested",
            retryable=False,
            stage=(record.current_stage.value if record.current_stage else None),
        )
        updated = replace(
            record,
            status=JobStatus.CANCELLED,
            current_stage=None,
            warnings=record.warnings + (issue,),
            updated_at=self._now(),
            lease_owner=None,
            lease_expires_at=None,
        )
        return self.store.save(updated, expected_revision=record.revision)

    def _release_lease(self, record: JobRecord) -> JobRecord:
        if record.lease_owner is None:
            return record
        updated = replace(
            record,
            lease_owner=None,
            lease_expires_at=None,
            updated_at=self._now(),
        )
        return self.store.save(updated, expected_revision=record.revision)
