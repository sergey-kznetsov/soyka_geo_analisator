from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import pytest

from geoanalyzer_storage import (
    ApplicationRegistration,
    ArtifactRecord,
    MigrationRunner,
    PostgresApplicationRegistry,
    PostgresArtifactStore,
    PostgresDatabase,
    PostgresJsonCache,
    PostgresSettings,
)
from soika_uds.contracts import TerritoryContext
from soika_uds.geolocation import (
    AddressMention,
    LocationKind,
    MentionSource,
    NominatimClient,
)
from soika_uds.integration import AnalysisRequestV1
from soika_uds.orchestration import (
    CheckpointState,
    ConcurrentUpdateError,
    JobRecord,
    PipelineStage,
    PostgresJobStore,
    StageCheckpoint,
)


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


def test_application_registry_is_shared_but_domain_schema_is_isolated(
    database: PostgresDatabase,
) -> None:
    registry = PostgresApplicationRegistry(database)
    registration = ApplicationRegistration(
        application_id="stage13-service",
        domain_schema="ga_stage13_service",
    )

    registry.register(registration)
    with database.connection() as connection:
        row = connection.execute(
            "SELECT domain_schema::text, active FROM ga_meta.applications "
            "WHERE application_id = %s",
            (registration.application_id,),
        ).fetchone()

    assert row == ("ga_stage13_service", True)
    assert registry.disable(registration.application_id) is True


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


class _CountingTransport:
    def __init__(self) -> None:
        self.calls = 0

    def request_json(self, _method: str, _url: str, **_kwargs: Any) -> Any:
        self.calls += 1
        return [
            {
                "lon": "49.1064",
                "lat": "55.7963",
                "display_name": "ул. Баумана, 1, Казань",
                "importance": 0.8,
                "osm_type": "way",
                "osm_id": 123,
                "addresstype": "house",
                "address": {"city": "Казань", "road": "улица Баумана"},
            }
        ]


def test_repeated_osm_lookup_uses_postgres_cache_without_redownload(
    database: PostgresDatabase,
) -> None:
    cache = PostgresJsonCache(
        database,
        application="soika",
        namespace="osm.nominatim:repeat-v1",
    )
    transport = _CountingTransport()
    mention = AddressMention(
        text="ул. Баумана, 1",
        normalized="улица баумана 1",
        kind=LocationKind.HOUSE,
        confidence=0.95,
        source=MentionSource.RULES,
        street="Баумана",
        house_number="1",
    )
    parameters = {
        "city": "Казань",
        "country_codes": ("ru",),
        "language": "ru",
        "limit": 5,
    }

    first = NominatimClient(transport, cache).search(mention, **parameters)
    second = NominatimClient(transport, cache).search(mention, **parameters)

    assert first == second
    assert len(first) == 1
    assert transport.calls == 1


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


def _create_job(database: PostgresDatabase, analysis_id: str) -> JobRecord:
    candidate = JobRecord.new(
        _request(analysis_id),
        datetime(2026, 8, 7, 9, 0, tzinfo=UTC),
    )
    return PostgresJobStore(database).create_idempotent(candidate)


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


def test_completed_checkpoint_output_is_immutable_artifact(
    database: PostgresDatabase,
) -> None:
    analysis_id = "stage13-checkpoint-artifact"
    store = PostgresJobStore(database)
    created = _create_job(database, analysis_id)
    completed = created.replace_checkpoint(
        StageCheckpoint(
            stage=PipelineStage.PREPARING,
            state=CheckpointState.COMPLETED,
            attempt=1,
            started_at=datetime(2026, 8, 7, 9, 0, tzinfo=UTC),
            completed_at=datetime(2026, 8, 7, 9, 1, tzinfo=UTC),
            updated_at=datetime(2026, 8, 7, 9, 1, tzinfo=UTC),
            output={"fixture": "completed-stage-output"},
        )
    )

    store.save(completed, expected_revision=created.revision)
    with database.connection() as connection:
        rows = connection.execute(
            "SELECT artifact_key, payload FROM ga_core.artifacts "
            "WHERE application_id = 'soika' AND analysis_id = %s "
            "AND artifact_type = 'stage-output'",
            (analysis_id,),
        ).fetchall()

    assert rows == [("preparing", {"fixture": "completed-stage-output"})]


def test_immutable_artifact_round_trip_preserves_digest_and_postgis_geometry(
    database: PostgresDatabase,
) -> None:
    analysis_id = "stage13-artifact-job"
    _create_job(database, analysis_id)
    store = PostgresArtifactStore(database)
    artifact = ArtifactRecord(
        application_id="soika",
        analysis_id=analysis_id,
        artifact_type="map-layer",
        artifact_key="event-centroid",
        payload={"event_id": "evt-1", "score": 0.75},
        schema_version="1.0.0",
        producer_version="0.18.0",
        source_stage="scoring",
        geometry={"type": "Point", "coordinates": [49.1064, 55.7963]},
    )

    assert store.put(artifact) is True
    assert store.put(artifact) is False
    loaded = store.get_latest(
        application="soika",
        analysis_id=analysis_id,
        artifact_type="map-layer",
        artifact_key="event-centroid",
    )

    assert loaded is not None
    assert loaded.content_digest == artifact.content_digest
    assert dict(loaded.geometry or {}) == dict(artifact.geometry or {})
    with database.connection() as connection:
        srid, geometry_type = connection.execute(
            "SELECT ST_SRID(geometry), GeometryType(geometry) "
            "FROM ga_core.artifacts WHERE application_id = 'soika' "
            "AND analysis_id = %s AND content_digest = %s",
            (analysis_id, artifact.content_digest),
        ).fetchone()

    assert srid == 4326
    assert geometry_type == "POINT"
