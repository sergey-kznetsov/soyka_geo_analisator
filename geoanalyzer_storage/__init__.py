"""Shared PostgreSQL/PostGIS storage platform for the Geo Analyzer ecosystem."""

from .artifacts import ArtifactRecord, PostgresArtifactStore
from .backup import BackupPolicy, BackupTarget, pg_dump_command, pg_restore_command
from .cache import PostgresJsonCache
from .contracts import (
    JsonCache,
    application_id,
    cache_key,
    cache_namespace,
    canonical_json,
    digest_json,
)
from .migrations import (
    Migration,
    MigrationChecksumError,
    MigrationError,
    MigrationRunner,
    discover_migrations,
)
from .postgres import PostgresDatabase, PostgresSettings
from .registry import ApplicationRegistration, PostgresApplicationRegistry
from .retention import RetentionManager, RetentionPolicy

__version__ = "1.1.0"

__all__ = [
    "ApplicationRegistration",
    "ArtifactRecord",
    "BackupPolicy",
    "BackupTarget",
    "JsonCache",
    "Migration",
    "MigrationChecksumError",
    "MigrationError",
    "MigrationRunner",
    "PostgresApplicationRegistry",
    "PostgresArtifactStore",
    "PostgresDatabase",
    "PostgresJsonCache",
    "PostgresSettings",
    "RetentionManager",
    "RetentionPolicy",
    "application_id",
    "cache_key",
    "cache_namespace",
    "canonical_json",
    "digest_json",
    "discover_migrations",
    "pg_dump_command",
    "pg_restore_command",
]
