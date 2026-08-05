"Durable parser checkpoints and append-only audit events."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .models import AuditEvent, ParserPlatformError, SourcePolicyError

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_SAFE_SOURCE_RE = re.compile(r"^[a-z][a-z0-9._-]{2,63}$")


class ParserStoreError(ParserPlatformError):
    """Raised when durable parser state cannot be read or written."""


class ParserCheckpointStore(Protocol):
    def load(self, analysis_id: str, source_id: str) -> dict[str, object] | None:
        ...

    def save(
        self,
        analysis_id: str,
        source_id: str,
        checkpoint: Mapping[str, object] | None,
        *,
        completed: bool,
    ) -> None:
        ...

    def clear(self, analysis_id: str, source_id: str) -> None:
        ...


class AuditSink(Protocol):
    def write(self, event: AuditEvent) -> None:
        ...


def _safe_component(value: str, pattern: re.Pattern[str], field_name: str) -> str:
    if not isinstance(value, str):
        raise ParserStoreError(f"{field_name} must be a string")
    cleaned = value.strip()
    if pattern.fullmatch(cleaned) is None:
        raise ParserStoreError(f"{field_name} has unsafe format")
    return cleaned


@contextmanager
def _file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            import fcntl
        except ImportError as error:
            raise ParserStoreError(
                "file checkpoint store requires POSIX fcntl"
            ) from error
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


class InMemoryParserCheckpointStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], dict[str, object]] = {}

    def load(self, analysis_id: str, source_id: str) -> dict[str, object] | None:
        item = self._items.get((analysis_id, source_id))
        return json.loads(json.dumps(item)) if item is not None else None

    def save(
        self,
        analysis_id: str,
        source_id: str,
        checkpoint: Mapping[str, object] | None,
        *,
        completed: bool,
    ) -> None:
        payload = {
            "analysis_id": analysis_id,
            "source_id": source_id,
            "checkpoint": dict(checkpoint) if checkpoint is not None else None,
            "completed": completed,
            "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        try:
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ParserStoreError("checkpoint must be JSON serializable") from error
        self._items[(analysis_id, source_id)] = json.loads(encoded)

    def clear(self, analysis_id: str, source_id: str) -> None:
        self._items.pop((analysis_id, source_id), None)


class FileParserCheckpointStore:
    """Atomic one-file-per-analysis/source checkpoint store."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _paths(self, analysis_id: str, source_id: str) -> tuple[Path, Path]:
        analysis = _safe_component(analysis_id, _SAFE_ID_RE, "analysis_id")
        source = _safe_component(source_id, _SAFE_SOURCE_RE, "source_id")
        directory = self.root / analysis
        return directory / f"{source}.json", directory / f"{source}.lock"

    def load(self, analysis_id: str, source_id: str) -> dict[str, object] | None:
        path, lock_path = self._paths(analysis_id, source_id)
        with _file_lock(lock_path):
            if not path.exists():
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ParserStoreError(
                    f"cannot read parser checkpoint {path}"
                ) from error
            if not isinstance(payload, dict):
                raise ParserStoreError("checkpoint document must be an object")
            if payload.get("analysis_id") != analysis_id:
                raise ParserStoreError("checkpoint analysis_id mismatch")
            if payload.get("source_id") != source_id:
                raise ParserStoreError("checkpoint source_id mismatch")
            checkpoint = payload.get("checkpoint")
            if checkpoint is not None and not isinstance(checkpoint, dict):
                raise ParserStoreError("checkpoint value must be an object")
            completed = payload.get("completed")
            if not isinstance(completed, bool):
                raise ParserStoreError("checkpoint completed must be boolean")
            return payload

    def save(
        self,
        analysis_id: str,
        source_id: str,
        checkpoint: Mapping[str, object] | None,
        *,
        completed: bool,
    ) -> None:
        path, lock_path = self._paths(analysis_id, source_id)
        payload = {
            "analysis_id": analysis_id,
            "source_id": source_id,
            "checkpoint": dict(checkpoint) if checkpoint is not None else None,
            "completed": completed,
            "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
        except (TypeError, ValueError) as error:
            raise ParserStoreError("checkpoint must be JSON serializable") from error

        with _file_lock(lock_path):
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=path.parent,
                    prefix=f".{path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as handle:
                    temporary_path = Path(handle.name)
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary_path, 0o600)
                os.replace(temporary_path, path)
            except OSError as error:
                raise ParserStoreError(
                    f"cannot write parser checkpoint {path}"
                ) from error
            finally:
                if temporary_path is not None and temporary_path.exists():
                    temporary_path.unlink(missing_ok=True)

    def clear(self, analysis_id: str, source_id: str) -> None:
        path, lock_path = self._paths(analysis_id, source_id)
        with _file_lock(lock_path):
            path.unlink(missing_ok=True)


class InMemoryAuditSink:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def write(self, event: AuditEvent) -> None:
        self.events.append(event)


class JsonlAuditSink:
    """Append-only JSONL audit trail with process-safe writes."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(f"{self.path.suffix}.lock")

    def write(self, event: AuditEvent) -> None:
        if not isinstance(event, AuditEvent):
            raise SourcePolicyError("audit sink requires AuditEvent")
        line = json.dumps(
            event.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        )
        with _file_lock(self.lock_path):
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(
                    self.path,
                    os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                    0o600,
                )
                try:
                    os.write(descriptor, f"{line}\n".encode())
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            except OSError as error:
                raise ParserStoreError("cannot append parser audit event") from error
