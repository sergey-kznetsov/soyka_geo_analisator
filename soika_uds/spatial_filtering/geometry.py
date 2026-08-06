"""CRS-safe construction of radius and polygon territory targets."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from ..contracts import TerritoryContext
from ..geolocation.crs import metric_crs_for
from ..geolocation.models import GeoPoint
from .models import SOURCE_CRS, SpatialFilterConfig, TerritoryMode, digest_json


@dataclass(frozen=True, slots=True)
class SpatialTarget:
    mode: TerritoryMode
    source_crs: str
    metric_crs: str | None
    center: GeoPoint | None
    radius_meters: float | None
    geometry_geojson: Mapping[str, Any] | None
    geometry_wgs84: BaseGeometry | None
    geometry_metric: BaseGeometry | None
    digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "source_crs": self.source_crs,
            "metric_crs": self.metric_crs,
            "center": self.center.to_geojson() if self.center else None,
            "radius_meters": self.radius_meters,
            "geometry": dict(self.geometry_geojson) if self.geometry_geojson else None,
        }


def _as_mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise ValueError(f"{field_name} keys must be strings")
    return value


def _geojson_geometry(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if "crs" in payload:
        raise ValueError("RFC 7946 territory GeoJSON must not contain a crs member")
    geojson_type = payload.get("type")
    if geojson_type == "Feature":
        geometry = _as_mapping(payload.get("geometry"), "territory_geojson.geometry")
        if "crs" in geometry:
            raise ValueError("RFC 7946 geometry must not contain a crs member")
        return geometry
    return payload


def _coordinate_pairs(value: object) -> list[tuple[float, float]]:
    pairs: list[tuple[float, float]] = []

    def visit(item: object) -> None:
        if isinstance(item, Sequence) and not isinstance(item, str | bytes | bytearray):
            if (
                len(item) >= 2
                and isinstance(item[0], int | float)
                and not isinstance(item[0], bool)
                and isinstance(item[1], int | float)
                and not isinstance(item[1], bool)
            ):
                longitude = float(item[0])
                latitude = float(item[1])
                if not math.isfinite(longitude) or not math.isfinite(latitude):
                    raise ValueError("territory coordinates must be finite")
                if not -180.0 <= longitude <= 180.0:
                    raise ValueError("territory longitude must be in [-180, 180]")
                if not -90.0 <= latitude <= 90.0:
                    raise ValueError("territory latitude must be in [-90, 90]")
                pairs.append((longitude, latitude))
                return
            for child in item:
                visit(child)
            return
        raise ValueError("territory coordinates must be nested arrays of positions")

    visit(value)
    return pairs


def _validated_polygon(
    payload: Mapping[str, Any],
    config: SpatialFilterConfig,
) -> tuple[Mapping[str, Any], BaseGeometry, GeoPoint]:
    geometry_payload = _geojson_geometry(payload)
    geometry_type = geometry_payload.get("type")
    if geometry_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError("territory geometry must be Polygon or MultiPolygon")
    pairs = _coordinate_pairs(geometry_payload.get("coordinates"))
    if len(pairs) > config.max_polygon_vertices:
        raise ValueError("territory polygon exceeds max_polygon_vertices")
    if len(pairs) < 4:
        raise ValueError("territory polygon must contain at least four positions")
    longitudes = [item[0] for item in pairs]
    latitudes = [item[1] for item in pairs]
    if max(longitudes) - min(longitudes) > config.max_polygon_span_degrees:
        raise ValueError("territory polygon longitude span exceeds configured limit")
    if max(latitudes) - min(latitudes) > config.max_polygon_span_degrees:
        raise ValueError("territory polygon latitude span exceeds configured limit")
    geometry = shape(geometry_payload)
    if geometry.is_empty or not geometry.is_valid or geometry.area <= 0:
        raise ValueError("territory polygon must be non-empty and valid")
    representative = geometry.representative_point()
    center = GeoPoint(float(representative.x), float(representative.y))
    return dict(geometry_payload), geometry, center


def _project_geometry(geometry: BaseGeometry, target_crs: str) -> BaseGeometry:
    transformer = Transformer.from_crs("EPSG:4326", target_crs, always_xy=True)
    projected = transform(transformer.transform, geometry)
    if projected.is_empty or not projected.is_valid:
        raise ValueError("territory transformation produced invalid geometry")
    return projected


def build_spatial_target(
    territory: TerritoryContext,
    config: SpatialFilterConfig,
) -> SpatialTarget:
    if not isinstance(territory, TerritoryContext):
        raise TypeError("territory must be TerritoryContext")
    center = (
        GeoPoint(territory.longitude, territory.latitude)
        if territory.longitude is not None and territory.latitude is not None
        else None
    )
    radius = float(territory.radius_meters) if territory.radius_meters is not None else None
    has_radius = center is not None and radius is not None
    geometry_payload: Mapping[str, Any] | None = None
    geometry_wgs84: BaseGeometry | None = None
    polygon_center: GeoPoint | None = None
    if territory.territory_geojson is not None:
        geometry_payload, geometry_wgs84, polygon_center = _validated_polygon(
            territory.territory_geojson,
            config,
        )
    if has_radius and geometry_wgs84 is not None:
        mode = TerritoryMode.INTERSECTION
    elif has_radius:
        mode = TerritoryMode.RADIUS
    elif geometry_wgs84 is not None:
        mode = TerritoryMode.POLYGON
    else:
        mode = TerritoryMode.UNDEFINED
    crs_point = center or polygon_center
    metric_crs = metric_crs_for(crs_point) if crs_point is not None else None
    geometry_metric = (
        _project_geometry(geometry_wgs84, metric_crs)
        if geometry_wgs84 is not None and metric_crs is not None
        else None
    )
    target_payload = {
        "mode": mode.value,
        "source_crs": SOURCE_CRS,
        "metric_crs": metric_crs,
        "center": center.to_geojson() if center else None,
        "radius_meters": radius,
        "geometry": geometry_payload,
    }
    return SpatialTarget(
        mode=mode,
        source_crs=SOURCE_CRS,
        metric_crs=metric_crs,
        center=center,
        radius_meters=radius,
        geometry_geojson=geometry_payload,
        geometry_wgs84=geometry_wgs84,
        geometry_metric=geometry_metric,
        digest=digest_json(target_payload),
    )


def project_geo_point(point: GeoPoint, target_crs: str) -> Point:
    transformer = Transformer.from_crs("EPSG:4326", target_crs, always_xy=True)
    x, y = transformer.transform(point.longitude, point.latitude, errcheck=True)
    if not math.isfinite(x) or not math.isfinite(y):
        raise ValueError("point transformation returned non-finite coordinates")
    return Point(float(x), float(y))
