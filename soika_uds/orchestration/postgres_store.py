"""PostgreSQL implementation of the existing orchestration store protocol."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from geoanalyzer_storage import PostgresDatabase, application_id

from .models import ConcurrentUpdateError, JobNotFoundError, JobRecord
from .store import _migrate_legacy_checkpoints


class PostgresJobStore:
    """Optimistic-lock job store using the shared Geo Analyzer database."""

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
    def _jsonb(value: Any) -> Any:
        try:
            from psycopg.types.json import Jsonb
        except ImportError as error:
            raise RuntimeError(
                "PostgreSQL storage requires requirements-storage.txt"
            ) from error
        return Jsonb(value)

    @staticmethod
    def _record(payload: object) -> JobRecord:
        if not isinstance(payload, dict):
            raise ValueError("persisted PostgreSQL job payload must be an object")
        return JobRecord.from_dict(_migrate_legacy_checkpoints(payload))

    def _insert_job(self, connection: Any, record: JobRecord) -> object | None:
        payload = record.to_dict()
        row = connection.execute(
            "INSERT INTO ga_core.jobs("
            "application_id, analysis_id, idempotency_key, revision, status, "
            "current_stage, progress_percent, payload, created_at, updated_at, "
            "lease_owner, lease_expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT DO NOTHING RETURNING payload",
            (
                self.application,
                record.analysis_id,
                record.idempotency_key,
                record.revision,
                record.status.value,
                record.current_stage.value if record.current_stage else None,
                record.progress_percent,
                self._jsonb(payload),
                record.created_at,
                record.updated_at,
                record.lease_owner,
                record.lease_expires_at,
            ),
        ).fetchone()
        if row is None:
            return None
        self._sync_checkpoints(connection, record)
        return row[0]

    def _sync_checkpoints(self, connection: Any, record: JobRecord) -> None:
        for checkpoint in record.checkpoints:
            payload = checkpoint.to_dict()
            connection.execute(
                "INSERT INTO ga_core.stage_checkpoints("
                "application_id, analysis_id, stage, state, attempt, "
                "processed_items, total_items, output, warnings, error, "
                "started_at, completed_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (application_id, analysis_id, stage) DO UPDATE SET "
                "state = EXCLUDED.state, attempt = EXCLUDED.attempt, "
                "processed_items = EXCLUDED.processed_items, "
                "total_items = EXCLUDED.total_items, output = EXCLUDED.output, "
                "warnings = EXCLUDED.warnings, error = EXCLUDED.error, "
                "started_at = EXCLUDED.started_at, completed_at = EXCLUDED.completed_at, "
                "updated_at = EXCLUDED.updated_at",
                (
                    self.application,
                    record.analysis_id,
                    checkpoint.stage.value,
                    checkpoint.state.value,
                    checkpoint.attempt,
                    checkpoint.processed_items,
                    checkpoint.total_items,
                    self._jsonb(payload.get("output", {})),
                    self._jsonb(payload.get("warnings", [])),
                    (
                        self._jsonb(payload["error"])
                        if payload.get("error") is not None
                        else None
                    ),
                    checkpoint.started_at,
                    checkpoint.completed_at,
                    checkpoint.updated_at,
                ),
            )

    def create(self, record: JobRecord) -> JobRecord:
        persisted = replace(record, revision=1)
        with self.database.connection() as connection:
            payload = self._insert_job(connection, persisted)
            if payload is None:
                raise ConcurrentUpdateError(
                    f"job {record.analysis_id} already exists"
                )
        return self._record(payload)

    def create_idempotent(self, record: JobRecord) -> JobRecord:
        persisted = replace(record, revision=1)
        with self.database.connection() as connection:
            payload = self._insert_job(connection, persisted)
            if payload is None:
                row = connection.execute(
                    "SELECT payload FROM ga_core.jobs "
                    "WHERE application_id = %s "
                    "AND (idempotency_key = %s OR analysis_id = %s) "
                    "ORDER BY CASE WHEN idempotency_key = %s THEN 0 ELSE 1 END "
                    "LIMIT 1",
                    (
                        self.application,
                        record.idempotency_key,
                        record.analysis_id,
                        record.idempotency_key,
                    ),
                ).fetchone()
                if row is None:
                    raise ConcurrentUpdateError(
                        "idempotent insert conflicted but no persisted job was found"
                    )
                payload = row[0]
        return self._record(payload)

    def load(self, analysis_id: str) -> JobRecord:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT payload FROM ga_core.jobs "
                "WHERE application_id = %s AND analysis_id = %s",
                (self.application, analysis_id),
            ).fetchone()
        if row is None:
            raise JobNotFoundError(f"job {analysis_id} was not found")
        return self._record(row[0])

    def save(self, record: JobRecord, *, expected_revision: int) -> JobRecord:
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        persisted = replace(record, revision=expected_revision + 1)
        payload = persisted.to_dict()
        with self.database.connection() as connection:
            row = connection.execute(
                "UPDATE ga_core.jobs SET "
                "idempotency_key = %s, revision = %s, status = %s, current_stage = %s, "
                "progress_percent = %s, payload = %s, created_at = %s, updated_at = %s, "
                "lease_owner = %s, lease_expires_at = %s "
                "WHERE application_id = %s AND analysis_id = %s AND revision = %s "
                "RETURNING payload",
                (
                    persisted.idempotency_key,
                    persisted.revision,
                    persisted.status.value,
                    persisted.current_stage.value if persisted.current_stage else None,
                    persisted.progress_percent,
                    self._jsonb(payload),
                    persisted.created_at,
                    persisted.updated_at,
                    persisted.lease_owner,
                    persisted.lease_expires_at,
                    self.application,
                    persisted.analysis_id,
                    expected_revision,
                ),
            ).fetchone()
            if row is None:
                current = connection.execute(
                    "SELECT revision FROM ga_core.jobs "
                    "WHERE application_id = %s AND analysis_id = %s",
                    (self.application, persisted.analysis_id),
                ).fetchone()
                if current is None:
                    raise JobNotFoundError(
                        f"job {persisted.analysis_id} was not found"
                    )
                raise ConcurrentUpdateError(
                    f"job {persisted.analysis_id} revision changed from "
                    f"{expected_revision} to {current[0]}"
                )
            self._sync_checkpoints(connection, persisted)
        return self._record(row[0])

    def list_records(self) -> tuple[JobRecord, ...]:
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM ga_core.jobs WHERE application_id = %s "
                "ORDER BY analysis_id",
                (self.application,),
            ).fetchall()
        return tuple(self._record(row[0]) for row in rows)

    def find_by_idempotency_key(self, key: str) -> JobRecord | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT payload FROM ga_core.jobs "
                "WHERE application_id = %s AND idempotency_key = %s",
                (self.application, key),
            ).fetchone()
        return None if row is None else self._record(row[0])


__all__ = ["PostgresJobStore"]
