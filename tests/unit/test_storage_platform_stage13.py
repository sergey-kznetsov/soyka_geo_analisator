from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType

import pytest

from geoanalyzer_storage import (
    ArtifactRecord,
    BackupPolicy,
    BackupTarget,
    MigrationError,
    MigrationRunner,
    PostgresDatabase,
    PostgresJsonCache,
    PostgresSettings,
    RetentionPolicy,
    application_id,
    cache_key,
    cache_namespace,
    discover_migrations,
    pg_dump_command,
    pg_restore_command,
)
from geoanalyzer_storage.cli import _ordered_scopes
from soika_uds import PostgresJobStore, ResponseCache


def test_application_and_cache_namespaces_are_strict() -> None:
    assert application_id("soika") == "soika"
    assert application_id("geo-service_2") == "geo-service_2"
    assert cache_namespace("osm.nominatim:v1") == "osm.nominatim:v1"

    with pytest.raises(ValueError):
        application_id("SOIKA")
    with pytest.raises(ValueError):
        application_id("bad schema")
    with pytest.raises(ValueError):
        cache_namespace("../shared")


def test_cache_key_is_deterministic_and_order_independent() -> None:
    first = cache_key("nominatim.search", {"q": "Казань", "limit": 5})
    second = cache_key("nominatim.search", {"limit": 5, "q": "Казань"})

    assert first == second
    assert len(first) == 64


def test_postgres_settings_never_repr_dsn_secret() -> None:
    settings = PostgresSettings(
        dsn="postgresql://geo:super-secret@db/geoanalyzer",
        application_name="soika-worker",
    )

    rendered = repr(settings)
    assert "super-secret" not in rendered
    assert "postgresql://" not in rendered
    assert "<redacted>" in rendered


def test_storage_dependencies_remain_lazy_at_import_time() -> None:
    assert callable(PostgresJsonCache.key)
    assert all(
        hasattr(PostgresJobStore, name)
        for name in (
            "create",
            "create_idempotent",
            "load",
            "save",
            "list_records",
            "find_by_idempotency_key",
        )
    )
    assert hasattr(ResponseCache, "get")


def test_lazy_pool_initialization_is_serialized(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("psycopg_pool")
    created: list[object] = []

    class FakePool:
        def __init__(self, **_kwargs: object) -> None:
            created.append(self)

        def open(self, *, wait: bool) -> None:
            assert wait is True
            time.sleep(0.02)

        def close(self) -> None:
            pass

    module.ConnectionPool = FakePool  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg_pool", module)
    database = PostgresDatabase(
        PostgresSettings(
            dsn="postgresql://example.invalid/test",
            min_pool_size=0,
            max_pool_size=1,
        )
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        pools = tuple(executor.map(lambda _index: database.pool, range(2)))

    assert len(created) == 1
    assert pools[0] is pools[1]
    database.close()


def test_failed_synchronous_pool_open_closes_local_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ModuleType("psycopg_pool")
    created: list[FakePool] = []

    class FakePool:
        def __init__(self, **_kwargs: object) -> None:
            self.close_calls = 0
            created.append(self)

        def open(self, *, wait: bool) -> None:
            assert wait is True
            raise RuntimeError("open failed")

        def close(self) -> None:
            self.close_calls += 1

    module.ConnectionPool = FakePool  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg_pool", module)
    database = PostgresDatabase(
        PostgresSettings(
            dsn="postgresql://example.invalid/test",
            min_pool_size=0,
            max_pool_size=1,
        )
    )

    for _attempt in range(2):
        with pytest.raises(RuntimeError, match="open failed"):
            _ = database.pool

    assert len(created) == 2
    assert [pool.close_calls for pool in created] == [1, 1]


def test_cli_applies_platform_before_dependent_scopes() -> None:
    assert _ordered_scopes(None) == ("platform",)
    assert _ordered_scopes(("soika",)) == ("platform", "soika")
    assert _ordered_scopes(("soika", "platform")) == ("platform", "soika")
    assert _ordered_scopes(("platform", "soika", "soika")) == (
        "platform",
        "soika",
    )


def test_migration_runner_honors_configured_lock_timeout() -> None:
    database = PostgresDatabase(
        PostgresSettings(
            dsn="postgresql://example.invalid/test",
            lock_timeout_ms=1_234,
            min_pool_size=0,
        )
    )
    runner = MigrationRunner(database, migrations=())
    calls: list[tuple[str, tuple[str, ...] | None]] = []

    class FakeConnection:
        def execute(
            self,
            query: str,
            params: tuple[str, ...] | None = None,
        ) -> None:
            calls.append((query, params))

    runner._set_transaction_lock_timeout(FakeConnection())

    assert calls == [
        ("SELECT set_config('lock_timeout', %s, true)", ("1234ms",)),
    ]


def test_migration_runner_rejects_applied_version_missing_from_package() -> None:
    database = PostgresDatabase(
        PostgresSettings(
            dsn="postgresql://example.invalid/test",
            min_pool_size=0,
        )
    )
    packaged = discover_migrations("platform")
    runner = MigrationRunner(database, scope="platform", migrations=packaged)
    applied = {
        packaged[0].version: (packaged[0].name, packaged[0].checksum),
        2: ("removed_history", "0" * 64),
    }

    with pytest.raises(
        MigrationError,
        match="applied migration missing from package: platform:0002_removed_history",
    ):
        runner._validate_applied_history(applied)


def test_migrations_are_scoped_ordered_and_hash_pinned() -> None:
    platform_migrations = discover_migrations("platform")
    soika_migrations = discover_migrations("soika")

    assert [(item.scope, item.version) for item in platform_migrations] == [
        ("platform", 1)
    ]
    assert [(item.scope, item.version) for item in soika_migrations] == [("soika", 1)]
    assert all(
        len(item.checksum) == 64 for item in platform_migrations + soika_migrations
    )
    platform = platform_migrations[0].sql
    soika = soika_migrations[0].sql
    assert "CREATE EXTENSION IF NOT EXISTS postgis" in platform
    assert "CREATE SCHEMA IF NOT EXISTS ga_core" in platform
    assert "CREATE SCHEMA IF NOT EXISTS ga_cache" in platform
    assert "geometry_json JSONB" in platform
    assert "CREATE SCHEMA IF NOT EXISTS ga_soika" not in platform
    assert "CREATE SCHEMA IF NOT EXISTS ga_soika" in soika
    assert "ga_soika.geocoding_results" in soika
    assert "USING GIST(point)" in soika
    assert "USING GIST((point::geography))" in soika


def test_artifact_content_is_deeply_immutable_after_digest_creation() -> None:
    payload = {"nested": {"items": [1, 2]}}
    artifact = ArtifactRecord(
        application_id="soika",
        analysis_id="analysis-1",
        artifact_type="fixture",
        artifact_key="nested",
        payload=payload,
    )
    digest = artifact.content_digest
    payload["nested"]["items"].append(3)

    assert artifact.content_digest == digest
    assert tuple(artifact.payload["nested"]["items"]) == (1, 2)
    with pytest.raises(TypeError):
        artifact.payload["nested"]["new"] = True


def test_backup_commands_do_not_accept_or_emit_passwords() -> None:
    target = BackupTarget(
        host="2001:db8::10",
        port=5432,
        database="geoanalyzer",
        user="backup_user",
    )
    dump = pg_dump_command(target, Path("backup.dump"))
    restore = pg_restore_command(target, Path("backup.dump"))

    assert "--format=custom" in dump
    assert "--no-owner" in dump
    assert "--clean" in restore
    assert "--if-exists" in restore
    assert "2001:db8::10" in dump
    assert not any("password" in part.casefold() for part in dump + restore)


def test_pg_restore_terminates_options_before_archive_path() -> None:
    target = BackupTarget(
        host="db.internal",
        port=5432,
        database="geoanalyzer",
        user="restore_user",
    )

    command = pg_restore_command(target, Path("--dbname=other"))

    assert command[-2:] == ("--", "--dbname=other")
    assert command.count("--dbname") == 1
    assert command[command.index("--dbname") + 1] == "geoanalyzer"


def test_retention_and_backup_policies_are_fail_closed() -> None:
    assert RetentionPolicy().cleanup_batch_size == 5_000
    assert BackupPolicy().keep_daily == 7

    with pytest.raises(ValueError):
        RetentionPolicy(completed_job_days=0)
    with pytest.raises(ValueError):
        BackupPolicy(keep_daily=0, keep_weekly=0, keep_monthly=0)


def test_storage_compose_pins_postgis_and_pg18_volume_layout() -> None:
    compose = Path("docker-compose.storage.yml").read_text(encoding="utf-8")

    assert "postgis/postgis:18-3.6@sha256:" in compose
    assert "/var/lib/postgresql" in compose
    assert "127.0.0.1:" in compose


def test_storage_requirements_are_hash_pinned() -> None:
    requirements = Path("requirements-storage.txt").read_text(encoding="utf-8")

    assert "typing-extensions==4.16.0" in requirements
    assert "psycopg==3.3.4" in requirements
    assert "psycopg-pool==3.3.1" in requirements
    assert requirements.count("--hash=sha256:") == 3
