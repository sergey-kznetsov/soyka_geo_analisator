from __future__ import annotations

import pytest

from soika_uds.spatial_filtering import spatial_index_plan, spatial_query_plan


def test_postgis_plan_uses_index_aware_predicates() -> None:
    indexes = spatial_index_plan()
    assert len(indexes) == 2
    assert all("USING GIST" in item.sql for item in indexes)
    assert "WHERE geom IS NOT NULL AND has_exact_geometry" in indexes[1].sql

    queries = spatial_query_plan()
    assert "ST_DWithin" in queries.radius_predicate
    assert "::geography" in queries.radius_predicate
    assert "ST_Covers" in queries.polygon_predicate
    assert queries.required_srid == 4326


def test_postgis_plan_rejects_unsafe_identifiers() -> None:
    with pytest.raises(ValueError, match="identifier"):
        spatial_index_plan(table="invalid-table-name")
