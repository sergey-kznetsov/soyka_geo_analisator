"""PostgreSQL-backed durable queue for isolated SOIKA workers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from geoanalyzer_storage import PostgresDatabase, application_id

from .models import (
    ComputeClass,
    QueueConflictError,
    QueueItem,
    QueueLeaseError,
    QueueStats,
    new_trace_id,
    validate_trace_id,
    validate_worker_id,
)

_TERMINAL_STATUSES = (
    "completed",
    "completed_with_warnings",
    "failed",
    "cancelled",
)
_REAPABLE_STATUSES = (
    "completed",
    "completed_with_warnings",
    "cancelled",
)


class JobQueue(Protocol):
    def enqueue(
        self,
        analysis_id: str,
        *,
        compute_class: ComputeClass,
        priority: int = 0,
        max_attempts: int = 3,
        trace_id: str | None = None,
    ) -> QueueItem:
        ...

    def claim(
        self,
        *,
        worker_id: str,
        compute_class: ComputeClass,
        lease_seconds: float,
    ) -> QueueItem | None:
        ...

    def renew(
        self,
        analysis_id: str,
        *,
        worker_id: str,
        lease_seconds: float,
    ) -> QueueItem:
        ...

    def release(
        self,
        analysis_id: str,
        *,
        worker_id: str,
        retryable: bool,
        retry_delay_seconds: float,
        error: Mapping[str, object],
    ) -> QueueItem:
        ...

    def ack(self, analysis_id: str, *, worker_id: str) -> None:
        ...

    def request_cancel(self, analysis_id: str) -> QueueItem:
        ...

    def is_cancel_requested(self, analysis_id: str) -> bool:
        ...

    def retry(self, analysis_id: str) -> QueueItem:
        ...

    def stats(self, compute_class: ComputeClass) -> QueueStats:
        ...


class PostgresJobQueue:
    """Lease-based queue using PostgreSQL row locks only during claim."""

    _RETURNING = (
        "analysis_id, compute_class, priority, attempt, max_attempts, "
        "available_at, lease_owner, lease_expires_at, cancel_requested, "
        "trace_id, last_error, enqueued_at, updated_at"
    )
    _CLAIM_RETURNING = (
        "q.analysis_id, q.compute_class, q.priority, q.attempt, q.max_attempts, "
        "q.available_at, q.lease_owner, q.lease_expires_at, q.cancel_requested, "
        "q.trace_id, q.last_error, q.enqueued_at, q.updated_at"
    )

    def __init__(
        self,
        database: PostgresDatabase,
        *,
        application: str = "soika",
    ) -> None:
        if not isinstance(database, PostgresDatabase):
            raise TypeError("database must be PostgresDatabase")
        self.database = database
        self.application = application_id(application)

    @staticmethod
    def _jsonb(value: Mapping[str, object]) -> Any:
        try:
            from psycopg.types.json import Jsonb
        except ImportError as error:
            raise RuntimeError(
                "PostgreSQL worker queue requires requirements-storage.txt"
            ) from error
        return Jsonb(dict(value))

    @classmethod
    def _item(cls, row: object) -> QueueItem:
        if not isinstance(row, tuple | list) or len(row) != 13:
            raise ValueError("worker queue row has unexpected shape")
        last_error = row[10]
        if last_error is not None and not isinstance(last_error, dict):
            raise ValueError("worker queue last_error must be an object")
        return QueueItem(
            analysis_id=str(row[0]),
            compute_class=ComputeClass(str(row[1])),
            priority=int(row[2]),
            attempt=int(row[3]),
            max_attempts=int(row[4]),
            available_at=row[5],
            lease_owner=None if row[6] is None else str(row[6]),
            lease_expires_at=row[7],
            cancel_requested=bool(row[8]),
            trace_id=str(row[9]),
            last_error=None if last_error is None else dict(last_error),
            enqueued_at=row[11],
            updated_at=row[12],
        )

    @staticmethod
    def _positive_seconds(value: float, field_name: str) -> float:
        if not isinstance(value, int | float) or value <= 0:
            raise ValueError(f"{field_name} must be positive")
        return float(value)

    def enqueue(
        self,
        analysis_id: str,
        *,
        compute_class: ComputeClass,
        priority: int = 0,
        max_attempts: int = 3,
        trace_id: str | None = None,
    ) -> QueueItem:
        if not isinstance(analysis_id, str) or not analysis_id.strip():
            raise ValueError("analysis_id must be non-empty")
        compute_class = ComputeClass(compute_class)
        if type(priority) is not int or not -100 <= priority <= 100:
            raise ValueError("priority must be an integer in [-100, 100]")
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        resolved_trace_id = validate_trace_id(trace_id or new_trace_id())

        with self.database.connection() as connection:
            row = connection.execute(
                "INSERT INTO ga_core.job_queue("
                "application_id, analysis_id, compute_class, priority, "
                "max_attempts, trace_id) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (application_id, analysis_id) DO NOTHING "
                f"RETURNING {self._RETURNING}",
                (
                    self.application,
                    analysis_id,
                    compute_class.value,
                    priority,
                    max_attempts,
                    resolved_trace_id,
                ),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    f"SELECT {self._RETURNING} FROM ga_core.job_queue "
                    "WHERE application_id = %s AND analysis_id = %s",
                    (self.application, analysis_id),
                ).fetchone()
                if row is None:
                    raise QueueConflictError(
                        f"queue item {analysis_id!r} conflicted but was not found"
                    )
                current = self._item(row)
                if current.compute_class is not compute_class:
                    raise QueueConflictError(
                        "existing queue item has a different compute class"
                    )
                return current
        return self._item(row)

    def claim(
        self,
        *,
        worker_id: str,
        compute_class: ComputeClass,
        lease_seconds: float,
    ) -> QueueItem | None:
        worker_id = validate_worker_id(worker_id)
        compute_class = ComputeClass(compute_class)
        lease_seconds = self._positive_seconds(lease_seconds, "lease_seconds")
        with self.database.connection() as connection:
            row = connection.execute(
                "WITH candidate AS ("
                "SELECT q.application_id, q.analysis_id "
                "FROM ga_core.job_queue AS q "
                "JOIN ga_core.jobs AS j "
                "ON j.application_id = q.application_id "
                "AND j.analysis_id = q.analysis_id "
                "WHERE q.application_id = %s "
                "AND q.compute_class = %s "
                "AND q.cancel_requested = FALSE "
                "AND q.attempt < q.max_attempts "
                "AND q.available_at <= clock_timestamp() "
                "AND (q.lease_owner IS NULL "
                "OR q.lease_expires_at <= clock_timestamp()) "
                "AND j.status <> ALL(%s) "
                "ORDER BY q.priority DESC, q.available_at, "
                "q.enqueued_at, q.analysis_id "
                "FOR UPDATE OF q SKIP LOCKED LIMIT 1"
                ") "
                "UPDATE ga_core.job_queue AS q SET "
                "attempt = q.attempt + 1, lease_owner = %s, "
                "lease_expires_at = clock_timestamp() + "
                "(%s * interval '1 second'), updated_at = clock_timestamp() "
                "FROM candidate AS c "
                "WHERE q.application_id = c.application_id "
                "AND q.analysis_id = c.analysis_id "
                f"RETURNING {self._CLAIM_RETURNING}",
                (
                    self.application,
                    compute_class.value,
                    list(_TERMINAL_STATUSES),
                    worker_id,
                    lease_seconds,
                ),
            ).fetchone()
        return None if row is None else self._item(row)

    def renew(
        self,
        analysis_id: str,
        *,
        worker_id: str,
        lease_seconds: float,
    ) -> QueueItem:
        worker_id = validate_worker_id(worker_id)
        lease_seconds = self._positive_seconds(lease_seconds, "lease_seconds")
        with self.database.connection() as connection:
            row = connection.execute(
                "UPDATE ga_core.job_queue SET "
                "lease_expires_at = clock_timestamp() + "
                "(%s * interval '1 second'), updated_at = clock_timestamp() "
                "WHERE application_id = %s AND analysis_id = %s "
                "AND lease_owner = %s "
                "AND lease_expires_at > clock_timestamp() "
                f"RETURNING {self._RETURNING}",
                (lease_seconds, self.application, analysis_id, worker_id),
            ).fetchone()
        if row is None:
            raise QueueLeaseError(f"queue lease for {analysis_id!r} was lost")
        return self._item(row)

    def release(
        self,
        analysis_id: str,
        *,
        worker_id: str,
        retryable: bool,
        retry_delay_seconds: float,
        error: Mapping[str, object],
    ) -> QueueItem:
        worker_id = validate_worker_id(worker_id)
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds must not be negative")
        if not isinstance(error, Mapping):
            raise TypeError("error must be a mapping")
        with self.database.connection() as connection:
            row = connection.execute(
                "UPDATE ga_core.job_queue SET "
                "attempt = CASE WHEN %s THEN attempt ELSE max_attempts END, "
                "available_at = CASE "
                "WHEN %s AND attempt < max_attempts "
                "THEN clock_timestamp() + (%s * interval '1 second') "
                "ELSE available_at END, "
                "lease_owner = NULL, lease_expires_at = NULL, "
                "last_error = %s, updated_at = clock_timestamp() "
                "WHERE application_id = %s AND analysis_id = %s "
                "AND lease_owner = %s "
                f"RETURNING {self._RETURNING}",
                (
                    retryable,
                    retryable,
                    float(retry_delay_seconds),
                    self._jsonb(error),
                    self.application,
                    analysis_id,
                    worker_id,
                ),
            ).fetchone()
        if row is None:
            raise QueueLeaseError(f"queue lease for {analysis_id!r} was lost")
        return self._item(row)

    def ack(self, analysis_id: str, *, worker_id: str) -> None:
        worker_id = validate_worker_id(worker_id)
        with self.database.connection() as connection:
            row = connection.execute(
                "DELETE FROM ga_core.job_queue "
                "WHERE application_id = %s AND analysis_id = %s "
                "AND lease_owner = %s RETURNING analysis_id",
                (self.application, analysis_id, worker_id),
            ).fetchone()
        if row is None:
            raise QueueLeaseError(f"queue lease for {analysis_id!r} was lost")

    def request_cancel(self, analysis_id: str) -> QueueItem:
        with self.database.connection() as connection:
            row = connection.execute(
                "UPDATE ga_core.job_queue SET cancel_requested = TRUE, "
                "updated_at = clock_timestamp() "
                "WHERE application_id = %s AND analysis_id = %s "
                f"RETURNING {self._RETURNING}",
                (self.application, analysis_id),
            ).fetchone()
        if row is None:
            raise QueueConflictError(f"queue item {analysis_id!r} was not found")
        return self._item(row)

    def is_cancel_requested(self, analysis_id: str) -> bool:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT cancel_requested FROM ga_core.job_queue "
                "WHERE application_id = %s AND analysis_id = %s",
                (self.application, analysis_id),
            ).fetchone()
        return bool(row and row[0])

    def retry(self, analysis_id: str) -> QueueItem:
        with self.database.connection() as connection:
            row = connection.execute(
                "UPDATE ga_core.job_queue SET attempt = 0, "
                "available_at = clock_timestamp(), lease_owner = NULL, "
                "lease_expires_at = NULL, cancel_requested = FALSE, "
                "last_error = NULL, updated_at = clock_timestamp() "
                "WHERE application_id = %s AND analysis_id = %s "
                "AND (lease_owner IS NULL OR lease_expires_at <= clock_timestamp()) "
                f"RETURNING {self._RETURNING}",
                (self.application, analysis_id),
            ).fetchone()
        if row is None:
            raise QueueConflictError(
                f"queue item {analysis_id!r} is missing or has a live lease"
            )
        return self._item(row)

    def stats(self, compute_class: ComputeClass) -> QueueStats:
        compute_class = ComputeClass(compute_class)
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT "
                "count(*) FILTER (WHERE cancel_requested = FALSE "
                "AND attempt < max_attempts AND available_at <= clock_timestamp() "
                "AND (lease_owner IS NULL "
                "OR lease_expires_at <= clock_timestamp())) AS ready, "
                "count(*) FILTER (WHERE lease_owner IS NOT NULL "
                "AND lease_expires_at > clock_timestamp()) AS leased, "
                "count(*) FILTER (WHERE cancel_requested = FALSE "
                "AND attempt < max_attempts AND available_at > clock_timestamp()) "
                "AS delayed, "
                "count(*) FILTER (WHERE attempt >= max_attempts) AS exhausted, "
                "count(*) FILTER (WHERE cancel_requested = TRUE) AS cancelled, "
                "COALESCE(EXTRACT(EPOCH FROM clock_timestamp() - MIN(available_at) "
                "FILTER (WHERE cancel_requested = FALSE AND attempt < max_attempts "
                "AND available_at <= clock_timestamp() AND lease_owner IS NULL)), 0) "
                "AS oldest_ready_age_seconds "
                "FROM ga_core.job_queue "
                "WHERE application_id = %s AND compute_class = %s",
                (self.application, compute_class.value),
            ).fetchone()
        if row is None:
            return QueueStats()
        return QueueStats(
            ready=int(row[0]),
            leased=int(row[1]),
            delayed=int(row[2]),
            exhausted=int(row[3]),
            cancelled=int(row[4]),
            oldest_ready_age_seconds=max(0.0, float(row[5])),
        )

    def reap_terminal(self) -> int:
        with self.database.connection() as connection:
            cursor = connection.execute(
                "DELETE FROM ga_core.job_queue AS q USING ga_core.jobs AS j "
                "WHERE q.application_id = %s "
                "AND j.application_id = q.application_id "
                "AND j.analysis_id = q.analysis_id "
                "AND j.status = ANY(%s)",
                (self.application, list(_REAPABLE_STATUSES)),
            )
            return int(cursor.rowcount)

    def healthcheck(self) -> bool:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT to_regclass('ga_core.job_queue') IS NOT NULL"
            ).fetchone()
        return bool(row and row[0])


__all__ = ["JobQueue", "PostgresJobQueue"]
