"""Immutable analysis artifacts shared by ecosystem applications."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .contracts import application_id, canonical_json, digest_json
from .postgres import PostgresDatabase


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    application_id: str
    analysis_id: str
    artifact_type: str
    artifact_key: str
    payload: Mapping[str, Any]
    schema_version: str | None = None
    producer_version: str | None = None
    source_stage: str | None = None
    geometry: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "application_id", application_id(self.application_id))
        for name in ("analysis_id", "artifact_type", "artifact_key"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        for name in ("schema_version", "producer_version", "source_stage"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _text(value, name))
        if not isinstance(self.payload, Mapping):
            raise ValueError("artifact payload must be an object")
        canonical_json(self.payload)
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
        if self.geometry is not None:
            if not isinstance(self.geometry, Mapping):
                raise ValueError("artifact geometry must be a GeoJSON object")
            canonical_json(self.geometry)
            object.__setattr__(
                self,
                "geometry",
                MappingProxyType(dict(self.geometry)),
            )

    @property
    def content_digest(self) -> str:
        return digest_json(
            {
                "payload": dict(self.payload),
                "geometry": dict(self.geometry) if self.geometry is not None else None,
            }
        )


class PostgresArtifactStore:
    def __init__(self, database: PostgresDatabase) -> None:
        if not isinstance(database, PostgresDatabase):
            raise TypeError("database must be PostgresDatabase")
        self.database = database

    @staticmethod
    def _jsonb(value: Any) -> Any:
        try:
            from psycopg.types.json import Jsonb
        except ImportError as error:
            raise RuntimeError(
                "PostgreSQL storage requires requirements-storage.txt"
            ) from error
        return Jsonb(value)

    def put(self, artifact: ArtifactRecord) -> bool:
        if not isinstance(artifact, ArtifactRecord):
            raise TypeError("artifact must be ArtifactRecord")
        geometry = dict(artifact.geometry) if artifact.geometry is not None else None
        geometry_text = canonical_json(geometry) if geometry is not None else None
        with self.database.connection() as connection:
            row = connection.execute(
                "INSERT INTO ga_core.artifacts("
                "application_id, analysis_id, artifact_type, artifact_key, "
                "content_digest, schema_version, producer_version, source_stage, "
                "payload, geometry_json, geometry) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "CASE WHEN %s IS NULL THEN NULL ELSE "
                "ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326) END) "
                "ON CONFLICT DO NOTHING RETURNING content_digest",
                (
                    artifact.application_id,
                    artifact.analysis_id,
                    artifact.artifact_type,
                    artifact.artifact_key,
                    artifact.content_digest,
                    artifact.schema_version,
                    artifact.producer_version,
                    artifact.source_stage,
                    self._jsonb(dict(artifact.payload)),
                    self._jsonb(geometry) if geometry is not None else None,
                    geometry_text,
                    geometry_text,
                ),
            ).fetchone()
        return row is not None

    def get_latest(
        self,
        *,
        application: str,
        analysis_id: str,
        artifact_type: str,
        artifact_key: str,
    ) -> ArtifactRecord | None:
        application = application_id(application)
        analysis_id = _text(analysis_id, "analysis_id")
        artifact_type = _text(artifact_type, "artifact_type")
        artifact_key = _text(artifact_key, "artifact_key")
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT payload, schema_version, producer_version, source_stage, "
                "geometry_json, content_digest "
                "FROM ga_core.artifacts "
                "WHERE application_id = %s AND analysis_id = %s "
                "AND artifact_type = %s AND artifact_key = %s "
                "ORDER BY created_at DESC, content_digest DESC LIMIT 1",
                (application, analysis_id, artifact_type, artifact_key),
            ).fetchone()
        if row is None:
            return None
        artifact = ArtifactRecord(
            application_id=application,
            analysis_id=analysis_id,
            artifact_type=artifact_type,
            artifact_key=artifact_key,
            payload=row[0],
            schema_version=row[1],
            producer_version=row[2],
            source_stage=row[3],
            geometry=row[4],
        )
        if artifact.content_digest != row[5]:
            raise ValueError("persisted artifact digest does not match stored content")
        return artifact


__all__ = ["ArtifactRecord", "PostgresArtifactStore"]
