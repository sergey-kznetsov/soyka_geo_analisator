"""Application-scoped durable JSON cache backed by PostgreSQL."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contracts import (
    application_id,
    cache_key,
    cache_namespace,
    canonical_json,
    digest_json,
    sha256_digest,
)
from .postgres import PostgresDatabase


class PostgresJsonCache:
    """Durable L2 cache with deterministic keys, TTL, and application isolation."""

    def __init__(
        self,
        database: PostgresDatabase,
        *,
        application: str,
        namespace: str,
    ) -> None:
        if not isinstance(database, PostgresDatabase):
            raise TypeError("database must be PostgresDatabase")
        self.database = database
        self.application = application_id(application)
        self.namespace = cache_namespace(namespace)

    @staticmethod
    def key(operation: str, parameters: Mapping[str, Any]) -> str:
        return cache_key(operation, parameters)

    def get(self, key: str) -> Any | None:
        key = sha256_digest(key, "cache key")
        with self.database.connection() as connection:
            connection.execute(
                "DELETE FROM ga_cache.entries "
                "WHERE application_id = %s AND namespace = %s AND key_digest = %s "
                "AND expires_at <= clock_timestamp()",
                (self.application, self.namespace, key),
            )
            row = connection.execute(
                "SELECT value_json FROM ga_cache.entries "
                "WHERE application_id = %s AND namespace = %s AND key_digest = %s "
                "AND expires_at > clock_timestamp()",
                (self.application, self.namespace, key),
            ).fetchone()
        return None if row is None else row[0]

    def set(self, key: str, payload: Any, *, ttl_seconds: int) -> None:
        key = sha256_digest(key, "cache key")
        if type(ttl_seconds) is not int or ttl_seconds < 1:
            raise ValueError("cache ttl must be a positive integer")
        serialized = canonical_json(payload)
        value_digest = digest_json(payload)
        try:
            from psycopg.types.json import Jsonb
        except ImportError as error:
            raise RuntimeError(
                "PostgreSQL storage requires requirements-storage.txt"
            ) from error
        json_value = Jsonb(payload, dumps=lambda _value: serialized)
        with self.database.connection() as connection:
            connection.execute(
                "INSERT INTO ga_cache.entries(" 
                "application_id, namespace, key_digest, value_json, value_digest, "
                "created_at, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, clock_timestamp(), "
                "clock_timestamp() + (%s * interval '1 second')) "
                "ON CONFLICT (application_id, namespace, key_digest) DO UPDATE SET "
                "value_json = EXCLUDED.value_json, "
                "value_digest = EXCLUDED.value_digest, "
                "created_at = EXCLUDED.created_at, "
                "expires_at = EXCLUDED.expires_at",
                (
                    self.application,
                    self.namespace,
                    key,
                    json_value,
                    value_digest,
                    ttl_seconds,
                ),
            )

    def delete_expired(self, *, limit: int = 10_000) -> int:
        if type(limit) is not int or limit < 1:
            raise ValueError("cleanup limit must be a positive integer")
        with self.database.connection() as connection:
            rows = connection.execute(
                "WITH doomed AS ("
                "SELECT ctid FROM ga_cache.entries "
                "WHERE application_id = %s AND namespace = %s "
                "AND expires_at <= clock_timestamp() "
                "ORDER BY expires_at LIMIT %s"
                ") DELETE FROM ga_cache.entries AS cache "
                "USING doomed WHERE cache.ctid = doomed.ctid RETURNING 1",
                (self.application, self.namespace, limit),
            ).fetchall()
        return len(rows)

    def clear_namespace(self) -> int:
        with self.database.connection() as connection:
            rows = connection.execute(
                "DELETE FROM ga_cache.entries "
                "WHERE application_id = %s AND namespace = %s RETURNING 1",
                (self.application, self.namespace),
            ).fetchall()
        return len(rows)


__all__ = ["PostgresJsonCache"]
