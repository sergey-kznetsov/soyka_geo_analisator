from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from geoanalyzer_storage import (
    MigrationRunner,
    PostgresDatabase,
    PostgresJsonCache,
    PostgresSettings,
)
from soika_uds.contracts import TerritoryContext
from soika_uds.integration import AnalysisRequestV1
from soika_uds.orchestration import ConcurrentUpdateError, JobRecord, PostgresJobStore


@pytest.fixture(scope="module")
def database() -> PostgresDatabase:
    dsn = os.environ.get("GEOANALYZER_TEST_DATABASE_DSN", "").strip()
    if not dsn:
        pytest.skip("GEOANALYZER_TEST_DATABASE_DSN is not configured")
    database = PostgresDatabase(
        PostgresSettings(
            dsn=dsn,
            application_name="stage13-integration",
            min_pool_size=0,
            max_pool_size=4,
        )
    )
    MigrationRunner(database).apply()
    try:
        yield database
    finally:
        database.close()


def test_migrations_are_transactional_and_idempotent(database: PostgresDatabase) -> None:
    assert MigrationRunner(database).apply() == ()

    with database.connection() as connection:
        versions = connection.execute(
            "SELECT version FROM ga_meta.schema_migrations ORDER BY version"
        ).fetchall()
        server_version = int(connection.execute("SHOW server_version_num").fetchone()[0])
        postgis_version = connection.execute(
            "SELECT postgis_lib_version()"
        ).fetchone()[0]

    assert versions == [(1,), (2,)]
    assert server_version // 10_000 == 18
    assert str(postgis_version).startswith("3.6")


def test_soika_domain_has_real_postgis_indexes(database: PostgresDatabase) -> None:
    with database.connection() as connection:
        srid = connection.execute(
            "SELECT Find_SRID('ga_soika', 'geocoding_results', 'point')"
        ).fetchone()[0]
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname = 'ga_soika'"
            ).fetchall()
        }

    assert srid == 4326
    assert "idx_ga_soika_geocoding_results_exact_gist" in indexes
    assert "idx_ga_soika_geocoding_results_exact_geography_gist" in indexes
    assert "idx_ga_soika_event_connections_geometry_gist" in indexes


def test_postgres_cache_has_ttl_and_application_isolation(
    database: PostgresDatabase,
) -> None:
    with database.connection() as connection:
        connection.execute(
            "INSERT INTO ga_meta.applications(application_id) VALUES ('other-service') "
            "ON CONFLICT (application_id) DO NOTHING"
        )

    soika_cache = PostgresJsonCache(
        database,
        application="soika",
        namespace="osm.nominatim:v1",
    )
    other_cache = PostgresJsonCache(
        database,
        application="other-service",
        namespace="osm.nominatim:v1",
    )
    key = soika_cache.key("search", {"q": "Казань", "limit": 5})
    soika_cache.set(key, {"source": "soika"}, ttl_seconds=60)
    other_cache.set(key, {"source": "other"}, ttl_seconds=60)

    assert soika_cache.get(key) == {"source": "soika"}
    assert other_cache.get(key) == {"source": "other"}

    with database.connection() as connection:
        connection.execute(
            "UPDATE ga_cache.entries SET "
            "created_at = clock_timestamp() - interval '2 seconds', "
            "expires_at = clock_timestamp() - interval '1 second' "
            "WHERE application_id = 'soika' AND namespace = %s AND key_digest = %s",
            (soika_cache.namespace, key),
        )

    assert soika_cache.get(key) is None
    assert other_cache.get(key) == {"source": "other"}


def _request(analysis_id: str) -> AnalysisRequestV1:
    return AnalysisRequestV1(
        analysis_id=analysis_id,
        requested_at=datetime(2026, 8, 7, 9, 0, tzinfo=UTC),
        territory=TerritoryContext(
            analysis_id=analysis_id,
            city="Казань",
            latitude=55.8,
            longitude=49.1,
            radius_meters=1_000,
        ),
    )


def test_postgres_job_store_preserves_orchestrator_contract(
    database: PostgresDatabase,
) -> None:
    store = PostgresJobStore(database)
    candidate = JobRecord.new(
        _request("stage13-postgres-job"),
        datetime(2026, 8, 7, 9, 0, tzinfo=UTC),
    )

    created = store.create_idempotent(candidate)
    repeated = store.create_idempotent(candidate)
    loaded = store.load(candidate.analysis_id)

    assert created.revision == 1
    assert repeated.to_dict() == created.to_dict()
    assert loaded.to_dict() == created.to_dict()
    assert store.find_by_idempotency_key(candidate.idempotency_key) == created

    saved = store.save(created, expected_revision=1)
    assert saved.revision == 2

    with pytest.raises(ConcurrentUpdateError):
        store.save(created, expected_revision=1)

    with database.connection() as connection:
        checkpoint_count = connection.execute(
            "SELECT count(*) FROM ga_core.stage_checkpoints "
            "WHERE application_id = 'soika' AND analysis_id = %s",
            (candidate.analysis_id,),
        ).fetchone()[0]

    assert checkpoint_count == len(created.checkpoints)
