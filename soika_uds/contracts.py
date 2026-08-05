"""Stable contracts used between Geo Analyzer and SOIKA UDS Development."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any

from .prediction import Prediction


class JobStatus(str, Enum):
    QUEUED = "queued"
    PREPARING = "preparing"
    COLLECTING = "collecting"
    PREPROCESSING = "preprocessing"
    CLASSIFYING = "classifying"
    GEOCODING = "geocoding"
    FILTERING = "filtering"
    DETECTING_EVENTS = "detecting_events"
    CALCULATING = "calculating"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PrecisionLevel(str, Enum):
    BUILDING = "building"
    STREET = "street"
    INTERSECTION = "intersection"
    POI = "poi"
    DISTRICT = "district"
    CITY = "city"
    UNKNOWN = "unknown"


def _clean_required(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    return value


def _validate_coordinates(latitude: float | None, longitude: float | None) -> None:
    if (latitude is None) != (longitude is None):
        raise ValueError("latitude and longitude must be supplied together")
    if latitude is not None and not -90.0 <= latitude <= 90.0:
        raise ValueError("latitude must be in [-90, 90]")
    if longitude is not None and not -180.0 <= longitude <= 180.0:
        raise ValueError("longitude must be in [-180, 180]")


@dataclass(frozen=True, slots=True)
class TerritoryContext:
    """Validated territory received from the Geo Analyzer backend."""

    analysis_id: str
    city: str
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    radius_meters: int | None = None
    territory_geojson: Mapping[str, Any] | None = None
    period_from: date | None = None
    period_to: date | None = None
    sources: tuple[str, ...] = ()
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "analysis_id",
            _clean_required(self.analysis_id, "analysis_id"),
        )
        object.__setattr__(self, "city", _clean_required(self.city, "city"))

        if self.address is not None:
            address = self.address.strip()
            object.__setattr__(self, "address", address or None)

        _validate_coordinates(self.latitude, self.longitude)

        has_coordinates = self.latitude is not None
        if not self.address and not has_coordinates and self.territory_geojson is None:
            raise ValueError(
                "territory requires an address, a coordinate pair, or territory_geojson"
            )

        if self.radius_meters is not None and self.radius_meters <= 0:
            raise ValueError("radius_meters must be positive")
        if self.period_from and self.period_to and self.period_from > self.period_to:
            raise ValueError("period_from must not be later than period_to")

        normalized_sources = tuple(
            _clean_required(source, "source") for source in self.sources
        )
        object.__setattr__(self, "sources", normalized_sources)
        object.__setattr__(self, "options", MappingProxyType(dict(self.options)))

        if self.territory_geojson is not None:
            geojson = dict(self.territory_geojson)
            geojson_type = geojson.get("type")
            if geojson_type not in {"Polygon", "MultiPolygon", "Feature"}:
                raise ValueError(
                    "territory_geojson type must be Polygon, MultiPolygon, or Feature"
                )
            object.__setattr__(self, "territory_geojson", MappingProxyType(geojson))


@dataclass(frozen=True, slots=True)
class SourceMessage:
    """Source-independent message produced by every parser adapter."""

    source: str
    external_id: str
    text: str
    published_at: datetime
    url: str | None = None
    author_id: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _clean_required(self.source, "source"))
        object.__setattr__(
            self, "external_id", _clean_required(self.external_id, "external_id")
        )
        object.__setattr__(self, "text", _clean_required(self.text, "text"))
        if not isinstance(self.published_at, datetime):
            raise TypeError("published_at must be a datetime")
        _validate_coordinates(self.latitude, self.longitude)
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class ModelResult:
    """Structured classifier result with model provenance."""

    primary: Prediction
    alternatives: tuple[Prediction, ...] = ()
    model_id: str = ""
    model_revision: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_id", _clean_required(self.model_id, "model_id"))
        if self.model_revision is not None:
            revision = self.model_revision.strip()
            object.__setattr__(self, "model_revision", revision or None)


@dataclass(frozen=True, slots=True)
class MessageClassification:
    """Category and detailed topic for one source message."""

    message: SourceMessage
    category: ModelResult
    topic: ModelResult


@dataclass(frozen=True, slots=True)
class CoverageSummary:
    sources_requested: int = 0
    sources_available: int = 0
    messages_collected: int = 0
    messages_relevant: int = 0
    messages_geocoded: int = 0
    messages_low_confidence: int = 0

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            value = getattr(self, field_name)
            if value < 0:
                raise ValueError(f"{field_name} must not be negative")
        if self.sources_available > self.sources_requested:
            raise ValueError("sources_available cannot exceed sources_requested")
        if self.messages_relevant > self.messages_collected:
            raise ValueError("messages_relevant cannot exceed messages_collected")
        if self.messages_geocoded > self.messages_relevant:
            raise ValueError("messages_geocoded cannot exceed messages_relevant")


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    analysis_id: str
    status: JobStatus
    coverage: CoverageSummary = field(default_factory=CoverageSummary)
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "analysis_id",
            _clean_required(self.analysis_id, "analysis_id"),
        )
        object.__setattr__(
            self,
            "warnings",
            tuple(item.strip() for item in self.warnings if item.strip()),
        )
        object.__setattr__(
            self,
            "errors",
            tuple(item.strip() for item in self.errors if item.strip()),
        )
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
