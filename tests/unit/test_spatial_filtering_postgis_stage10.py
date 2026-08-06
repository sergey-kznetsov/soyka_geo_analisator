from __future__ import annotations

import pytest

from soika_uds.spatial_filtering import spatial_index_plan, spatial_query_plan


def test_postgis_plan_uses_index_aware_predicates() -> None:
    indexes = spatial_index_plan()
    assert len(indexes) == 2
    assert all("USING GIST" in item.sql for item in indexes)
    assert "USING GIST (geom)" in indexes[0].sql
    assert "USING GIST ((geom::geography))" in indexes[1].sql
    assert all(
        "WHERE geom IS NOT NULL AND has_exact_geometry" in item.sql
        for item in indexes
    )

    queries = spatial_query_plan()
    assert queries.radius_predicate.startswith(
        "geom IS NOT NULL AND has_exact_geometry AND ST_DWithin"
    )
    assert "geom::geography" in queries.radius_predicate
    assert queries.polygon_predicate.startswith(
        "geom IS NOT NULL AND has_exact_geometry AND ST_Covers"
    )
    assert queries.required_srid == 4326


def test_postgis_index_names_are_bounded_and_distinct() -> None:
    indexes = spatial_index_plan(
        table="t" * 63,
        geometry_column="g" * 63,
        exact_column="e" * 63,
    )

    assert len({item.name for item in indexes}) == 2
    assert all(len(item.name.encode("ascii")) <= 63 for item in indexes)
    assert all(f"IF NOT EXISTS {item.name} " in item.sql for item in indexes)


def test_postgis_plan_rejects_unsafe_identifiers() -> None:
    with pytest.raises(ValueError, match="identifier"):
        spatial_index_plan(table="invalid-table-name")
    with pytest.raises(ValueError, match="identifier"):
        spatial_query_plan(exact_column="invalid-column-name")
