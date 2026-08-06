"""Immutable contracts for deterministic spatial territory filtering."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any

from ..geolocation.models import GeoPoint, LocationKind

ALGORITHM_VERSION = "1.0.0"
SOURCE_CRS = "OGC:CRS84"


def _number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be finite")
    return result


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    result = value.strip()
    if not result:
        raise ValueError(f"{field_name} must not be empty")
    return result


def _freeze(value: object, field_name: str) -> Any:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        return _number(value, field_name)
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field_name} keys must be strings")
            frozen[key] = _freeze(item, f"{field_name}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return tuple(
            _freeze(item, f"{field_name}[{index}]")
            for index, item in enumerate(value)
        )
    raise ValueError(f"{field_name} contains unsupported {type(value).__name__}")


def _thaw(value: object) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def canonical_json(value: object) -> str:
    return json.dumps(
        _thaw(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def digest_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class TerritoryMode(str, Enum):
    RADIUS = "radius"
    POLYGON = "polygon"
    INTERSECTION = "intersection"
    UNDEFINED = "undefined"


class SpatialDecision(str, Enum):
    INCLUDED = "included"
    EXCLUDED = "excluded"
    INDETERMINATE = "indeterminate"
    SKIPPED = "skipped"


class SpatialRelation(str, Enum):
    INSIDE = "inside"
    BOUNDARY = "boundary"
    OUTSIDE = "outside"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SpatialFilterConfig:
    source_crs: str = SOURCE_CRS
    boundary_epsilon_m: float = 0.5
    exact_location_kinds: tuple[LocationKind, ...] = (
        LocationKind.HOUSE,
        LocationKind.INTERSECTION,
        LocationKind.POI,
        LocationKind.LANDMARK,
    )
    max_polygon_vertices: int = 20_000
    max_polygon_span_degrees: float = 12.0

    def __post_init__(self) -> None:
        source_crs = _text(self.source_crs, "config.source_crs")
        if source_crs not in {SOURCE_CRS, "EPSG:4326"}:
            raise ValueError("config.source_crs must be OGC:CRS84 or EPSG:4326")
        object.__setattr__(self, "source_crs", source_crs)
        epsilon = _number(self.boundary_epsilon_m, "config.boundary_epsilon_m")
        if not 0.0 <= epsilon <= 10.0:
            raise ValueError("config.boundary_epsilon_m must be in [0, 10]")
        object.__setattr__(self, "boundary_epsilon_m", epsilon)
        kinds = tuple(
            item if isinstance(item, LocationKind) else LocationKind(item)
            for item in self.exact_location_kinds
        )
        if not kinds or len(kinds) != len(set(kinds)):
            raise ValueError("config.exact_location_kinds must be non-empty and unique")
        object.__setattr__(self, "exact_location_kinds", kinds)
        if type(self.max_polygon_vertices) is not int or self.max_polygon_vertices < 4:
            raise ValueError("config.max_polygon_vertices must be an integer >= 4")
        span = _number(self.max_polygon_span_degrees, "config.max_polygon_span_degrees")
        if not 0.1 <= span <= 180.0:
            raise ValueError("config.max_polygon_span_degrees must be in [0.1, 180]")
        object.__setattr__(self, "max_polygon_span_degrees", span)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_crs": self.source_crs,
            "boundary_epsilon_m": self.boundary_epsilon_m,
            "exact_location_kinds": sorted(item.value for item in self.exact_location_kinds),
            "max_polygon_vertices": self.max_polygon_vertices,
            "max_polygon_span_degrees": self.max_polygon_span_degrees,
        }

    @property
    def digest(self) -> str:
        return digest_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class SpatialMessageResult:
    message_key: str
    decision: SpatialDecision
    relation: SpatialRelation
    included_for_analysis: bool
    point: GeoPoint | None
    location_kind: LocationKind | None
    distance_m: float | None
    boundary_distance_m: float | None
    reasons: tuple[str, ...]
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_key", _text(self.message_key, "message_key"))
        if not isinstance(self.decision, SpatialDecision):
            object.__setattr__(self, "decision", SpatialDecision(self.decision))
        if not isinstance(self.relation, SpatialRelation):
            object.__setattr__(self, "relation", SpatialRelation(self.relation))
        if not isinstance(self.included_for_analysis, bool):
            raise ValueError("included_for_analysis must be boolean")
        if self.point is not None and not isinstance(self.point, GeoPoint):
            raise ValueError("point must be GeoPoint or None")
        if self.location_kind is not None and not isinstance(
            self.location_kind, LocationKind
        ):
            object.__setattr__(self, "location_kind", LocationKind(self.location_kind))
        for name in ("distance_m", "boundary_distance_m"):
            value = getattr(self, name)
            if value is not None:
                number = _number(value, name)
                if number < 0:
                    raise ValueError(f"{name} must not be negative")
                object.__setattr__(self, name, number)
        reasons = tuple(_text(item, "reason") for item in self.reasons)
        if not reasons:
            raise ValueError("reasons must not be empty")
        object.__setattr__(self, "reasons", reasons)
        object.__setattr__(self, "provenance", _freeze(self.provenance, "provenance"))
        expected = self.decision is SpatialDecision.INCLUDED
        if self.included_for_analysis is not expected:
            raise ValueError("included_for_analysis must match the included decision")

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_key": self.message_key,
            "decision": self.decision.value,
            "relation": self.relation.value,
            "included_for_analysis": self.included_for_analysis,
            "point": self.point.to_geojson() if self.point is not None else None,
            "location_kind": self.location_kind.value if self.location_kind else None,
            "distance_m": self.distance_m,
            "boundary_distance_m": self.boundary_distance_m,
            "reasons": list(self.reasons),
            "provenance": _thaw(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class SpatialFilterStats:
    received: int
    evaluated: int
    included: int
    excluded: int
    indeterminate: int
    skipped: int

    def __post_init__(self) -> None:
        for name in (
            "received",
            "evaluated",
            "included",
            "excluded",
            "indeterminate",
            "skipped",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.evaluated + self.indeterminate + self.skipped != self.received:
            raise ValueError("evaluated + indeterminate + skipped must equal received")
        if self.included + self.excluded != self.evaluated:
            raise ValueError("included + excluded must equal evaluated")

    def to_dict(self) -> dict[str, int]:
        return {
            "received": self.received,
            "evaluated": self.evaluated,
            "included": self.included,
            "excluded": self.excluded,
            "indeterminate": self.indeterminate,
            "skipped": self.skipped,
        }


@dataclass(frozen=True, slots=True)
class SpatialFilterBatchResult:
    results: tuple[SpatialMessageResult, ...]
    stats: SpatialFilterStats
    target: Mapping[str, Any]
    input_digest: str
    output_digest: str
    config_digest: str
    target_digest: str
    algorithm_version: str = ALGORITHM_VERSION

    def __post_init__(self) -> None:
        results = tuple(self.results)
        if not all(isinstance(item, SpatialMessageResult) for item in results):
            raise ValueError("results must contain SpatialMessageResult values")
        keys = [item.message_key for item in results]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("results must be sorted by unique message_key values")
        object.__setattr__(self, "results", results)
        if not isinstance(self.stats, SpatialFilterStats):
            raise ValueError("stats must be SpatialFilterStats")
        object.__setattr__(self, "target", _freeze(self.target, "target"))
        for name in ("input_digest", "output_digest", "config_digest", "target_digest"):
            value = _text(getattr(self, name), name)
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
            object.__setattr__(self, name, value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithm_version": self.algorithm_version,
            "results": [item.to_dict() for item in self.results],
            "stats": self.stats.to_dict(),
            "target": _thaw(self.target),
            "input_digest": self.input_digest,
            "output_digest": self.output_digest,
            "config_digest": self.config_digest,
            "target_digest": self.target_digest,
        }
