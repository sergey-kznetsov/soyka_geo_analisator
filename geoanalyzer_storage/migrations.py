"""Checksum-verified transactional migrations for the shared storage platform."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from .contracts import sha256_digest
from .postgres import PostgresDatabase

_MIGRATION_NAME = re.compile(r"^(?P<version>[0-9]{4})_(?P<name>[a-z0-9_]+)\.sql$")
_MIGRATION_LOCK_ID = int.from_bytes(
    hashlib.sha256(b"geoanalyzer-storage-migrations-v1").digest()[:8],
    "big",
) & ((1 << 63) - 1)
_BOOTSTRAP_SQL = """
CREATE SCHEMA IF NOT EXISTS ga_meta;
CREATE TABLE IF NOT EXISTS ga_meta.schema_migrations (
    version BIGINT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    checksum CHAR(64) NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (checksum ~ '^[a-f0-9]{64}$')
);
"""


class MigrationError(RuntimeError):
    """Base migration failure."""


class MigrationChecksumError(MigrationError):
    """An already applied immutable migration changed on disk."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str
    checksum: str

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version < 1:
            raise ValueError("migration version must be a positive integer")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("migration name must be non-empty")
        if not isinstance(self.sql, str) or not self.sql.strip():
            raise ValueError("migration SQL must be non-empty")
        sha256_digest(self.checksum, "migration checksum")


def discover_migrations() -> tuple[Migration, ...]:
    root = files("geoanalyzer_storage").joinpath("sql", "migrations")
    result: list[Migration] = []
    for entry in root.iterdir():
        match = _MIGRATION_NAME.fullmatch(entry.name)
        if match is None:
            continue
        sql = entry.read_text(encoding="utf-8")
        result.append(
            Migration(
                version=int(match.group("version")),
                name=match.group("name"),
                sql=sql,
                checksum=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            )
        )
    ordered = tuple(sorted(result, key=lambda item: item.version))
    versions = [item.version for item in ordered]
    if len(versions) != len(set(versions)):
        raise MigrationError("migration versions must be unique")
    return ordered


class MigrationRunner:
    def __init__(self, database: PostgresDatabase) -> None:
        if not isinstance(database, PostgresDatabase):
            raise TypeError("database must be PostgresDatabase")
        self.database = database

    @staticmethod
    def _applied(connection: Any) -> dict[int, tuple[str, str]]:
        rows = connection.execute(
            "SELECT version, name, checksum FROM ga_meta.schema_migrations "
            "ORDER BY version"
        ).fetchall()
        return {int(row[0]): (str(row[1]), str(row[2])) for row in rows}

    def apply(self) -> tuple[Migration, ...]:
        migrations = discover_migrations()
        applied_now: list[Migration] = []
        with self.database.connection() as connection:
            with connection.transaction():
                connection.execute("SET LOCAL lock_timeout = '5s'")
                connection.execute(
                    "SELECT pg_advisory_xact_lock(%s)",
                    (_MIGRATION_LOCK_ID,),
                )
                connection.execute(_BOOTSTRAP_SQL)
                applied = self._applied(connection)
                for migration in migrations:
                    current = applied.get(migration.version)
                    if current is not None:
                        current_name, current_checksum = current
                        if (
                            current_name != migration.name
                            or current_checksum != migration.checksum
                        ):
                            raise MigrationChecksumError(
                                "applied migration changed: "
                                f"{migration.version:04d}_{migration.name}"
                            )
                        continue
                    connection.execute(migration.sql)
                    connection.execute(
                        "INSERT INTO ga_meta.schema_migrations"
                        "(version, name, checksum) VALUES (%s, %s, %s)",
                        (migration.version, migration.name, migration.checksum),
                    )
                    applied_now.append(migration)
        return tuple(applied_now)


__all__ = [
    "Migration",
    "MigrationChecksumError",
    "MigrationError",
    "MigrationRunner",
    "discover_migrations",
]
