"""Migration-ready PostGIS index and predicate plan for stage 10."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_MAX_IDENTIFIER_BYTES = 63


def _identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a safe PostgreSQL identifier")
    return value


def _bounded_index_name(*parts: str) -> str:
    raw = "_".join(parts)
    if len(raw.encode("ascii")) <= _MAX_IDENTIFIER_BYTES:
        return raw
    suffix = hashlib.sha256(raw.encode("ascii")).hexdigest()[:10]
    prefix_length = _MAX_IDENTIFIER_BYTES - len(suffix) - 1
    return f"{raw[:prefix_length]}_{suffix}"


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
    polygon_name = _bounded_index_name(
        "idx",
        table,
        geometry_column,
        "exact",
        "gist",
    )
    radius_name = _bounded_index_name(
        "idx",
        table,
        geometry_column,
        "exact",
        "geography",
        "gist",
    )
    exact_predicate = f"WHERE {geometry_column} IS NOT NULL AND {exact_column}"
    return (
        PostGISIndexSpec(
            name=polygon_name,
            sql=(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {polygon_name} "
                f"ON {table} USING GIST ({geometry_column}) {exact_predicate};"
            ),
            purpose="partial geometry index for ST_Covers on exact points",
        ),
        PostGISIndexSpec(
            name=radius_name,
            sql=(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {radius_name} "
                f"ON {table} USING GIST (({geometry_column}::geography)) "
                f"{exact_predicate};"
            ),
            purpose="partial functional geography index for metric ST_DWithin",
        ),
    )


def spatial_query_plan(
    *,
    geometry_column: str = "geom",
    exact_column: str = "has_exact_geometry",
) -> PostGISQueryPlan:
    geometry_column = _identifier(geometry_column, "geometry_column")
    exact_column = _identifier(exact_column, "exact_column")
    eligibility = f"{geometry_column} IS NOT NULL AND {exact_column}"
    return PostGISQueryPlan(
        radius_predicate=(
            f"{eligibility} AND ST_DWithin({geometry_column}::geography, "
            "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)"
        ),
        polygon_predicate=(
            f"{eligibility} AND "
            "ST_Covers(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), "
            f"{geometry_column})"
        ),
    )
