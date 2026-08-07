from __future__ import annotations

from soika_uds.events import EventCluster, EventLevel
from soika_uds.scoring import RiskScoringEngine


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


def test_antimeridian_points_keep_local_spread_and_connection_distance() -> None:
    events = (
        _event("evt-a", EventLevel.BUILDING, ("m1", "m2")),
        _event("evt-b", EventLevel.ROAD, ("m2", "m3")),
    )
    points = {
        "m1": {"type": "Point", "coordinates": [179.9, 10.0]},
        "m2": {"type": "Point", "coordinates": [-179.9, 10.0]},
        "m3": {"type": "Point", "coordinates": [-179.8, 10.0]},
    }

    result = RiskScoringEngine().score(events, points)
    by_id = {item.event_id: item for item in result.event_scores}
    spread = next(
        item for item in by_id["evt-a"].indicators if item.name == "spatial_spread"
    )
    connection = result.connections[0]

    assert spread.raw_value is not None
    assert spread.raw_value < 20_000
    assert connection.distance_m is not None
    assert connection.distance_m < 20_000
    assert connection.geometry["type"] == "MultiLineString"
    first, second = connection.geometry["coordinates"]
    assert abs(first[-1][0]) == 180.0
    assert abs(second[0][0]) == 180.0
    assert first[-1][0] == -second[0][0]
    assert result.provenance["antimeridian_geometry"] == "split_multilinestring"
