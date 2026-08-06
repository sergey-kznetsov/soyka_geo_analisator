from __future__ import annotations

import json

import pytest

from soika_uds.contracts import TerritoryContext
from soika_uds.geolocation import GeoPoint
from soika_uds.geolocation.crs import metric_distance_m
from soika_uds.spatial_filtering import (
    SpatialDecision,
    SpatialFilterConfig,
    SpatialFilterEngine,
    SpatialRelation,
    TerritoryMode,
    build_spatial_target,
)


def _result(
    key: str,
    longitude: float,
    latitude: float,
    *,
    kind: str = "house",
    eligible: bool = True,
) -> dict:
    candidate_id = f"candidate-{key}"
    return {
        "message_key": key,
        "included_for_analysis": eligible,
        "selected_candidate_id": candidate_id,
        "candidates": [
            {
                "candidate_id": candidate_id,
                "kind": kind,
                "geometry": {
                    "type": "Point",
                    "coordinates": [longitude, latitude],
                },
            }
        ],
        "reasons": [],
    }


def _polygon() -> dict:
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [49.0, 55.7],
                [49.2, 55.7],
                [49.2, 55.9],
                [49.0, 55.9],
                [49.0, 55.7],
            ]
        ],
    }


def test_radius_includes_boundary_and_excludes_outside() -> None:
    center = GeoPoint(49.1221, 55.7887)
    boundary = GeoPoint(49.1231, 55.7887)
    radius = metric_distance_m(center, boundary)
    territory = TerritoryContext(
        analysis_id="stage10-radius",
        city="Казань",
        latitude=center.latitude,
        longitude=center.longitude,
        radius_meters=radius,
    )
    batch = SpatialFilterEngine().filter(
        (
            _result("outside", 49.2, 55.8),
            _result("boundary", boundary.longitude, boundary.latitude),
            _result("inside", center.longitude, center.latitude),
        ),
        territory=territory,
    )

    by_key = {item.message_key: item for item in batch.results}
    assert by_key["inside"].decision is SpatialDecision.INCLUDED
    assert by_key["boundary"].relation is SpatialRelation.BOUNDARY
    assert by_key["boundary"].included_for_analysis is True
    assert by_key["outside"].decision is SpatialDecision.EXCLUDED
    assert batch.stats.to_dict() == {
        "received": 3,
        "evaluated": 3,
        "included": 2,
        "excluded": 1,
        "indeterminate": 0,
        "skipped": 0,
    }


def test_polygon_covers_boundary_and_respects_holes() -> None:
    polygon = _polygon()
    polygon["coordinates"].append(
        [
            [49.08, 55.78],
            [49.12, 55.78],
            [49.12, 55.82],
            [49.08, 55.82],
            [49.08, 55.78],
        ]
    )
    territory = TerritoryContext(
        analysis_id="stage10-polygon",
        city="Казань",
        territory_geojson=polygon,
    )
    batch = SpatialFilterEngine().filter(
        (
            _result("boundary", 49.0, 55.8),
            _result("hole", 49.1, 55.8),
            _result("inside", 49.15, 55.8),
        ),
        territory=territory,
    )

    by_key = {item.message_key: item for item in batch.results}
    assert by_key["boundary"].decision is SpatialDecision.INCLUDED
    assert by_key["boundary"].relation is SpatialRelation.BOUNDARY
    assert by_key["inside"].decision is SpatialDecision.INCLUDED
    assert by_key["hole"].decision is SpatialDecision.EXCLUDED


def test_intersection_requires_radius_and_polygon() -> None:
    territory = TerritoryContext(
        analysis_id="stage10-intersection",
        city="Казань",
        latitude=55.8,
        longitude=49.1,
        radius_meters=5_000,
        territory_geojson=_polygon(),
    )
    target = build_spatial_target(territory, SpatialFilterConfig())
    assert target.mode is TerritoryMode.INTERSECTION

    batch = SpatialFilterEngine().filter(
        (
            _result("inside-both", 49.1, 55.8),
            _result("polygon-only", 49.19, 55.8),
        ),
        territory=territory,
    )
    by_key = {item.message_key: item for item in batch.results}
    assert by_key["inside-both"].decision is SpatialDecision.INCLUDED
    assert by_key["polygon-only"].decision is SpatialDecision.EXCLUDED
    assert "outside_constraint" in by_key["polygon-only"].reasons


def test_missing_or_approximate_geometry_is_indeterminate() -> None:
    territory = TerritoryContext(
        analysis_id="stage10-indeterminate",
        city="Казань",
        territory_geojson=_polygon(),
    )
    missing = {
        "message_key": "missing",
        "included_for_analysis": True,
        "selected_candidate_id": None,
        "candidates": [],
        "reasons": [],
    }
    batch = SpatialFilterEngine().filter(
        (missing, _result("street", 49.1, 55.8, kind="street")),
        territory=territory,
    )

    assert all(
        item.decision is SpatialDecision.INDETERMINATE for item in batch.results
    )
    assert batch.stats.indeterminate == 2


def test_geolocation_ineligible_result_is_skipped() -> None:
    territory = TerritoryContext(
        analysis_id="stage10-skipped",
        city="Казань",
        territory_geojson=_polygon(),
    )
    batch = SpatialFilterEngine().filter(
        (_result("skipped", 49.1, 55.8, eligible=False),),
        territory=territory,
    )
    assert batch.results[0].decision is SpatialDecision.SKIPPED
    assert batch.stats.skipped == 1


def test_undefined_target_fails_closed_as_indeterminate() -> None:
    territory = TerritoryContext(
        analysis_id="stage10-no-target",
        city="Казань",
        address="Кремль",
    )
    batch = SpatialFilterEngine().filter(
        (_result("a", 49.1, 55.8),),
        territory=territory,
    )
    assert batch.target["mode"] == "undefined"
    assert batch.results[0].decision is SpatialDecision.INDETERMINATE
    assert batch.results[0].reasons == ("territory_geometry_missing",)


def test_output_is_order_independent_and_json_compatible() -> None:
    territory = TerritoryContext(
        analysis_id="stage10-order",
        city="Казань",
        territory_geojson=_polygon(),
    )
    engine = SpatialFilterEngine()
    first = engine.filter(
        (_result("b", 49.3, 55.8), _result("a", 49.1, 55.8)),
        territory=territory,
    )
    second = engine.filter(
        (_result("a", 49.1, 55.8), _result("b", 49.3, 55.8)),
        territory=territory,
    )
    assert first.output_digest == second.output_digest
    assert first.input_digest == second.input_digest
    assert [item.message_key for item in first.results] == ["a", "b"]
    json.dumps(first.to_dict(), ensure_ascii=False)


def test_geojson_crs_and_invalid_polygon_are_rejected() -> None:
    with pytest.raises(ValueError, match="crs member"):
        build_spatial_target(
            TerritoryContext(
                analysis_id="stage10-crs",
                city="Казань",
                territory_geojson={**_polygon(), "crs": {"type": "name"}},
            ),
            SpatialFilterConfig(),
        )
    with pytest.raises(ValueError, match="valid"):
        build_spatial_target(
            TerritoryContext(
                analysis_id="stage10-invalid",
                city="Казань",
                territory_geojson={
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [49.0, 55.7],
                            [49.2, 55.9],
                            [49.2, 55.7],
                            [49.0, 55.9],
                            [49.0, 55.7],
                        ]
                    ],
                },
            ),
            SpatialFilterConfig(),
        )
