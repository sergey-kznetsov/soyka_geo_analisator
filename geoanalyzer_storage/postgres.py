"""Lazy PostgreSQL connection pool shared by Geo Analyzer server programs."""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

_APPLICATION_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,63}$")


@dataclass(frozen=True, slots=True)
class PostgresSettings:
    dsn: str = field(repr=False)
    application_name: str = "geoanalyzer-storage"
    min_pool_size: int = 1
    max_pool_size: int = 8
    statement_timeout_ms: int = 30_000
    lock_timeout_ms: int = 5_000

    def __post_init__(self) -> None:
        if not isinstance(self.dsn, str) or not self.dsn.strip():
            raise ValueError("PostgreSQL DSN must be non-empty")
        if (
            not isinstance(self.application_name, str)
            or _APPLICATION_NAME.fullmatch(self.application_name) is None
        ):
            raise ValueError("application_name contains unsupported characters")
        if type(self.min_pool_size) is not int or self.min_pool_size < 0:
            raise ValueError("min_pool_size must be a non-negative integer")
        if type(self.max_pool_size) is not int or self.max_pool_size < 1:
            raise ValueError("max_pool_size must be a positive integer")
        if self.min_pool_size > self.max_pool_size:
            raise ValueError("min_pool_size cannot exceed max_pool_size")
        for name in ("statement_timeout_ms", "lock_timeout_ms"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")

    def __repr__(self) -> str:
        return (
            "PostgresSettings(dsn=<redacted>, "
            f"application_name={self.application_name!r}, "
            f"min_pool_size={self.min_pool_size}, "
            f"max_pool_size={self.max_pool_size}, "
            f"statement_timeout_ms={self.statement_timeout_ms}, "
            f"lock_timeout_ms={self.lock_timeout_ms})"
        )


class PostgresDatabase:
    """A small synchronous pool wrapper with no domain dependencies."""

    def __init__(self, settings: PostgresSettings) -> None:
        if not isinstance(settings, PostgresSettings):
            raise TypeError("settings must be PostgresSettings")
        self.settings = settings
        self._pool: Any | None = None

    def _configure(self, connection: Any) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SET statement_timeout = {self.settings.statement_timeout_ms}"
            )
            cursor.execute(f"SET lock_timeout = {self.settings.lock_timeout_ms}")

    @property
    def pool(self) -> Any:
        if self._pool is None:
            try:
                from psycopg_pool import ConnectionPool
            except ImportError as error:
                raise RuntimeError(
                    "PostgreSQL storage requires requirements-storage.txt"
                ) from error
            pool = ConnectionPool(
                conninfo=self.settings.dsn,
                min_size=self.settings.min_pool_size,
                max_size=self.settings.max_pool_size,
                kwargs={"application_name": self.settings.application_name},
                configure=self._configure,
                open=False,
            )
            pool.open(wait=True)
            self._pool = pool
        return self._pool

    @contextmanager
    def connection(self) -> Iterator[Any]:
        with self.pool.connection() as connection:
            yield connection

    def close(self) -> None:
        pool = self._pool
        self._pool = None
        if pool is not None:
            pool.close()

    def __enter__(self) -> PostgresDatabase:
        _ = self.pool
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()


__all__ = ["PostgresDatabase", "PostgresSettings"]
