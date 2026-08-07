"""Immutable contracts for event connections, indicators, and risk scoring."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any

SCHEMA_VERSION = "1.0.0"
ALGORITHM_VERSION = "1.0.0"
FORMULA_VERSION = "1.0.0"
SOURCE_CRS = "OGC:CRS84"
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
INDICATOR_NAMES = ("intensity", "persistence", "connectivity", "spatial_spread")


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _unit(value: object, name: str) -> float:
    result = _number(value, name)
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{name} must be in [0, 1]")
    return result


def _positive(value: object, name: str) -> float:
    result = _number(value, name)
    if result <= 0.0:
        raise ValueError(f"{name} must be positive")
    return result


def _sha256(value: object, name: str) -> str:
    result = _text(value, name).lower()
    if _SHA256_RE.fullmatch(result) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return result


def _json_value(value: object, name: str) -> Any:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        return _number(value, name)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{name} keys must be strings")
            result[key] = _json_value(item, f"{name}.{key}")
        return result
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_value(item, f"{name}[]") for item in value]
    raise ValueError(f"{name} contains unsupported {type(value).__name__}")


def _freeze(value: object, name: str) -> Any:
    normalized = _json_value(value, name)
    if isinstance(normalized, dict):
        return MappingProxyType(
            {
                key: _freeze(item, f"{name}.{key}")
                for key, item in normalized.items()
            }
        )
    if isinstance(normalized, list):
        return tuple(_freeze(item, f"{name}[]") for item in normalized)
    return normalized


def _thaw(value: object) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def canonical_json(value: object) -> str:
    return json.dumps(
        _json_value(value, "canonical_json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def digest_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class RiskBand(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    UNAVAILABLE = "unavailable"


class IndicatorStatus(str, Enum):
    OBSERVED = "observed"
    MISSING = "missing"


class ConnectionKind(str, Enum):
    PEER_OVERLAP = "peer_overlap"
    CROSS_LEVEL_OVERLAP = "cross_level_overlap"


@dataclass(frozen=True, slots=True)
class RiskScoringConfig:
    indicator_weights: Mapping[str, float] = field(
        default_factory=lambda: {
            "intensity": 0.25,
            "persistence": 0.25,
            "connectivity": 0.25,
            "spatial_spread": 0.25,
        }
    )
    intensity_reference_messages: float = 20.0
    persistence_reference_hours: float = 168.0
    connectivity_reference_messages: float = 20.0
    spatial_spread_reference_m: float = 2_000.0
    medium_threshold: float = 0.25
    high_threshold: float = 0.50
    critical_threshold: float = 0.75
    source_crs: str = SOURCE_CRS
    schema_version: str = SCHEMA_VERSION
    algorithm_version: str = ALGORITHM_VERSION
    formula_version: str = FORMULA_VERSION

    def __post_init__(self) -> None:
        weights: dict[str, float] = {}
        if set(self.indicator_weights) != set(INDICATOR_NAMES):
            raise ValueError("indicator_weights must contain exactly the supported indicators")
        for name in INDICATOR_NAMES:
            weights[name] = _unit(self.indicator_weights[name], f"indicator_weights.{name}")
        if not math.isclose(sum(weights.values()), 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("indicator_weights must sum to 1")
        object.__setattr__(self, "indicator_weights", MappingProxyType(weights))
        for name in (
            "intensity_reference_messages",
            "persistence_reference_hours",
            "connectivity_reference_messages",
            "spatial_spread_reference_m",
        ):
            object.__setattr__(self, name, _positive(getattr(self, name), name))
        medium = _unit(self.medium_threshold, "medium_threshold")
        high = _unit(self.high_threshold, "high_threshold")
        critical = _unit(self.critical_threshold, "critical_threshold")
        if not 0.0 < medium < high < critical < 1.0:
            raise ValueError("risk thresholds must satisfy 0 < medium < high < critical < 1")
        object.__setattr__(self, "medium_threshold", medium)
        object.__setattr__(self, "high_threshold", high)
        object.__setattr__(self, "critical_threshold", critical)
        if _text(self.source_crs, "source_crs") != SOURCE_CRS:
            raise ValueError(f"source_crs must be {SOURCE_CRS}")
        for name in ("schema_version", "algorithm_version", "formula_version"):
            object.__setattr__(self, name, _text(getattr(self, name), name))

    def to_dict(self) -> dict[str, Any]:
        return {
            "indicator_weights": dict(self.indicator_weights),
            "intensity_reference_messages": self.intensity_reference_messages,
            "persistence_reference_hours": self.persistence_reference_hours,
            "connectivity_reference_messages": self.connectivity_reference_messages,
            "spatial_spread_reference_m": self.spatial_spread_reference_m,
            "medium_threshold": self.medium_threshold,
            "high_threshold": self.high_threshold,
            "critical_threshold": self.critical_threshold,
            "source_crs": self.source_crs,
            "schema_version": self.schema_version,
            "algorithm_version": self.algorithm_version,
            "formula_version": self.formula_version,
        }

    @property
    def digest(self) -> str:
        return digest_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class ExpertValidationManifest:
    formula_version: str
    config_digest: str
    review_id: str
    reviewer_role: str
    reviewed_at: str
    evidence_digest: str
    approved: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "formula_version", _text(self.formula_version, "formula_version")
        )
        object.__setattr__(self, "config_digest", _sha256(self.config_digest, "config_digest"))
        object.__setattr__(self, "review_id", _text(self.review_id, "review_id"))
        object.__setattr__(self, "reviewer_role", _text(self.reviewer_role, "reviewer_role"))
        object.__setattr__(self, "reviewed_at", _text(self.reviewed_at, "reviewed_at"))
        object.__setattr__(
            self, "evidence_digest", _sha256(self.evidence_digest, "evidence_digest")
        )
        if not isinstance(self.approved, bool):
            raise ValueError("approved must be boolean")

    def validates(self, config: RiskScoringConfig) -> bool:
        return (
            self.approved
            and self.formula_version == config.formula_version
            and self.config_digest == config.digest
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "formula_version": self.formula_version,
            "config_digest": self.config_digest,
            "review_id": self.review_id,
            "reviewer_role": self.reviewer_role,
            "reviewed_at": self.reviewed_at,
            "evidence_digest": self.evidence_digest,
            "approved": self.approved,
        }


@dataclass(frozen=True, slots=True)
class EventConnection:
    source_event_id: str
    target_event_id: str
    kind: ConnectionKind
    shared_message_ids: tuple[str, ...]
    jaccard: float
    same_category: bool
    same_topic: bool
    temporal_gap_hours: float | None
    geometry: Mapping[str, Any] | None
    source_crs: str
    metric_crs: str | None
    distance_m: float | None

    def __post_init__(self) -> None:
        source = _text(self.source_event_id, "source_event_id")
        target = _text(self.target_event_id, "target_event_id")
        if source >= target:
            raise ValueError("connection event ids must be canonical and strictly ordered")
        object.__setattr__(self, "source_event_id", source)
        object.__setattr__(self, "target_event_id", target)
        if not isinstance(self.kind, ConnectionKind):
            object.__setattr__(self, "kind", ConnectionKind(self.kind))
        shared = tuple(_text(item, "shared_message_ids[]") for item in self.shared_message_ids)
        if not shared or shared != tuple(sorted(set(shared))):
            raise ValueError("shared_message_ids must be sorted, unique and non-empty")
        object.__setattr__(self, "shared_message_ids", shared)
        object.__setattr__(self, "jaccard", _unit(self.jaccard, "jaccard"))
        if self.jaccard <= 0.0:
            raise ValueError("jaccard must be positive for a connection")
        for name in ("same_category", "same_topic"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be boolean")
        if self.temporal_gap_hours is not None:
            gap = _number(self.temporal_gap_hours, "temporal_gap_hours")
            if gap < 0.0:
                raise ValueError("temporal_gap_hours must not be negative")
            object.__setattr__(self, "temporal_gap_hours", gap)
        if self.geometry is not None:
            object.__setattr__(self, "geometry", _freeze(self.geometry, "geometry"))
        object.__setattr__(self, "source_crs", _text(self.source_crs, "source_crs"))
        if self.metric_crs is not None:
            object.__setattr__(self, "metric_crs", _text(self.metric_crs, "metric_crs"))
        if self.distance_m is not None:
            distance = _number(self.distance_m, "distance_m")
            if distance < 0.0:
                raise ValueError("distance_m must not be negative")
            object.__setattr__(self, "distance_m", distance)
        if (self.geometry is None) != (self.distance_m is None):
            raise ValueError("connection geometry and distance must be available together")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_event_id": self.source_event_id,
            "target_event_id": self.target_event_id,
            "kind": self.kind.value,
            "shared_message_ids": list(self.shared_message_ids),
            "jaccard": self.jaccard,
            "same_category": self.same_category,
            "same_topic": self.same_topic,
            "temporal_gap_hours": self.temporal_gap_hours,
            "geometry": _thaw(self.geometry),
            "source_crs": self.source_crs,
            "metric_crs": self.metric_crs,
            "distance_m": self.distance_m,
        }


@dataclass(frozen=True, slots=True)
class IndicatorScore:
    name: str
    status: IndicatorStatus
    raw_value: float | None
    normalized_value: float | None
    reference_value: float
    weight: float
    contribution: float | None
    reason: str

    def __post_init__(self) -> None:
        name = _text(self.name, "indicator.name")
        if name not in INDICATOR_NAMES:
            raise ValueError("unsupported indicator name")
        object.__setattr__(self, "name", name)
        if not isinstance(self.status, IndicatorStatus):
            object.__setattr__(self, "status", IndicatorStatus(self.status))
        object.__setattr__(
            self,
            "reference_value",
            _positive(self.reference_value, "reference_value"),
        )
        object.__setattr__(self, "weight", _unit(self.weight, "weight"))
        object.__setattr__(self, "reason", _text(self.reason, "reason"))
        values = (self.raw_value, self.normalized_value, self.contribution)
        if self.status is IndicatorStatus.MISSING:
            if any(value is not None for value in values):
                raise ValueError("missing indicator values must be null")
        else:
            raw = _number(self.raw_value, "raw_value")
            if raw < 0.0:
                raise ValueError("raw_value must not be negative")
            normalized = _unit(self.normalized_value, "normalized_value")
            contribution = _unit(self.contribution, "contribution")
            if not math.isclose(
                contribution,
                normalized * self.weight,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise ValueError("contribution must equal normalized_value * weight")
            object.__setattr__(self, "raw_value", raw)
            object.__setattr__(self, "normalized_value", normalized)
            object.__setattr__(self, "contribution", contribution)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "raw_value": self.raw_value,
            "normalized_value": self.normalized_value,
            "reference_value": self.reference_value,
            "weight": self.weight,
            "contribution": self.contribution,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class EventRiskScore:
    event_id: str
    indicators: tuple[IndicatorScore, ...]
    score: float | None
    band: RiskBand
    formula_version: str
    decision_use_approved: bool
    explanation: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _text(self.event_id, "event_id"))
        indicators = tuple(self.indicators)
        if tuple(item.name for item in indicators) != INDICATOR_NAMES:
            raise ValueError("indicators must follow the canonical indicator order")
        object.__setattr__(self, "indicators", indicators)
        if not isinstance(self.band, RiskBand):
            object.__setattr__(self, "band", RiskBand(self.band))
        if self.score is None:
            if self.band is not RiskBand.UNAVAILABLE:
                raise ValueError("missing score requires unavailable risk band")
        else:
            object.__setattr__(self, "score", _unit(self.score, "score"))
            expected = sum(item.contribution or 0.0 for item in indicators)
            if not math.isclose(self.score, expected, rel_tol=0.0, abs_tol=1e-12):
                raise ValueError("score must equal the sum of indicator contributions")
        object.__setattr__(
            self, "formula_version", _text(self.formula_version, "formula_version")
        )
        if not isinstance(self.decision_use_approved, bool):
            raise ValueError("decision_use_approved must be boolean")
        object.__setattr__(self, "explanation", _freeze(self.explanation, "explanation"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "indicators": [item.to_dict() for item in self.indicators],
            "score": self.score,
            "band": self.band.value,
            "formula_version": self.formula_version,
            "decision_use_approved": self.decision_use_approved,
            "explanation": _thaw(self.explanation),
        }


@dataclass(frozen=True, slots=True)
class ScoringStats:
    events: int
    connections: int
    scored: int
    unavailable_scores: int
    unique_messages: int

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"stats.{name} must be a non-negative integer")
        if self.scored + self.unavailable_scores != self.events:
            raise ValueError("scored + unavailable_scores must equal events")

    def to_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class ScoringBatchResult:
    connections: tuple[EventConnection, ...]
    event_scores: tuple[EventRiskScore, ...]
    stats: ScoringStats
    input_digest: str
    output_digest: str
    config_digest: str
    formula_validation: Mapping[str, Any]
    provenance: Mapping[str, Any]
    schema_version: str = SCHEMA_VERSION
    algorithm_version: str = ALGORITHM_VERSION
    formula_version: str = FORMULA_VERSION

    def __post_init__(self) -> None:
        connections = tuple(self.connections)
        scores = tuple(self.event_scores)
        connection_keys = [(item.source_event_id, item.target_event_id) for item in connections]
        if (
            connection_keys != sorted(connection_keys)
            or len(connection_keys) != len(set(connection_keys))
        ):
            raise ValueError("connections must be sorted and unique")
        score_ids = [item.event_id for item in scores]
        if score_ids != sorted(score_ids) or len(score_ids) != len(set(score_ids)):
            raise ValueError("event_scores must be sorted by unique event_id")
        object.__setattr__(self, "connections", connections)
        object.__setattr__(self, "event_scores", scores)
        if not isinstance(self.stats, ScoringStats):
            raise ValueError("stats must be ScoringStats")
        for name in ("input_digest", "output_digest", "config_digest"):
            object.__setattr__(self, name, _sha256(getattr(self, name), name))
        object.__setattr__(
            self,
            "formula_validation",
            _freeze(self.formula_validation, "formula_validation"),
        )
        object.__setattr__(self, "provenance", _freeze(self.provenance, "provenance"))
        for name in ("schema_version", "algorithm_version", "formula_version"):
            object.__setattr__(self, name, _text(getattr(self, name), name))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "algorithm_version": self.algorithm_version,
            "formula_version": self.formula_version,
            "connections": [item.to_dict() for item in self.connections],
            "event_scores": [item.to_dict() for item in self.event_scores],
            "stats": self.stats.to_dict(),
            "input_digest": self.input_digest,
            "output_digest": self.output_digest,
            "config_digest": self.config_digest,
            "formula_validation": _thaw(self.formula_validation),
            "provenance": _thaw(self.provenance),
        }
