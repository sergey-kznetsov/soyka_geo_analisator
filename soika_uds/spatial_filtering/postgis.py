"""Migration-ready PostGIS index and predicate plan for stage 10."""

from __future__ import annotations

import re
from dataclasses import dataclass

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a safe PostgreSQL identifier")
    return value


@dataclass(frozen=True, slots=True)
class PostGISIndexSpec:
    name: str
    sql: str
    purpose: str


@dataclass(frozen=True, slots=True)
class PostGISQueryPlan:
    radius_predicate: str
    polygon_predicate: str
    required_srid: int = 4326


def spatial_index_plan(
    *,
    table: str = "message_geometries",
    geometry_column: str = "geom",
    exact_column: str = "has_exact_geometry",
) -> tuple[PostGISIndexSpec, ...]:
    table = _identifier(table, "table")
    geometry_column = _identifier(geometry_column, "geometry_column")
    exact_column = _identifier(exact_column, "exact_column")
    general_name = f"idx_{table}_{geometry_column}_gist"
    exact_name = f"idx_{table}_{geometry_column}_exact_gist"
    return (
        PostGISIndexSpec(
            name=general_name,
            sql=(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {general_name} "
                f"ON {table} USING GIST ({geometry_column});"
            ),
            purpose="general polygon and distance prefilter",
        ),
        PostGISIndexSpec(
            name=exact_name,
            sql=(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {exact_name} "
                f"ON {table} USING GIST ({geometry_column}) "
                f"WHERE {geometry_column} IS NOT NULL AND {exact_column};"
            ),
            purpose="partial index for exact geometries eligible for analysis",
        ),
    )


def spatial_query_plan(
    *,
    geometry_column: str = "geom",
) -> PostGISQueryPlan:
    geometry_column = _identifier(geometry_column, "geometry_column")
    return PostGISQueryPlan(
        radius_predicate=(
            f"ST_DWithin({geometry_column}::geography, "
            "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)"
        ),
        polygon_predicate=(
            "ST_Covers(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), "
            f"{geometry_column})"
        ),
    )
