"""Persistent response-cache contracts and the SQLite compatibility backend."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Mapping
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from .models import canonical_json, digest_json


class ResponseCache(Protocol):
    """Cache surface accepted by geolocation providers."""

    @staticmethod
    def key(operation: str, parameters: Mapping[str, Any]) -> str: ...

    def get(self, cache_key: str) -> Any | None: ...

    def set(self, cache_key: str, payload: Any, *, ttl_seconds: int) -> None: ...


class SQLiteResponseCache:
    """Embedded compatibility cache; PostgreSQL is the shared production backend."""

    def __init__(self, path: Path, *, namespace: str) -> None:
        self._path = Path(path)
        self._namespace = namespace.strip()
        if not self._namespace:
            raise ValueError("cache namespace must be non-empty")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=10.0)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS response_cache (
                    namespace TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (namespace, cache_key)
                )
                """
            )

    @staticmethod
    def key(operation: str, parameters: Mapping[str, Any]) -> str:
        return digest_json({"operation": operation, "parameters": parameters})

    def get(self, cache_key: str) -> Any | None:
        now = time.time()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload, expires_at FROM response_cache "
                "WHERE namespace = ? AND cache_key = ?",
                (self._namespace, cache_key),
            ).fetchone()
            if row is None:
                return None
            payload, expires_at = row
            if float(expires_at) <= now:
                connection.execute(
                    "DELETE FROM response_cache WHERE namespace = ? AND cache_key = ?",
                    (self._namespace, cache_key),
                )
                return None
            return json.loads(payload)

    def set(self, cache_key: str, payload: Any, *, ttl_seconds: int) -> None:
        if not isinstance(ttl_seconds, int) or ttl_seconds < 1:
            raise ValueError("cache ttl must be a positive integer")
        now = time.time()
        serialized = canonical_json(payload)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO response_cache(
                    namespace, cache_key, payload, expires_at, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(namespace, cache_key) DO UPDATE SET
                    payload = excluded.payload,
                    expires_at = excluded.expires_at,
                    created_at = excluded.created_at
                """,
                (
                    self._namespace,
                    cache_key,
                    serialized,
                    now + ttl_seconds,
                    now,
                ),
            )


__all__ = ["ResponseCache", "SQLiteResponseCache"]
