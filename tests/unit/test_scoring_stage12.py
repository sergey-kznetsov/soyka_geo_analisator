from __future__ import annotations

from datetime import UTC, datetime

import pytest

from soika_uds.events import EventCluster, EventLevel
from soika_uds.scoring import (
    ExpertValidationManifest,
    IndicatorStatus,
    RiskBand,
    RiskScoringConfig,
    RiskScoringEngine,
)


def _event(
    event_id: str,
    level: EventLevel,
    message_ids: tuple[str, ...],
    *,
    started: str | None = "2026-08-01T10:00:00Z",
    ended: str | None = "2026-08-02T10:00:00Z",
    topic: str = "освещение",
) -> EventCluster:
    return EventCluster(
        event_id=event_id,
        level=level,
        object_id=f"object:{level.value}",
        message_ids=tuple(sorted(message_ids)),
        category="ЖКХ",
        topic=topic,
        keywords=("фонарь",),
        representative_message_ids=(sorted(message_ids)[0],),
        started_at=started,
        ended_at=ended,
        explanation={"basis": ["fixture"]},
    )


def _points(*message_ids: str) -> dict[str, dict]:
    return {
        message_id: {
            "type": "Point",
            "coordinates": [49.10 + index * 0.001, 55.80 + index * 0.001],
        }
        for index, message_id in enumerate(message_ids)
    }


def _manifest(config: RiskScoringConfig, *, config_digest: str | None = None):
    return ExpertValidationManifest(
        formula_version=config.formula_version,
        config_digest=config_digest or config.digest,
        review_id="expert-review-2026-08",
        reviewer_role="urban-risk-domain-expert",
        reviewed_at=datetime(2026, 8, 7, tzinfo=UTC).isoformat(),
        evidence_digest="a" * 64,
        approved=True,
    )


def test_connections_intersect_message_ids_as_sets_not_characters() -> None:
    events = (
        _event("evt-a", EventLevel.BUILDING, ("12", "34")),
        _event("evt-b", EventLevel.ROAD, ("23", "45")),
    )
    result = RiskScoringEngine().score(events, _points("12", "34", "23", "45"))

    assert result.connections == ()


def test_connection_has_jaccard_and_crs_safe_geometry() -> None:
    events = (
        _event("evt-a", EventLevel.BUILDING, ("m1", "m2")),
        _event("evt-b", EventLevel.ROAD, ("m2", "m3")),
    )
    result = RiskScoringEngine().score(events, _points("m1", "m2", "m3"))

    connection = result.connections[0]
    assert connection.shared_message_ids == ("m2",)
    assert connection.jaccard == pytest.approx(1 / 3)
    assert connection.geometry["type"] == "LineString"
    assert connection.source_crs == "OGC:CRS84"
    assert connection.metric_crs.startswith("EPSG:326")
    assert connection.distance_m >= 0.0


def test_nested_events_do_not_double_count_connectivity_messages() -> None:
    events = (
        _event("evt-building", EventLevel.BUILDING, ("m1", "m2", "m3")),
        _event("evt-road", EventLevel.ROAD, ("m1", "m2", "m3")),
        _event("evt-global", EventLevel.GLOBAL, ("m1", "m2", "m3", "m4")),
    )
    result = RiskScoringEngine().score(events, _points("m1", "m2", "m3", "m4"))
    by_id = {item.event_id: item for item in result.event_scores}
    connectivity = next(
        item for item in by_id["evt-building"].indicators if item.name == "connectivity"
    )

    assert connectivity.raw_value == 1.0
    assert result.stats.unique_messages == 4


def test_missing_observation_makes_score_unavailable_not_zero() -> None:
    event = _event(
        "evt-a",
        EventLevel.BUILDING,
        ("m1", "m2"),
        started=None,
        ended=None,
    )
    result = RiskScoringEngine().score(
        (event,),
        {"m1": _points("m1")["m1"], "m2": None},
    )
    score = result.event_scores[0]

    assert score.score is None
    assert score.band is RiskBand.UNAVAILABLE
    assert any(item.status is IndicatorStatus.MISSING for item in score.indicators)
    assert score.explanation["missing_data_policy"] == "score_unavailable_not_zero"


def test_fixed_reference_normalization_handles_constant_dataset() -> None:
    events = (
        _event("evt-a", EventLevel.BUILDING, ("m1", "m2")),
        _event("evt-b", EventLevel.ROAD, ("m3", "m4")),
    )
    config = RiskScoringConfig(
        intensity_reference_messages=2,
        persistence_reference_hours=24,
        connectivity_reference_messages=2,
        spatial_spread_reference_m=1_000,
    )
    result = RiskScoringEngine(config=config).score(
        events,
        _points("m1", "m2", "m3", "m4"),
    )

    assert all(item.score is not None for item in result.event_scores)
    assert result.provenance["dataset_relative_minmax"] is False
    assert "fixed_positive_references" in result.provenance["zero_range_policy"]


def test_result_is_order_independent() -> None:
    events = (
        _event("evt-a", EventLevel.BUILDING, ("m1", "m2")),
        _event("evt-b", EventLevel.ROAD, ("m2", "m3")),
    )
    points = _points("m1", "m2", "m3")
    engine = RiskScoringEngine()

    first = engine.score(events, points)
    second = engine.score(tuple(reversed(events)), dict(reversed(tuple(points.items()))))

    assert first.input_digest == second.input_digest
    assert first.output_digest == second.output_digest
    assert first.to_dict() == second.to_dict()


def test_weights_and_thresholds_are_fail_closed() -> None:
    with pytest.raises(ValueError, match="sum to 1"):
        RiskScoringConfig(
            indicator_weights={
                "intensity": 1.0,
                "persistence": 1.0,
                "connectivity": 0.0,
                "spatial_spread": 0.0,
            }
        )
    with pytest.raises(ValueError, match="positive"):
        RiskScoringConfig(intensity_reference_messages=0)


def test_matching_manifest_without_external_verifier_stays_fail_closed() -> None:
    config = RiskScoringConfig()
    event = _event("evt-a", EventLevel.BUILDING, ("m1", "m2"))
    result = RiskScoringEngine(
        config=config,
        expert_validation=_manifest(config),
    ).score((event,), _points("m1", "m2"))

    assert result.event_scores[0].decision_use_approved is False
    assert result.formula_validation["manifest_approved"] is True
    assert result.formula_validation["approved"] is False
    assert result.formula_validation["status"] == "external_verifier_missing"


def test_matching_manifest_and_verified_evidence_enable_decision_use() -> None:
    config = RiskScoringConfig()
    manifest = _manifest(config)
    verified_ids: list[str] = []

    def verifier(candidate: ExpertValidationManifest) -> bool:
        verified_ids.append(candidate.review_id)
        return candidate.evidence_digest == "a" * 64

    event = _event("evt-a", EventLevel.BUILDING, ("m1", "m2"))
    result = RiskScoringEngine(
        config=config,
        expert_validation=manifest,
        expert_validation_verifier=verifier,
    ).score((event,), _points("m1", "m2"))

    assert result.event_scores[0].decision_use_approved is True
    assert result.formula_validation["approved"] is True
    assert result.formula_validation["external_verification_passed"] is True
    assert verified_ids == ["expert-review-2026-08"]


def test_stale_approved_manifest_is_reported_as_not_effectively_approved() -> None:
    config = RiskScoringConfig()
    manifest = _manifest(config, config_digest="b" * 64)
    event = _event("evt-a", EventLevel.BUILDING, ("m1", "m2"))
    result = RiskScoringEngine(
        config=config,
        expert_validation=manifest,
        expert_validation_verifier=lambda _manifest: True,
    ).score((event,), _points("m1", "m2"))

    assert result.formula_validation["manifest_approved"] is True
    assert result.formula_validation["manifest_matches_current_config"] is False
    assert result.formula_validation["approved"] is False
    assert result.formula_validation["status"] == "expert_manifest_stale"
    assert result.event_scores[0].decision_use_approved is False


def test_public_message_point_keys_are_validated() -> None:
    event = _event("evt-a", EventLevel.BUILDING, ("m1", "m2"))

    with pytest.raises(ValueError, match="message_points keys"):
        RiskScoringEngine().score((event,), {1: _points("m1")["m1"]})


def test_public_event_values_are_validated_before_sorting() -> None:
    with pytest.raises(TypeError, match="EventCluster"):
        RiskScoringEngine().score(({"event_id": "not-an-event"},), {})


def test_empty_events_are_supported() -> None:
    result = RiskScoringEngine().score((), {})

    assert result.connections == ()
    assert result.event_scores == ()
    assert result.stats.events == 0
