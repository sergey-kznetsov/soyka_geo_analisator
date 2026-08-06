"""Metric CRS selection and distance helpers."""

from __future__ import annotations

import math

from .models import GeoPoint

_EARTH_RADIUS_M = 6_371_008.8


def metric_crs_for(point: GeoPoint) -> str:
    """Select the WGS84 UTM zone covering the point."""

    if not -80.0 <= point.latitude <= 84.0:
        return "EPSG:3857"
    zone = min(60, max(1, int((point.longitude + 180.0) // 6.0) + 1))
    code = 32600 + zone if point.latitude >= 0 else 32700 + zone
    return f"EPSG:{code}"


def project_point(point: GeoPoint, target_crs: str) -> tuple[float, float]:
    from pyproj import Transformer

    transformer = Transformer.from_crs("EPSG:4326", target_crs, always_xy=True)
    x, y = transformer.transform(point.longitude, point.latitude, errcheck=True)
    if not math.isfinite(x) or not math.isfinite(y):
        raise ValueError("coordinate transformation returned non-finite values")
    return float(x), float(y)


def metric_distance_m(first: GeoPoint, second: GeoPoint) -> float:
    midpoint = GeoPoint(
        longitude=(first.longitude + second.longitude) / 2.0,
        latitude=(first.latitude + second.latitude) / 2.0,
    )
    crs = metric_crs_for(midpoint)
    first_x, first_y = project_point(first, crs)
    second_x, second_y = project_point(second, crs)
    return math.hypot(second_x - first_x, second_y - first_y)


def haversine_distance_m(first: GeoPoint, second: GeoPoint) -> float:
    lat1 = math.radians(first.latitude)
    lat2 = math.radians(second.latitude)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(second.longitude - first.longitude)
    value = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2.0) ** 2
    )
    return 2.0 * _EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(value)))
