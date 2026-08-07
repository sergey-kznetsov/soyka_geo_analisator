"""Deterministic connection building and transparent risk scoring."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import combinations
from typing import Any

from pyproj import Geod, Transformer

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

_WGS84_GEOD = Geod(ellps="WGS84")
_LOCAL_RADIUS_LIMIT_M = 1_500_000.0


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


def _wrapped_delta(longitude: float, reference: float) -> float:
    delta = longitude - reference
    while delta > 180.0:
        delta -= 360.0
    while delta < -180.0:
        delta += 360.0
    return delta


def _unwrap_longitude(longitude: float, reference: float) -> float:
    return reference + _wrapped_delta(longitude, reference)


def _spherical_centroid(points: Sequence[GeoPoint]) -> GeoPoint:
    if not points:
        raise ValueError("spherical centroid requires at least one point")
    x = 0.0
    y = 0.0
    z = 0.0
    for point in points:
        longitude = math.radians(point.longitude)
        latitude = math.radians(point.latitude)
        cos_lat = math.cos(latitude)
        x += cos_lat * math.cos(longitude)
        y += cos_lat * math.sin(longitude)
        z += math.sin(latitude)
    norm = math.sqrt(x * x + y * y + z * z)
    if norm < 1e-12:
        fallback = min(points, key=lambda item: (item.longitude, item.latitude))
        return GeoPoint(longitude=fallback.longitude, latitude=fallback.latitude)
    longitude = math.degrees(math.atan2(y, x))
    latitude = math.degrees(math.atan2(z, math.hypot(x, y)))
    return GeoPoint(longitude=longitude, latitude=latitude)


def _geodesic_distance(left: GeoPoint, right: GeoPoint) -> float:
    _azimuth, _back_azimuth, distance = _WGS84_GEOD.inv(
        left.longitude,
        left.latitude,
        right.longitude,
        right.latitude,
    )
    result = abs(float(distance))
    if not math.isfinite(result):
        raise ValueError("WGS84 geodesic distance must be finite")
    return result


def _aeqd_crs(center: GeoPoint) -> str:
    return (
        f"+proj=aeqd +lat_0={center.latitude:.12g} +lon_0={center.longitude:.12g} "
        "+datum=WGS84 +units=m +no_defs +type=crs"
    )


def _projected_points(
    points: Sequence[GeoPoint],
    center: GeoPoint,
) -> tuple[str, tuple[tuple[float, float], ...]]:
    metric_crs = _aeqd_crs(center)
    transformer = Transformer.from_crs(SOURCE_CRS, metric_crs, always_xy=True)
    projected = tuple(
        transformer.transform(item.longitude, item.latitude) for item in points
    )
    if any(not all(math.isfinite(value) for value in item) for item in projected):
        raise ValueError("local metric projection produced non-finite coordinates")
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

    spherical_center = _spherical_centroid(points)
    radial_distances = tuple(
        _geodesic_distance(spherical_center, point) for point in points
    )
    if max(radial_distances, default=0.0) > _LOCAL_RADIUS_LIMIT_M:
        return spherical_center, max(radial_distances), (), None

    metric_crs, projected = _projected_points(points, spherical_center)
    centroid_x = sum(item[0] for item in projected) / len(projected)
    centroid_y = sum(item[1] for item in projected) / len(projected)
    reverse = Transformer.from_crs(metric_crs, SOURCE_CRS, always_xy=True)
    longitude, latitude = reverse.transform(centroid_x, centroid_y)
    if not math.isfinite(longitude) or not math.isfinite(latitude):
        raise ValueError("local metric centroid produced non-finite coordinates")
    centroid = GeoPoint(longitude=longitude, latitude=latitude)
    spread = max(math.hypot(x - centroid_x, y - centroid_y) for x, y in projected)
    return centroid, spread, (), metric_crs


def _geodesic_midpoint(
    left: GeoPoint,
    azimuth: float,
    distance_m: float,
) -> GeoPoint:
    longitude, latitude, _back = _WGS84_GEOD.fwd(
        left.longitude,
        left.latitude,
        azimuth,
        distance_m / 2.0,
    )
    return GeoPoint(longitude=longitude, latitude=latitude)


def _antimeridian_crossing(
    left: GeoPoint,
    right: GeoPoint,
    azimuth: float,
    distance_m: float,
) -> tuple[float, float] | None:
    right_unwrapped = _unwrap_longitude(right.longitude, left.longitude)
    if -180.0 <= right_unwrapped <= 180.0:
        return None
    boundary = 180.0 if right_unwrapped > 180.0 else -180.0
    increasing = right_unwrapped > left.longitude
    low = 0.0
    high = 1.0
    crossing_latitude = (left.latitude + right.latitude) / 2.0
    for _ in range(64):
        fraction = (low + high) / 2.0
        longitude, latitude, _back = _WGS84_GEOD.fwd(
            left.longitude,
            left.latitude,
            azimuth,
            distance_m * fraction,
        )
        unwrapped = _unwrap_longitude(longitude, left.longitude)
        crossing_latitude = latitude
        if (unwrapped < boundary) == increasing:
            low = fraction
        else:
            high = fraction
    return boundary, crossing_latitude


def _connection_geojson(
    left: GeoPoint,
    right: GeoPoint,
    azimuth: float,
    distance_m: float,
) -> Mapping[str, Any]:
    crossing = _antimeridian_crossing(left, right, azimuth, distance_m)
    if crossing is None:
        return {
            "type": "LineString",
            "coordinates": [
                [left.longitude, left.latitude],
                [right.longitude, right.latitude],
            ],
        }
    boundary, latitude = crossing
    opposite = -180.0 if boundary > 0.0 else 180.0
    return {
        "type": "MultiLineString",
        "coordinates": [
            [
                [left.longitude, left.latitude],
                [boundary, latitude],
            ],
            [
                [opposite, latitude],
                [right.longitude, right.latitude],
            ],
        ],
    }


def _connection_geometry(
    left: GeoPoint | None,
    right: GeoPoint | None,
) -> tuple[Mapping[str, Any] | None, str | None, float | None]:
    if left is None or right is None:
        return None, None, None

    azimuth, _back_azimuth, geodesic_distance = _WGS84_GEOD.inv(
        left.longitude,
        left.latitude,
        right.longitude,
        right.latitude,
    )
    distance_m = abs(float(geodesic_distance))
    if not math.isfinite(distance_m):
        raise ValueError("connection geodesic distance must be finite")
    geometry = _connection_geojson(left, right, azimuth, distance_m)

    if distance_m > 2.0 * _LOCAL_RADIUS_LIMIT_M:
        return geometry, None, distance_m

    midpoint = _geodesic_midpoint(left, azimuth, distance_m)
    metric_crs = _aeqd_crs(midpoint)
    transformer = Transformer.from_crs(SOURCE_CRS, metric_crs, always_xy=True)
    left_xy = transformer.transform(left.longitude, left.latitude)
    right_xy = transformer.transform(right.longitude, right.latitude)
    if not all(math.isfinite(value) for value in (*left_xy, *right_xy)):
        return geometry, None, distance_m
    projected_distance = math.hypot(
        right_xy[0] - left_xy[0],
        right_xy[1] - left_xy[1],
    )
    if not math.isfinite(projected_distance):
        return geometry, None, distance_m
    return geometry, metric_crs, projected_distance


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
            source, target = (
                (left, right) if left.event_id < right.event_id else (right, left)
            )
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
            centroid, spread, missing, metric_crs = _centroid_and_spread(
                event, message_points
            )
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
            "line_distance_method": "local_aeqd_or_wgs84_geodesic",
            "antimeridian_geometry": "split_multilinestring",
            "event_spread_method": "local_aeqd_or_wgs84_geodesic",
            "local_projection_radius_limit_m": _LOCAL_RADIUS_LIMIT_M,
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
