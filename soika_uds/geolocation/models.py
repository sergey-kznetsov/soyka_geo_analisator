"""Immutable contracts for production geolocation."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any

ALGORITHM_VERSION = "1.0.0"


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be finite")
    return result


def _confidence(value: object, field_name: str) -> float:
    result = _number(value, field_name)
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{field_name} must be in [0, 1]")
    return result


def _text(value: object, field_name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _freeze(value: object, field_name: str) -> Any:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        return _number(value, field_name)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field_name} keys must be strings")
            result[key] = _freeze(item, f"{field_name}.{key}")
        return MappingProxyType(result)
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


class LocationKind(str, Enum):
    HOUSE = "house"
    STREET = "street"
    INTERSECTION = "intersection"
    POI = "poi"
    DISTRICT = "district"
    LANDMARK = "landmark"
    CITY = "city"
    UNKNOWN = "unknown"


class MentionSource(str, Enum):
    PRIMARY_NER = "primary_ner"
    NATASHA = "natasha"
    RULES = "rules"


class CandidateSource(str, Enum):
    NOMINATIM = "nominatim"
    OVERPASS = "overpass"
    FIXTURE = "fixture"


@dataclass(frozen=True, slots=True)
class AddressMention:
    text: str
    normalized: str
    kind: LocationKind
    confidence: float
    source: MentionSource
    street: str | None = None
    house_number: str | None = None
    secondary_street: str | None = None
    poi: str | None = None
    district: str | None = None
    span_start: int | None = None
    span_end: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _text(self.text, "mention.text"))
        object.__setattr__(
            self,
            "normalized",
            _text(self.normalized, "mention.normalized"),
        )
        if not isinstance(self.kind, LocationKind):
            object.__setattr__(self, "kind", LocationKind(self.kind))
        if not isinstance(self.source, MentionSource):
            object.__setattr__(self, "source", MentionSource(self.source))
        object.__setattr__(
            self,
            "confidence",
            _confidence(self.confidence, "mention.confidence"),
        )
        for name in (
            "street",
            "house_number",
            "secondary_street",
            "poi",
            "district",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _text(value, f"mention.{name}"))
        if (self.span_start is None) != (self.span_end is None):
            raise ValueError("mention span bounds must be provided together")
        if self.span_start is not None:
            if not isinstance(self.span_start, int) or not isinstance(self.span_end, int):
                raise ValueError("mention span bounds must be integers")
            if self.span_start < 0 or self.span_end <= self.span_start:
                raise ValueError("mention span bounds are invalid")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "text": self.text,
            "normalized": self.normalized,
            "kind": self.kind.value,
            "confidence": self.confidence,
            "source": self.source.value,
        }
        for name in (
            "street",
            "house_number",
            "secondary_street",
            "poi",
            "district",
        ):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        if self.span_start is not None:
            payload["span_start"] = self.span_start
            payload["span_end"] = self.span_end
        return payload


@dataclass(frozen=True, slots=True)
class GeoPoint:
    longitude: float
    latitude: float

    def __post_init__(self) -> None:
        longitude = _number(self.longitude, "point.longitude")
        latitude = _number(self.latitude, "point.latitude")
        if not -180.0 <= longitude <= 180.0:
            raise ValueError("point.longitude must be in [-180, 180]")
        if not -90.0 <= latitude <= 90.0:
            raise ValueError("point.latitude must be in [-90, 90]")
        object.__setattr__(self, "longitude", longitude)
        object.__setattr__(self, "latitude", latitude)

    def to_geojson(self) -> dict[str, Any]:
        return {
            "type": "Point",
            "coordinates": [self.longitude, self.latitude],
        }


@dataclass(frozen=True, slots=True)
class GeocodingCandidate:
    candidate_id: str
    label: str
    kind: LocationKind
    point: GeoPoint
    confidence: float
    source: CandidateSource
    osm_type: str | None = None
    osm_id: int | None = None
    address: Mapping[str, Any] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "candidate_id",
            _text(self.candidate_id, "candidate_id"),
        )
        object.__setattr__(self, "label", _text(self.label, "candidate.label"))
        if not isinstance(self.kind, LocationKind):
            object.__setattr__(self, "kind", LocationKind(self.kind))
        if not isinstance(self.point, GeoPoint):
            raise ValueError("candidate.point must be GeoPoint")
        if not isinstance(self.source, CandidateSource):
            object.__setattr__(self, "source", CandidateSource(self.source))
        object.__setattr__(
            self,
            "confidence",
            _confidence(self.confidence, "candidate.confidence"),
        )
        if self.osm_type is not None:
            object.__setattr__(
                self,
                "osm_type",
                _text(self.osm_type, "candidate.osm_type"),
            )
        if self.osm_id is not None and (
            not isinstance(self.osm_id, int) or self.osm_id < 1
        ):
            raise ValueError("candidate.osm_id must be a positive integer")
        object.__setattr__(
            self,
            "address",
            _freeze(self.address, "candidate.address"),
        )
        reasons = tuple(_text(value, "candidate.reason") for value in self.reasons)
        object.__setattr__(self, "reasons", reasons)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "candidate_id": self.candidate_id,
            "label": self.label,
            "kind": self.kind.value,
            "geometry": self.point.to_geojson(),
            "confidence": self.confidence,
            "source": self.source.value,
            "address": _thaw(self.address),
            "reasons": list(self.reasons),
        }
        if self.osm_type is not None:
            payload["osm_type"] = self.osm_type
        if self.osm_id is not None:
            payload["osm_id"] = self.osm_id
        return payload


@dataclass(frozen=True, slots=True)
class MessageGeolocationResult:
    message_key: str
    mention: AddressMention | None
    candidates: tuple[GeocodingCandidate, ...]
    selected_candidate_id: str | None
    confidence: float
    included_for_analysis: bool
    reasons: tuple[str, ...]
    metric_crs: str | None
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "message_key",
            _text(self.message_key, "message_key"),
        )
        if self.mention is not None and not isinstance(
            self.mention,
            AddressMention,
        ):
            raise ValueError("mention must be AddressMention or None")
        candidates = tuple(self.candidates)
        if not all(isinstance(item, GeocodingCandidate) for item in candidates):
            raise ValueError("candidates must contain GeocodingCandidate values")
        ids = [item.candidate_id for item in candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate ids must be unique")
        object.__setattr__(self, "candidates", candidates)
        if self.selected_candidate_id is not None:
            selected = _text(
                self.selected_candidate_id,
                "selected_candidate_id",
            )
            if selected not in set(ids):
                raise ValueError("selected candidate must be present in candidates")
            object.__setattr__(self, "selected_candidate_id", selected)
        object.__setattr__(
            self,
            "confidence",
            _confidence(self.confidence, "confidence"),
        )
        if not isinstance(self.included_for_analysis, bool):
            raise ValueError("included_for_analysis must be boolean")
        object.__setattr__(
            self,
            "reasons",
            tuple(_text(value, "result.reason") for value in self.reasons),
        )
        if self.metric_crs is not None:
            object.__setattr__(
                self,
                "metric_crs",
                _text(self.metric_crs, "metric_crs"),
            )
        object.__setattr__(
            self,
            "provenance",
            _freeze(self.provenance, "provenance"),
        )

    @property
    def selected(self) -> GeocodingCandidate | None:
        if self.selected_candidate_id is None:
            return None
        return next(
            candidate
            for candidate in self.candidates
            if candidate.candidate_id == self.selected_candidate_id
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_key": self.message_key,
            "mention": self.mention.to_dict() if self.mention else None,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "selected_candidate_id": self.selected_candidate_id,
            "confidence": self.confidence,
            "included_for_analysis": self.included_for_analysis,
            "reasons": list(self.reasons),
            "metric_crs": self.metric_crs,
            "provenance": _thaw(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class GeolocationStats:
    received: int
    processed: int
    resolved: int
    low_confidence: int
    unresolved: int
    skipped: int

    def __post_init__(self) -> None:
        for name in (
            "received",
            "processed",
            "resolved",
            "low_confidence",
            "unresolved",
            "skipped",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.processed + self.skipped != self.received:
            raise ValueError("processed + skipped must equal received")
        if self.resolved + self.low_confidence + self.unresolved != self.processed:
            raise ValueError("processed outcome counts must match processed")

    def to_dict(self) -> dict[str, int]:
        return {
            name: getattr(self, name)
            for name in (
                "received",
                "processed",
                "resolved",
                "low_confidence",
                "unresolved",
                "skipped",
            )
        }


@dataclass(frozen=True, slots=True)
class GeolocationBatchResult:
    results: tuple[MessageGeolocationResult, ...]
    stats: GeolocationStats
    input_digest: str
    output_digest: str
    config_digest: str
    algorithm_version: str = ALGORITHM_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithm_version": self.algorithm_version,
            "results": [item.to_dict() for item in self.results],
            "stats": self.stats.to_dict(),
            "input_digest": self.input_digest,
            "output_digest": self.output_digest,
            "config_digest": self.config_digest,
        }


@dataclass(frozen=True, slots=True)
class GeolocationConfig:
    min_confidence: float = 0.65
    max_candidates: int = 5
    default_city: str | None = None
    country_codes: tuple[str, ...] = ("ru",)
    language: str = "ru"
    include_unclassified: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "min_confidence",
            _confidence(self.min_confidence, "config.min_confidence"),
        )
        if not isinstance(self.max_candidates, int) or not 1 <= self.max_candidates <= 40:
            raise ValueError("config.max_candidates must be in [1, 40]")
        if self.default_city is not None:
            object.__setattr__(
                self,
                "default_city",
                _text(self.default_city, "config.default_city"),
            )
        country_codes = tuple(
            _text(code, "country_code").lower() for code in self.country_codes
        )
        if any(len(code) != 2 or not code.isalpha() for code in country_codes):
            raise ValueError("country codes must be ISO alpha-2 values")
        object.__setattr__(self, "country_codes", country_codes)
        object.__setattr__(
            self,
            "language",
            _text(self.language, "config.language"),
        )
        if not isinstance(self.include_unclassified, bool):
            raise ValueError("config.include_unclassified must be boolean")

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_confidence": self.min_confidence,
            "max_candidates": self.max_candidates,
            "default_city": self.default_city,
            "country_codes": list(self.country_codes),
            "language": self.language,
            "include_unclassified": self.include_unclassified,
        }

    @property
    def digest(self) -> str:
        return digest_json(self.to_dict())
