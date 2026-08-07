from __future__ import annotations

import math

from soika_uds.events import EventCluster, EventLevel
from soika_uds.scoring import RiskScoringEngine


def _event(message_ids: tuple[str, ...]) -> EventCluster:
    return EventCluster(
        event_id="evt-global-wide",
        level=EventLevel.GLOBAL,
        object_id="global",
        message_ids=tuple(sorted(message_ids)),
        category="Другое",
        topic="global",
        keywords=("global",),
        representative_message_ids=(sorted(message_ids)[0],),
        started_at="2026-08-01T10:00:00Z",
        ended_at="2026-08-02T10:00:00Z",
        explanation={"basis": ["fixture"]},
    )


def test_geographically_wide_event_uses_global_geodesic_spread() -> None:
    event = _event(("m1", "m2"))
    points = {
        "m1": {"type": "Point", "coordinates": [0.0, 0.0]},
        "m2": {"type": "Point", "coordinates": [180.0, 0.0]},
    }

    result = RiskScoringEngine().score((event,), points)
    spread = next(
        item for item in result.event_scores[0].indicators if item.name == "spatial_spread"
    )

    assert spread.raw_value is not None
    assert math.isfinite(spread.raw_value)
    assert spread.raw_value > 10_000_000
    assert result.provenance["event_metric_crs"][event.event_id] is None
    assert result.provenance["event_spread_method"] == "local_aeqd_or_wgs84_geodesic"
