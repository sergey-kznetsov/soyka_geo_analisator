"""Deterministic spatial filtering of qualified geolocation results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..contracts import TerritoryContext
from ..geolocation.crs import metric_distance_m
from ..geolocation.models import GeoPoint, LocationKind
from .geometry import SpatialTarget, build_spatial_target, project_geo_point
from .models import (
    ALGORITHM_VERSION,
    SpatialDecision,
    SpatialFilterBatchResult,
    SpatialFilterConfig,
    SpatialFilterStats,
    SpatialMessageResult,
    SpatialRelation,
    TerritoryMode,
    digest_json,
)


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return value


def _sequence(value: object, field_name: str) -> Sequence[Any]:
    if isinstance(value, str | bytes | bytearray) or not isinstance(value, Sequence):
        raise ValueError(f"{field_name} must be an array")
    return value


def _selected_candidate(item: Mapping[str, Any]) -> Mapping[str, Any] | None:
    selected_id = item.get("selected_candidate_id")
    if selected_id is None:
        return None
    for candidate in _sequence(item.get("candidates", ()), "candidates"):
        candidate_mapping = _mapping(candidate, "candidate")
        if candidate_mapping.get("candidate_id") == selected_id:
            return candidate_mapping
    raise ValueError("selected candidate is not present in candidates")


def _point_from_candidate(candidate: Mapping[str, Any]) -> GeoPoint:
    geometry = _mapping(candidate.get("geometry"), "candidate.geometry")
    if geometry.get("type") != "Point":
        raise ValueError("selected candidate geometry must be Point")
    coordinates = _sequence(geometry.get("coordinates"), "candidate.geometry.coordinates")
    if len(coordinates) < 2:
        raise ValueError("Point coordinates must contain longitude and latitude")
    return GeoPoint(coordinates[0], coordinates[1])


def _base_result(
    *,
    item: Mapping[str, Any],
    decision: SpatialDecision,
    reason: str,
    point: GeoPoint | None = None,
    kind: LocationKind | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> SpatialMessageResult:
    return SpatialMessageResult(
        message_key=item["message_key"],
        decision=decision,
        relation=SpatialRelation.UNKNOWN,
        included_for_analysis=False,
        point=point,
        location_kind=kind,
        distance_m=None,
        boundary_distance_m=None,
        reasons=(reason,),
        provenance=provenance or {},
    )


class SpatialFilterEngine:
    def __init__(self, config: SpatialFilterConfig | None = None) -> None:
        self.config = config or SpatialFilterConfig()

    def _evaluate_radius(
        self,
        point: GeoPoint,
        target: SpatialTarget,
    ) -> tuple[bool, SpatialRelation, float, str]:
        if target.center is None or target.radius_meters is None:
            raise ValueError("radius target requires center and radius")
        distance = metric_distance_m(target.center, point)
        delta = abs(distance - target.radius_meters)
        if delta <= self.config.boundary_epsilon_m:
            return True, SpatialRelation.BOUNDARY, distance, "radius_boundary_included"
        if distance < target.radius_meters:
            return True, SpatialRelation.INSIDE, distance, "inside_radius"
        return False, SpatialRelation.OUTSIDE, distance, "outside_radius"

    def _evaluate_polygon(
        self,
        point: GeoPoint,
        target: SpatialTarget,
    ) -> tuple[bool, SpatialRelation, float, str]:
        if target.geometry_metric is None or target.metric_crs is None:
            raise ValueError("polygon target requires projected geometry")
        projected = project_geo_point(point, target.metric_crs)
        boundary_distance = float(target.geometry_metric.boundary.distance(projected))
        if target.geometry_metric.contains(projected):
            return True, SpatialRelation.INSIDE, boundary_distance, "inside_polygon"
        if (
            target.geometry_metric.covers(projected)
            or boundary_distance <= self.config.boundary_epsilon_m
        ):
            return True, SpatialRelation.BOUNDARY, boundary_distance, "polygon_boundary_included"
        return False, SpatialRelation.OUTSIDE, boundary_distance, "outside_polygon"

    def _evaluate_item(
        self,
        item: Mapping[str, Any],
        target: SpatialTarget,
    ) -> SpatialMessageResult:
        key = item.get("message_key")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("geolocation result message_key must be non-empty")
        if item.get("included_for_analysis") is not True:
            return _base_result(
                item=item,
                decision=SpatialDecision.SKIPPED,
                reason="geolocation_not_eligible",
                provenance={"geolocation_reasons": list(item.get("reasons", ()))},
            )
        candidate = _selected_candidate(item)
        if candidate is None:
            return _base_result(
                item=item,
                decision=SpatialDecision.INDETERMINATE,
                reason="exact_geometry_missing",
            )
        point = _point_from_candidate(candidate)
        kind = LocationKind(candidate.get("kind", LocationKind.UNKNOWN.value))
        if kind not in self.config.exact_location_kinds:
            return _base_result(
                item=item,
                decision=SpatialDecision.INDETERMINATE,
                reason="geometry_precision_not_exact",
                point=point,
                kind=kind,
            )
        provenance = {
            "source_crs": target.source_crs,
            "metric_crs": target.metric_crs,
            "target_digest": target.digest,
            "candidate_id": candidate.get("candidate_id"),
        }
        if target.mode is TerritoryMode.UNDEFINED:
            return _base_result(
                item=item,
                decision=SpatialDecision.INDETERMINATE,
                reason="territory_geometry_missing",
                point=point,
                kind=kind,
                provenance=provenance,
            )
        radius_result = None
        polygon_result = None
        if target.mode in {TerritoryMode.RADIUS, TerritoryMode.INTERSECTION}:
            radius_result = self._evaluate_radius(point, target)
        if target.mode in {TerritoryMode.POLYGON, TerritoryMode.INTERSECTION}:
            polygon_result = self._evaluate_polygon(point, target)
        checks = [result for result in (radius_result, polygon_result) if result is not None]
        included = all(result[0] for result in checks)
        relation = (
            SpatialRelation.BOUNDARY
            if included and any(result[1] is SpatialRelation.BOUNDARY for result in checks)
            else SpatialRelation.INSIDE
            if included
            else SpatialRelation.OUTSIDE
        )
        reasons = tuple(result[3] for result in checks)
        if target.mode is TerritoryMode.INTERSECTION:
            reasons = (*reasons, "inside_all_constraints" if included else "outside_constraint")
        distance_m = radius_result[2] if radius_result is not None else None
        boundary_distance_m = polygon_result[2] if polygon_result is not None else None
        return SpatialMessageResult(
            message_key=key,
            decision=(SpatialDecision.INCLUDED if included else SpatialDecision.EXCLUDED),
            relation=relation,
            included_for_analysis=included,
            point=point,
            location_kind=kind,
            distance_m=distance_m,
            boundary_distance_m=boundary_distance_m,
            reasons=reasons,
            provenance=provenance,
        )

    def filter(
        self,
        geolocation_results: Sequence[Mapping[str, Any]],
        *,
        territory: TerritoryContext,
    ) -> SpatialFilterBatchResult:
        if isinstance(geolocation_results, str | bytes | bytearray) or not isinstance(
            geolocation_results,
            Sequence,
        ):
            raise TypeError("geolocation_results must be an array")
        normalized = tuple(
            sorted(
                (_mapping(item, "geolocation_result") for item in geolocation_results),
                key=lambda item: str(item.get("message_key", "")),
            )
        )
        keys = [item.get("message_key") for item in normalized]
        if len(keys) != len(set(keys)):
            raise ValueError("geolocation result message_key values must be unique")
        target = build_spatial_target(territory, self.config)
        results = tuple(self._evaluate_item(item, target) for item in normalized)
        included = sum(item.decision is SpatialDecision.INCLUDED for item in results)
        excluded = sum(item.decision is SpatialDecision.EXCLUDED for item in results)
        indeterminate = sum(
            item.decision is SpatialDecision.INDETERMINATE for item in results
        )
        skipped = sum(item.decision is SpatialDecision.SKIPPED for item in results)
        stats = SpatialFilterStats(
            received=len(results),
            evaluated=included + excluded,
            included=included,
            excluded=excluded,
            indeterminate=indeterminate,
            skipped=skipped,
        )
        input_payload = [dict(item) for item in normalized]
        input_digest = digest_json(input_payload)
        target_payload = target.to_dict()
        output_core = {
            "algorithm_version": ALGORITHM_VERSION,
            "results": [item.to_dict() for item in results],
            "stats": stats.to_dict(),
            "target": target_payload,
            "input_digest": input_digest,
            "config_digest": self.config.digest,
            "target_digest": target.digest,
        }
        return SpatialFilterBatchResult(
            results=results,
            stats=stats,
            target=target_payload,
            input_digest=input_digest,
            output_digest=digest_json(output_core),
            config_digest=self.config.digest,
            target_digest=target.digest,
        )
