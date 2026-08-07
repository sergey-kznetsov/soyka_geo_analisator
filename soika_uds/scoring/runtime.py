"""Deterministic connection building and transparent risk scoring."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import combinations
from typing import Any

from pyproj import Transformer

from ..events import EventCluster
from ..geolocation import GeoPoint
from .models import (
    INDICATOR_NAMES,
    SOURCE_CRS,
    ConnectionKind,
    EventConnection,
    EventRiskScore,
    ExpertValidationManifest,
    IndicatorScore,
    IndicatorStatus,
    RiskBand,
    RiskScoringConfig,
    ScoringBatchResult,
    ScoringStats,
    digest_json,
)


def _parse_time(value: str | None, name: str) -> datetime | None:
    if value is None:
        return None
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise ValueError(f"{name} must be ISO 8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include UTC offset")
    return parsed.astimezone(UTC)


def _duration_hours(event: EventCluster) -> float | None:
    started = _parse_time(event.started_at, "event.started_at")
    ended = _parse_time(event.ended_at, "event.ended_at")
    if started is None or ended is None:
        return None
    if ended < started:
        raise ValueError("event.ended_at must not precede event.started_at")
    return (ended - started).total_seconds() / 3600.0


def _temporal_gap(left: EventCluster, right: EventCluster) -> float | None:
    left_start = _parse_time(left.started_at, "left.started_at")
    left_end = _parse_time(left.ended_at, "left.ended_at")
    right_start = _parse_time(right.started_at, "right.started_at")
    right_end = _parse_time(right.ended_at, "right.ended_at")
    if None in (left_start, left_end, right_start, right_end):
        return None
    assert left_start is not None and left_end is not None
    assert right_start is not None and right_end is not None
    if left_end < left_start or right_end < right_start:
        raise ValueError("event time bounds are invalid")
    if left_end < right_start:
        return (right_start - left_end).total_seconds() / 3600.0
    if right_end < left_start:
        return (left_start - right_end).total_seconds() / 3600.0
    return 0.0


def _point(value: object, name: str) -> GeoPoint | None:
    if value is None:
        return None
    if isinstance(value, GeoPoint):
        return value
    if not isinstance(value, Mapping) or value.get("type") != "Point":
        raise ValueError(f"{name} must be GeoJSON Point or null")
    coordinates = value.get("coordinates")
    if isinstance(coordinates, str | bytes | bytearray) or not isinstance(
        coordinates, Sequence
    ):
        raise ValueError(f"{name}.coordinates must be an array")
    if len(coordinates) != 2:
        raise ValueError(f"{name}.coordinates must contain longitude and latitude")
    return GeoPoint(longitude=coordinates[0], latitude=coordinates[1])


def _longitude_center(longitudes: Sequence[float]) -> float:
    """Return a wrapped circular longitude center in [-180, 180]."""
    if not longitudes:
        raise ValueError("longitude center requires at least one value")
    sin_sum = sum(math.sin(math.radians(value)) for value in longitudes)
    cos_sum = sum(math.cos(math.radians(value)) for value in longitudes)
    if math.hypot(sin_sum, cos_sum) < 1e-12:
        return float(longitudes[0])
    result = math.degrees(math.atan2(sin_sum, cos_sum))
    return 180.0 if math.isclose(result, -180.0, abs_tol=1e-12) else result


def _metric_crs(longitude: float, latitude: float) -> str:
    if -80.0 <= latitude <= 84.0:
        zone = min(60, max(1, int((longitude + 180.0) // 6.0) + 1))
        return f"EPSG:{32600 + zone if latitude >= 0.0 else 32700 + zone}"
    return (
        f"+proj=aeqd +lat_0={latitude:.12g} +lon_0={longitude:.12g} "
        "+datum=WGS84 +units=m +no_defs +type=crs"
    )


def _projected_points(
    points: Sequence[GeoPoint],
) -> tuple[str, tuple[tuple[float, float], ...]]:
    longitude = _longitude_center(tuple(item.longitude for item in points))
    latitude = sum(item.latitude for item in points) / len(points)
    metric_crs = _metric_crs(longitude, latitude)
    transformer = Transformer.from_crs(SOURCE_CRS, metric_crs, always_xy=True)
    projected = tuple(transformer.transform(item.longitude, item.latitude) for item in points)
    return metric_crs, projected


def _centroid_and_spread(
    event: EventCluster,
    message_points: Mapping[str, object],
) -> tuple[GeoPoint | None, float | None, tuple[str, ...], str | None]:
    missing: list[str] = []
    points: list[GeoPoint] = []
    for message_id in event.message_ids:
        point = _point(message_points.get(message_id), f"message_points.{message_id}")
        if point is None:
            missing.append(message_id)
        else:
            points.append(point)
    if missing:
        return None, None, tuple(missing), None
    if not points:
        return None, None, tuple(event.message_ids), None
    metric_crs, projected = _projected_points(points)
    centroid_x = sum(item[0] for item in projected) / len(projected)
    centroid_y = sum(item[1] for item in projected) / len(projected)
    reverse = Transformer.from_crs(metric_crs, SOURCE_CRS, always_xy=True)
    longitude, latitude = reverse.transform(centroid_x, centroid_y)
    spread = max(math.hypot(x - centroid_x, y - centroid_y) for x, y in projected)
    return GeoPoint(longitude=longitude, latitude=latitude), spread, (), metric_crs


def _connection_geometry(
    left: GeoPoint | None,
    right: GeoPoint | None,
) -> tuple[Mapping[str, Any] | None, str | None, float | None]:
    if left is None or right is None:
        return None, None, None
    midpoint_lon = _longitude_center((left.longitude, right.longitude))
    midpoint_lat = (left.latitude + right.latitude) / 2.0
    metric_crs = _metric_crs(midpoint_lon, midpoint_lat)
    transformer = Transformer.from_crs(SOURCE_CRS, metric_crs, always_xy=True)
    left_xy = transformer.transform(left.longitude, left.latitude)
    right_xy = transformer.transform(right.longitude, right.latitude)
    distance = math.hypot(right_xy[0] - left_xy[0], right_xy[1] - left_xy[1])
    geometry = {
        "type": "LineString",
        "coordinates": [
            [left.longitude, left.latitude],
            [right.longitude, right.latitude],
        ],
    }
    return geometry, metric_crs, distance


def _normalized(value: float, reference: float) -> float:
    if value < 0.0 or not math.isfinite(value):
        raise ValueError("indicator raw values must be finite and non-negative")
    if reference <= 0.0 or not math.isfinite(reference):
        raise ValueError("indicator reference values must be finite and positive")
    return min(1.0, value / reference)


def _effective_weights(config: RiskScoringConfig) -> dict[str, float]:
    raw = [config.indicator_weights[name] for name in INDICATOR_NAMES]
    total = sum(raw)
    result: dict[str, float] = {}
    remaining = 1.0
    for name, value in zip(INDICATOR_NAMES[:-1], raw[:-1], strict=True):
        weight = min(max(value / total, 0.0), remaining)
        result[name] = weight
        remaining = max(0.0, remaining - weight)
    result[INDICATOR_NAMES[-1]] = remaining
    return result


def _band(score: float, config: RiskScoringConfig) -> RiskBand:
    if score >= config.critical_threshold:
        return RiskBand.CRITICAL
    if score >= config.high_threshold:
        return RiskBand.HIGH
    if score >= config.medium_threshold:
        return RiskBand.MEDIUM
    return RiskBand.LOW


@dataclass(frozen=True, slots=True)
class RiskScoringEngine:
    config: RiskScoringConfig = field(default_factory=RiskScoringConfig)
    expert_validation: ExpertValidationManifest | None = None
    expert_validation_verifier: Callable[[ExpertValidationManifest], bool] | None = None

    def _validation_state(self) -> dict[str, Any]:
        manifest = self.expert_validation
        verifier = self.expert_validation_verifier
        if manifest is None:
            return {
                "approved": False,
                "manifest_approved": False,
                "manifest_matches_current_config": False,
                "external_verifier_configured": verifier is not None,
                "external_verification_passed": False,
                "status": "expert_validation_missing",
                "formula_version": self.config.formula_version,
                "config_digest": self.config.digest,
            }
        manifest_payload = manifest.to_dict()
        manifest_matches = (
            manifest.formula_version == self.config.formula_version
            and manifest.config_digest == self.config.digest
        )
        verifier_passed = False
        if manifest.approved and manifest_matches and verifier is not None:
            try:
                verifier_passed = verifier(manifest) is True
            except Exception:
                verifier_passed = False
        approved = manifest.approved and manifest_matches and verifier_passed
        if approved:
            status = "approved_for_decision_use"
        elif not manifest.approved:
            status = "expert_manifest_not_approved"
        elif not manifest_matches:
            status = "expert_manifest_stale"
        elif verifier is None:
            status = "external_verifier_missing"
        else:
            status = "external_verification_failed"
        return {
            **manifest_payload,
            "approved": approved,
            "manifest_approved": manifest.approved,
            "manifest_matches_current_config": manifest_matches,
            "external_verifier_configured": verifier is not None,
            "external_verification_passed": verifier_passed,
            "status": status,
        }

    @property
    def decision_use_approved(self) -> bool:
        return bool(self._validation_state()["approved"])

    def _connections(
        self,
        events: Sequence[EventCluster],
        centroids: Mapping[str, GeoPoint | None],
    ) -> tuple[EventConnection, ...]:
        result: list[EventConnection] = []
        for left, right in combinations(events, 2):
            shared = tuple(sorted(set(left.message_ids) & set(right.message_ids)))
            if not shared:
                continue
            union_size = len(set(left.message_ids) | set(right.message_ids))
            jaccard = len(shared) / union_size
            source, target = (left, right) if left.event_id < right.event_id else (right, left)
            geometry, metric_crs, distance = _connection_geometry(
                centroids[source.event_id], centroids[target.event_id]
            )
            result.append(
                EventConnection(
                    source_event_id=source.event_id,
                    target_event_id=target.event_id,
                    kind=(
                        ConnectionKind.PEER_OVERLAP
                        if source.level is target.level
                        else ConnectionKind.CROSS_LEVEL_OVERLAP
                    ),
                    shared_message_ids=shared,
                    jaccard=jaccard,
                    same_category=(
                        source.category is not None
                        and target.category is not None
                        and source.category == target.category
                    ),
                    same_topic=(
                        source.topic is not None
                        and target.topic is not None
                        and source.topic == target.topic
                    ),
                    temporal_gap_hours=_temporal_gap(source, target),
                    geometry=geometry,
                    source_crs=SOURCE_CRS,
                    metric_crs=metric_crs,
                    distance_m=distance,
                )
            )
        return tuple(
            sorted(result, key=lambda item: (item.source_event_id, item.target_event_id))
        )

    def _score_event(
        self,
        event: EventCluster,
        events_by_id: Mapping[str, EventCluster],
        connections: Sequence[EventConnection],
        spread_m: float | None,
        missing_points: Sequence[str],
        *,
        decision_use_approved: bool,
    ) -> EventRiskScore:
        neighbor_ids: set[str] = set()
        for connection in connections:
            if connection.source_event_id == event.event_id:
                neighbor_ids.add(connection.target_event_id)
            elif connection.target_event_id == event.event_id:
                neighbor_ids.add(connection.source_event_id)
        external_message_ids: set[str] = set()
        for neighbor_id in neighbor_ids:
            external_message_ids.update(events_by_id[neighbor_id].message_ids)
        external_message_ids.difference_update(event.message_ids)

        raw_values: dict[str, float | None] = {
            "intensity": float(event.size),
            "persistence": _duration_hours(event),
            "connectivity": float(len(external_message_ids)),
            "spatial_spread": spread_m,
        }
        references = {
            "intensity": self.config.intensity_reference_messages,
            "persistence": self.config.persistence_reference_hours,
            "connectivity": self.config.connectivity_reference_messages,
            "spatial_spread": self.config.spatial_spread_reference_m,
        }
        effective_weights = _effective_weights(self.config)
        missing_reasons = {
            "persistence": "event_time_bounds_unavailable",
            "spatial_spread": "member_message_geometry_unavailable",
        }
        indicators: list[IndicatorScore] = []
        for name in INDICATOR_NAMES:
            raw = raw_values[name]
            weight = effective_weights[name]
            reference = references[name]
            if raw is None:
                indicators.append(
                    IndicatorScore(
                        name=name,
                        status=IndicatorStatus.MISSING,
                        raw_value=None,
                        normalized_value=None,
                        reference_value=reference,
                        weight=weight,
                        contribution=None,
                        reason=missing_reasons[name],
                    )
                )
                continue
            normalized = _normalized(raw, reference)
            indicators.append(
                IndicatorScore(
                    name=name,
                    status=IndicatorStatus.OBSERVED,
                    raw_value=raw,
                    normalized_value=normalized,
                    reference_value=reference,
                    weight=weight,
                    contribution=normalized * weight,
                    reason="observed_and_normalized_against_fixed_reference",
                )
            )
        complete = all(item.status is IndicatorStatus.OBSERVED for item in indicators)
        score = sum(item.contribution or 0.0 for item in indicators) if complete else None
        band = _band(score, self.config) if score is not None else RiskBand.UNAVAILABLE
        return EventRiskScore(
            event_id=event.event_id,
            indicators=tuple(indicators),
            score=score,
            band=band,
            formula_version=self.config.formula_version,
            decision_use_approved=decision_use_approved,
            explanation={
                "formula": "sum(weight_i * normalized_indicator_i)",
                "normalization": "min(1, raw_value / fixed_reference)",
                "weight_policy": "normalize_tolerance_accepted_weights_to_unit_sum",
                "missing_data_policy": "score_unavailable_not_zero",
                "nested_event_policy": "connectivity_counts_unique_external_message_ids",
                "connection_count": len(neighbor_ids),
                "connected_unique_external_messages": len(external_message_ids),
                "missing_message_points": list(missing_points),
                "formula_expert_validated": decision_use_approved,
            },
        )

    def score(
        self,
        events: Sequence[EventCluster],
        message_points: Mapping[str, object],
    ) -> ScoringBatchResult:
        if isinstance(events, str | bytes | bytearray) or not isinstance(events, Sequence):
            raise TypeError("events must be an array")
        event_values = tuple(events)
        if not all(isinstance(item, EventCluster) for item in event_values):
            raise TypeError("events must contain EventCluster values")
        ordered = tuple(sorted(event_values, key=lambda item: item.event_id))
        ids = [item.event_id for item in ordered]
        if len(ids) != len(set(ids)):
            raise ValueError("event_id values must be unique")
        if not isinstance(message_points, Mapping):
            raise TypeError("message_points must be an object")
        for key in message_points:
            if not isinstance(key, str) or not key.strip():
                raise ValueError("message_points keys must be non-empty strings")

        centroids: dict[str, GeoPoint | None] = {}
        spreads: dict[str, float | None] = {}
        missing_points: dict[str, tuple[str, ...]] = {}
        metric_crs_by_event: dict[str, str | None] = {}
        for event in ordered:
            centroid, spread, missing, metric_crs = _centroid_and_spread(event, message_points)
            centroids[event.event_id] = centroid
            spreads[event.event_id] = spread
            missing_points[event.event_id] = missing
            metric_crs_by_event[event.event_id] = metric_crs

        connections = self._connections(ordered, centroids)
        validation = self._validation_state()
        decision_use_approved = bool(validation["approved"])
        events_by_id = {item.event_id: item for item in ordered}
        scores = tuple(
            self._score_event(
                event,
                events_by_id,
                connections,
                spreads[event.event_id],
                missing_points[event.event_id],
                decision_use_approved=decision_use_approved,
            )
            for event in ordered
        )
        unique_messages = sorted(
            {message_id for event in ordered for message_id in event.message_ids}
        )
        unique_message_set = set(unique_messages)
        input_core = {
            "events": [item.to_dict() for item in ordered],
            "message_points": {
                key: value.to_geojson() if isinstance(value, GeoPoint) else value
                for key, value in sorted(message_points.items())
                if key in unique_message_set
            },
        }
        stats = ScoringStats(
            events=len(ordered),
            connections=len(connections),
            scored=sum(item.score is not None for item in scores),
            unavailable_scores=sum(item.score is None for item in scores),
            unique_messages=len(unique_messages),
        )
        provenance = {
            "connection_evidence": "exact_message_id_set_intersection",
            "connection_weight": "jaccard_index",
            "line_source_crs": SOURCE_CRS,
            "line_distance_method": "pyproj_always_xy_to_local_metric_crs",
            "polar_metric_crs": "local_azimuthal_equidistant",
            "longitude_center_method": "circular_mean",
            "event_metric_crs": metric_crs_by_event,
            "dataset_relative_minmax": False,
            "zero_range_policy": "fixed_positive_references_remove_zero_range_division",
            "weight_policy": "normalize_tolerance_accepted_weights_to_unit_sum",
            "decision_use_approved": decision_use_approved,
        }
        output_core = {
            "connections": [item.to_dict() for item in connections],
            "event_scores": [item.to_dict() for item in scores],
            "stats": stats.to_dict(),
            "config_digest": self.config.digest,
            "formula_validation": validation,
            "provenance": provenance,
        }
        return ScoringBatchResult(
            connections=connections,
            event_scores=scores,
            stats=stats,
            input_digest=digest_json(input_core),
            output_digest=digest_json(output_core),
            config_digest=self.config.digest,
            formula_validation=validation,
            provenance=provenance,
            schema_version=self.config.schema_version,
            algorithm_version=self.config.algorithm_version,
            formula_version=self.config.formula_version,
        )
