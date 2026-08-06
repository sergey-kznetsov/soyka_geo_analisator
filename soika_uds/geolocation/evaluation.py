"""Address benchmark metrics for target cities."""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .crs import metric_distance_m
from .models import (
    GeoPoint,
    LocationKind,
    MessageGeolocationResult,
    digest_json,
)


@dataclass(frozen=True, slots=True)
class GeolocationValidationCase:
    message_key: str
    city: str
    expected_point: GeoPoint
    expected_kind: LocationKind
    tolerance_m: float

    def __post_init__(self) -> None:
        if not self.message_key.strip() or not self.city.strip():
            raise ValueError("validation key and city must be non-empty")
        if self.tolerance_m <= 0:
            raise ValueError("tolerance_m must be positive")


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, round((len(ordered) - 1) * fraction)),
    )
    return round(ordered[index], 3)


def evaluate_geolocation(
    expected: Sequence[GeolocationValidationCase],
    predictions: Sequence[MessageGeolocationResult],
) -> dict[str, Any]:
    expected_by_key = {item.message_key: item for item in expected}
    if len(expected_by_key) != len(expected):
        raise ValueError("duplicate validation message_key")
    prediction_by_key = {item.message_key: item for item in predictions}
    if len(prediction_by_key) != len(predictions):
        raise ValueError("duplicate prediction message_key")
    unknown = sorted(set(prediction_by_key) - set(expected_by_key))
    if unknown:
        raise ValueError("predictions contain unknown message_key values")
    distances: list[float] = []
    resolved = 0
    within_tolerance = 0
    kind_matches = 0
    city_rows: dict[str, dict[str, int]] = {}
    for key, case in expected_by_key.items():
        row = city_rows.setdefault(
            case.city,
            {
                "samples": 0,
                "resolved": 0,
                "within_tolerance": 0,
                "kind_matches": 0,
            },
        )
        row["samples"] += 1
        prediction = prediction_by_key.get(key)
        selected = prediction.selected if prediction else None
        if selected is None:
            continue
        resolved += 1
        row["resolved"] += 1
        distance = metric_distance_m(case.expected_point, selected.point)
        distances.append(distance)
        if distance <= case.tolerance_m:
            within_tolerance += 1
            row["within_tolerance"] += 1
        if selected.kind is case.expected_kind:
            kind_matches += 1
            row["kind_matches"] += 1
    sample_count = len(expected)
    report = {
        "samples": sample_count,
        "resolved": resolved,
        "resolution_rate": (
            round(resolved / sample_count, 6) if sample_count else 0.0
        ),
        "within_tolerance_rate": (
            round(within_tolerance / sample_count, 6)
            if sample_count
            else 0.0
        ),
        "kind_accuracy": (
            round(kind_matches / sample_count, 6) if sample_count else 0.0
        ),
        "median_distance_m": (
            round(statistics.median(distances), 3) if distances else None
        ),
        "p95_distance_m": _percentile(distances, 0.95),
        "cities": {
            city: {
                **row,
                "resolution_rate": round(
                    row["resolved"] / row["samples"],
                    6,
                ),
                "within_tolerance_rate": round(
                    row["within_tolerance"] / row["samples"],
                    6,
                ),
                "kind_accuracy": round(
                    row["kind_matches"] / row["samples"],
                    6,
                ),
            }
            for city, row in sorted(city_rows.items())
        },
    }
    report["report_digest"] = digest_json(report)
    return report
