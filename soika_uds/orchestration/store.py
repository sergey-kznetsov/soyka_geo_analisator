"""Durable job stores used by the SOIKA orchestrator."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from .models import (
    PIPELINE_STAGES,
    CheckpointState,
    ConcurrentUpdateError,
    JobNotFoundError,
    JobRecord,
    OrchestrationError,
    PipelineStage,
)

_file_locking = importlib.import_module(
    "msvcrt" if os.name == "nt" else "fcntl"
)


class OrchestrationStoreError(OrchestrationError):
    """Raised when durable orchestration state cannot be read or written."""


def _migrate_legacy_checkpoints(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Insert stage 10 into persisted pre-0.15 checkpoint sequences."""

    migrated = dict(payload)
    raw_checkpoints = migrated.get("checkpoints")
    if not isinstance(raw_checkpoints, list):
        return migrated
    checkpoints = [
        dict(item) if isinstance(item, Mapping) else item
        for item in raw_checkpoints
    ]
    stages = [
        item.get("stage") if isinstance(item, Mapping) else None
        for item in checkpoints
    ]
    if PipelineStage.FILTERING.value in stages:
        return migrated
    legacy_stages = [
        stage.value for stage in PIPELINE_STAGES if stage is not PipelineStage.FILTERING
    ]
    if stages != legacy_stages:
        return migrated
    geolocation_index = legacy_stages.index(PipelineStage.GEOLOCATION.value)
    downstream = checkpoints[geolocation_index + 1 :]
    downstream_started = any(
        isinstance(item, Mapping)
        and item.get("state", CheckpointState.PENDING.value)
        != CheckpointState.PENDING.value
        for item in downstream
    )
    state = (
        CheckpointState.COMPLETED.value
        if downstream_started
        else CheckpointState.PENDING.value
    )
    filtering_checkpoint: dict[str, Any] = {
        "stage": PipelineStage.FILTERING.value,
        "state": state,
        "attempt": 0,
        "output": {},
        "warnings": [],
    }
    if downstream_started:
        filtering_checkpoint["output"] = {
            "spatial_filtering": {
                "migration_status": "legacy_bypass",
                "reason": "checkpoint_created_before_stage_10",
            }
        }
    checkpoints.insert(geolocation_index + 1, filtering_checkpoint)
    migrated["checkpoints"] = checkpoints
    return migrated


class InMemoryJobStore:
    """Deterministic optimistic-lock store for tests and embedded execution."""

    def __init__(self) -> None:
        self._records: dict[str, JobRecord] = {}
        self._lock = RLock()

    def create(self, record: JobRecord) -> JobRecord:
        with self._lock:
            if record.analysis_id in self._records:
                raise ConcurrentUpdateError(
                    f"job {record.analysis_id} already exists"
                )
            persisted = replace(record, revision=1)
            self._records[record.analysis_id] = persisted
            return persisted

    def create_idempotent(self, record: JobRecord) -> JobRecord:
        with self._lock:
            for current in self._records.values():
                if current.idempotency_key == record.idempotency_key:
                    return current
            current = self._records.get(record.analysis_id)
            if current is not None:
                return current
            persisted = replace(record, revision=1)
            self._records[record.analysis_id] = persisted
            return persisted

    def load(self, analysis_id: str) -> JobRecord:
        with self._lock:
            try:
                return self._records[analysis_id]
            except KeyError as error:
                raise JobNotFoundError(f"job {analysis_id} was not found") from error

    def save(self, record: JobRecord, *, expected_revision: int) -> JobRecord:
        with self._lock:
            current = self.load(record.analysis_id)
            if current.revision != expected_revision:
                raise ConcurrentUpdateError(
                    f"job {record.analysis_id} revision changed from "
                    f"{expected_revision} to {current.revision}"
                )
            persisted = replace(record, revision=expected_revision + 1)
            self._records[record.analysis_id] = persisted
            return persisted

    def list_records(self) -> tuple[JobRecord, ...]:
        with self._lock:
            return tuple(self._records[key] for key in sorted(self._records))

    def find_by_idempotency_key(self, key: str) -> JobRecord | None:
        with self._lock:
            for record in self._records.values():
                if record.idempotency_key == key:
                    return record
        return None


class FileJobStore:
    """Atomic JSON store with process locks and optimistic revisions.

    The store is transport-neutral and has no dependency on Geo Analyzer. A
    later PostgreSQL/PostGIS implementation can provide the same methods.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.lock_root = self.root / ".locks"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock_root.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    @staticmethod
    def _file_name(analysis_id: str) -> str:
        digest = hashlib.sha256(analysis_id.encode("utf-8")).hexdigest()
        return f"{digest}.json"

    def _path(self, analysis_id: str) -> Path:
        return self.root / self._file_name(analysis_id)

    def _lock_path(self, analysis_id: str) -> Path:
        return self.lock_root / self._file_name(analysis_id).replace(
            ".json", ".lock"
        )

    @contextmanager
    def _job_lock(self, analysis_id: str) -> Iterator[None]:
        path = self._lock_path(analysis_id)
        with path.open("a+b") as stream:
            if os.name == "nt":
                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                _file_locking.locking(
                    stream.fileno(), _file_locking.LK_LOCK, 1
                )
            else:
                _file_locking.flock(
                    stream.fileno(), _file_locking.LOCK_EX
                )
            try:
                yield
            finally:
                if os.name == "nt":
                    stream.seek(0)
                    _file_locking.locking(
                        stream.fileno(), _file_locking.LK_UNLCK, 1
                    )
                else:
                    _file_locking.flock(
                        stream.fileno(), _file_locking.LOCK_UN
                    )

    @staticmethod
    def _decode(path: Path) -> JobRecord:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise OrchestrationStoreError(
                f"cannot read persisted job {path.name}: {error}"
            ) from error
        if not isinstance(payload, dict):
            raise OrchestrationStoreError(
                f"persisted job {path.name} must be a JSON object"
            )
        return JobRecord.from_dict(_migrate_legacy_checkpoints(payload))

    def _load_unlocked(self, analysis_id: str) -> JobRecord:
        path = self._path(analysis_id)
        if not path.exists():
            raise JobNotFoundError(f"job {analysis_id} was not found")
        record = self._decode(path)
        if record.analysis_id != analysis_id:
            raise OrchestrationStoreError(
                f"persisted job hash collision for {analysis_id}"
            )
        return record

    def _sync_directory(self) -> None:
        if not hasattr(os, "O_DIRECTORY"):
            return
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _write(self, record: JobRecord) -> None:
        path = self._path(record.analysis_id)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        data = json.dumps(
            record.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(data)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            self._sync_directory()
        finally:
            temporary.unlink(missing_ok=True)

    def create(self, record: JobRecord) -> JobRecord:
        with self._lock, self._job_lock(record.analysis_id):
            path = self._path(record.analysis_id)
            if path.exists():
                raise ConcurrentUpdateError(
                    f"job {record.analysis_id} already exists"
                )
            persisted = replace(record, revision=1)
            self._write(persisted)
            return persisted

    def create_idempotent(self, record: JobRecord) -> JobRecord:
        with self._lock, self._job_lock("__idempotency__"):
            for current in self._iter_records():
                if current.idempotency_key == record.idempotency_key:
                    return current
            path = self._path(record.analysis_id)
            if path.exists():
                return self._load_unlocked(record.analysis_id)
            with self._job_lock(record.analysis_id):
                if path.exists():
                    return self._load_unlocked(record.analysis_id)
                persisted = replace(record, revision=1)
                self._write(persisted)
                return persisted

    def load(self, analysis_id: str) -> JobRecord:
        with self._lock, self._job_lock(analysis_id):
            return self._load_unlocked(analysis_id)

    def save(self, record: JobRecord, *, expected_revision: int) -> JobRecord:
        with self._lock, self._job_lock(record.analysis_id):
            current = self._load_unlocked(record.analysis_id)
            if current.revision != expected_revision:
                raise ConcurrentUpdateError(
                    f"job {record.analysis_id} revision changed from "
                    f"{expected_revision} to {current.revision}"
                )
            persisted = replace(record, revision=expected_revision + 1)
            self._write(persisted)
            return persisted

    def _iter_records(self) -> Iterable[JobRecord]:
        for path in sorted(self.root.glob("*.json")):
            yield self._decode(path)

    def list_records(self) -> tuple[JobRecord, ...]:
        with self._lock:
            return tuple(
                sorted(self._iter_records(), key=lambda item: item.analysis_id)
            )

    def find_by_idempotency_key(self, key: str) -> JobRecord | None:
        with self._lock:
            for record in self._iter_records():
                if record.idempotency_key == key:
                    return record
        return None
