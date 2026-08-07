from __future__ import annotations

import pytest

from soika_uds.events import EventCluster, EventLevel
from soika_uds.scoring import RiskScoringConfig, RiskScoringEngine


def _event(event_id: str, level: EventLevel, message_ids: tuple[str, ...]) -> EventCluster:
    return EventCluster(
        event_id=event_id,
        level=level,
        object_id=f"object:{level.value}",
        message_ids=tuple(sorted(message_ids)),
        category="ЖКХ",
        topic="дороги",
        keywords=("дорога",),
        representative_message_ids=(sorted(message_ids)[0],),
        started_at="2026-08-01T10:00:00Z",
        ended_at="2026-08-02T10:00:00Z",
        explanation={"basis": ["fixture"]},
    )


def test_polar_points_use_local_azimuthal_equidistant_metric_crs() -> None:
    events = (
        _event("evt-a", EventLevel.BUILDING, ("m1", "m2")),
        _event("evt-b", EventLevel.ROAD, ("m2", "m3")),
    )
    points = {
        "m1": {"type": "Point", "coordinates": [0.0, 89.9]},
        "m2": {"type": "Point", "coordinates": [1.0, 89.9]},
        "m3": {"type": "Point", "coordinates": [2.0, 89.9]},
    }

    result = RiskScoringEngine().score(events, points)
    by_id = {item.event_id: item for item in result.event_scores}
    spread = next(
        item for item in by_id["evt-a"].indicators if item.name == "spatial_spread"
    )
    connection = result.connections[0]

    assert spread.raw_value is not None
    assert spread.raw_value < 1_000
    assert connection.distance_m is not None
    assert connection.distance_m < 1_000
    assert connection.metric_crs is not None
    assert "+proj=aeqd" in connection.metric_crs
    assert result.provenance["polar_metric_crs"] == "local_azimuthal_equidistant"


@pytest.mark.parametrize(
    "weights",
    [
        {
            "intensity": 0.1,
            "persistence": 0.2,
            "connectivity": 0.3,
            "spatial_spread": 0.4000000005,
        },
        {
            "intensity": 0.06,
            "persistence": 0.57,
            "connectivity": 0.37,
            "spatial_spread": 0.0,
        },
    ],
)
def test_accepted_weight_tolerance_is_normalized_to_unit_sum(
    weights: dict[str, float],
) -> None:
    config = RiskScoringConfig(
        indicator_weights=weights,
        intensity_reference_messages=1,
        persistence_reference_hours=1,
        connectivity_reference_messages=1,
        spatial_spread_reference_m=1,
    )
    events = (
        _event("evt-a", EventLevel.BUILDING, ("m1", "m2")),
        _event("evt-b", EventLevel.ROAD, ("m2", "m3")),
    )
    points = {
        "m1": {"type": "Point", "coordinates": [49.10, 55.80]},
        "m2": {"type": "Point", "coordinates": [49.101, 55.801]},
        "m3": {"type": "Point", "coordinates": [49.102, 55.802]},
    }

    result = RiskScoringEngine(config=config).score(events, points)

    assert result.event_scores[0].score == pytest.approx(1.0)
    assert result.event_scores[1].score == pytest.approx(1.0)
    effective_weights = [item.weight for item in result.event_scores[0].indicators]
    assert sum(effective_weights) <= 1.0
    assert sum(effective_weights) == pytest.approx(1.0)
    assert result.provenance["weight_policy"] == (
        "normalize_tolerance_accepted_weights_to_unit_sum"
    )
