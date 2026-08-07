"""Immutable contracts for reproducible event and thematic clustering."""

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
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


def _required(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _json_value(value: object, field_name: str) -> Any:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field_name} keys must be strings")
            result[key] = _json_value(item, f"{field_name}.{key}")
        return result
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [
            _json_value(item, f"{field_name}[{index}]")
            for index, item in enumerate(value)
        ]
    raise ValueError(f"{field_name} contains unsupported {type(value).__name__}")


def _freeze(value: object, field_name: str) -> Any:
    normalized = _json_value(value, field_name)
    if isinstance(normalized, dict):
        return MappingProxyType(
            {
                key: _freeze(item, f"{field_name}.{key}")
                for key, item in normalized.items()
            }
        )
    if isinstance(normalized, list):
        return tuple(
            _freeze(item, f"{field_name}[{index}]")
            for index, item in enumerate(normalized)
        )
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


def _sha256(value: object, field_name: str) -> str:
    normalized = _required(value, field_name).lower()
    if _SHA256_RE.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return normalized


class EventLevel(str, Enum):
    BUILDING = "building"
    LINK = "link"
    ROAD = "road"
    GLOBAL = "global"


EVENT_LEVELS = tuple(EventLevel)


class ScopeStatus(str, Enum):
    CLUSTERED = "clustered"
    INSUFFICIENT_DATA = "insufficient_data"
    NO_CLUSTERS = "no_clusters"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class EventClusteringConfig:
    levels: tuple[EventLevel, ...] = EVENT_LEVELS
    min_scope_messages: int = 5
    min_event_size: int = 3
    allow_single_cluster: bool = True
    include_noise: bool = False
    random_seed: int = 42
    keyword_limit: int = 6
    representative_limit: int = 3
    max_events_per_scope: int = 100
    schema_version: str = SCHEMA_VERSION
    algorithm_version: str = ALGORITHM_VERSION

    def __post_init__(self) -> None:
        levels = tuple(
            item if isinstance(item, EventLevel) else EventLevel(item)
            for item in self.levels
        )
        if not levels or len(levels) != len(set(levels)):
            raise ValueError("levels must be non-empty and unique")
        object.__setattr__(self, "levels", levels)
        for field_name in (
            "min_scope_messages",
            "min_event_size",
            "keyword_limit",
            "representative_limit",
            "max_events_per_scope",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")
        if self.min_scope_messages < self.min_event_size:
            raise ValueError("min_scope_messages must be >= min_event_size")
        if type(self.random_seed) is not int or self.random_seed < 0:
            raise ValueError("random_seed must be a non-negative integer")
        for field_name in ("allow_single_cluster", "include_noise"):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"{field_name} must be boolean")
        object.__setattr__(
            self, "schema_version", _required(self.schema_version, "schema_version")
        )
        object.__setattr__(
            self,
            "algorithm_version",
            _required(self.algorithm_version, "algorithm_version"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "levels": [item.value for item in self.levels],
            "min_scope_messages": self.min_scope_messages,
            "min_event_size": self.min_event_size,
            "allow_single_cluster": self.allow_single_cluster,
            "include_noise": self.include_noise,
            "random_seed": self.random_seed,
            "keyword_limit": self.keyword_limit,
            "representative_limit": self.representative_limit,
            "max_events_per_scope": self.max_events_per_scope,
            "schema_version": self.schema_version,
            "algorithm_version": self.algorithm_version,
        }

    @property
    def digest(self) -> str:
        return digest_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class EventMessage:
    message_key: str
    model_text: str
    published_at_utc: str | None = None
    category: str | None = None
    topic: str | None = None
    point: Mapping[str, Any] | None = None
    scopes: Mapping[str, str] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_key", _required(self.message_key, "message_key"))
        object.__setattr__(self, "model_text", _required(self.model_text, "model_text"))
        for field_name in ("published_at_utc", "category", "topic"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _required(value, field_name))
        if self.point is not None:
            object.__setattr__(self, "point", _freeze(self.point, "point"))
        scopes: dict[str, str] = {}
        for key, value in self.scopes.items():
            level = EventLevel(key).value
            scopes[level] = _required(value, f"scopes.{level}")
        object.__setattr__(self, "scopes", MappingProxyType(scopes))
        object.__setattr__(self, "provenance", _freeze(self.provenance, "provenance"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_key": self.message_key,
            "model_text": self.model_text,
            "published_at_utc": self.published_at_utc,
            "category": self.category,
            "topic": self.topic,
            "point": _thaw(self.point),
            "scopes": dict(self.scopes),
            "provenance": _thaw(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class EventCluster:
    event_id: str
    level: EventLevel
    object_id: str
    message_ids: tuple[str, ...]
    category: str | None
    topic: str | None
    keywords: tuple[str, ...]
    representative_message_ids: tuple[str, ...]
    started_at: str | None
    ended_at: str | None
    explanation: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _required(self.event_id, "event_id"))
        if not isinstance(self.level, EventLevel):
            object.__setattr__(self, "level", EventLevel(self.level))
        object.__setattr__(self, "object_id", _required(self.object_id, "object_id"))
        message_ids = tuple(_required(item, "message_ids[]") for item in self.message_ids)
        if not message_ids or message_ids != tuple(sorted(set(message_ids))):
            raise ValueError("message_ids must be sorted, unique and non-empty")
        object.__setattr__(self, "message_ids", message_ids)
        for field_name in ("category", "topic", "started_at", "ended_at"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _required(value, field_name))
        keywords = tuple(_required(item, "keywords[]") for item in self.keywords)
        representatives = tuple(
            _required(item, "representative_message_ids[]")
            for item in self.representative_message_ids
        )
        if len(keywords) != len(set(keywords)):
            raise ValueError("keywords must be unique")
        if any(item not in set(message_ids) for item in representatives):
            raise ValueError("representatives must belong to message_ids")
        object.__setattr__(self, "keywords", keywords)
        object.__setattr__(self, "representative_message_ids", representatives)
        object.__setattr__(self, "explanation", _freeze(self.explanation, "explanation"))

    @property
    def size(self) -> int:
        return len(self.message_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "level": self.level.value,
            "object_id": self.object_id,
            "message_ids": list(self.message_ids),
            "size": self.size,
            "category": self.category,
            "topic": self.topic,
            "keywords": list(self.keywords),
            "representative_message_ids": list(self.representative_message_ids),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "explanation": _thaw(self.explanation),
        }


@dataclass(frozen=True, slots=True)
class ScopeDiagnostic:
    level: EventLevel
    object_id: str
    message_count: int
    status: ScopeStatus
    cluster_count: int
    noise_count: int
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.level, EventLevel):
            object.__setattr__(self, "level", EventLevel(self.level))
        object.__setattr__(self, "object_id", _required(self.object_id, "object_id"))
        if not isinstance(self.status, ScopeStatus):
            object.__setattr__(self, "status", ScopeStatus(self.status))
        for field_name in ("message_count", "cluster_count", "noise_count"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        reasons = tuple(_required(item, "reasons[]") for item in self.reasons)
        if not reasons:
            raise ValueError("diagnostic reasons must not be empty")
        object.__setattr__(self, "reasons", reasons)

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "object_id": self.object_id,
            "message_count": self.message_count,
            "status": self.status.value,
            "cluster_count": self.cluster_count,
            "noise_count": self.noise_count,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class EventBatchStats:
    received: int
    eligible: int
    events: int
    event_memberships: int
    clustered_scopes: int
    insufficient_scopes: int
    no_cluster_scopes: int
    unavailable_scopes: int

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ValueError(f"stats.{field_name} must be non-negative")
        if self.eligible > self.received:
            raise ValueError("eligible cannot exceed received")

    def to_dict(self) -> dict[str, int]:
        return {
            field_name: getattr(self, field_name)
            for field_name in self.__dataclass_fields__
        }


@dataclass(frozen=True, slots=True)
class EventBatchResult:
    events: tuple[EventCluster, ...]
    diagnostics: tuple[ScopeDiagnostic, ...]
    stats: EventBatchStats
    input_digest: str
    output_digest: str
    config_digest: str
    component_provenance: Mapping[str, Any]
    schema_version: str = SCHEMA_VERSION
    algorithm_version: str = ALGORITHM_VERSION

    def __post_init__(self) -> None:
        events = tuple(self.events)
        diagnostics = tuple(self.diagnostics)
        if not all(isinstance(item, EventCluster) for item in events):
            raise ValueError("events must contain EventCluster values")
        if not all(isinstance(item, ScopeDiagnostic) for item in diagnostics):
            raise ValueError("diagnostics must contain ScopeDiagnostic values")
        event_ids = [item.event_id for item in events]
        if event_ids != sorted(event_ids) or len(event_ids) != len(set(event_ids)):
            raise ValueError("events must be sorted by unique event_id")
        object.__setattr__(self, "events", events)
        object.__setattr__(self, "diagnostics", diagnostics)
        if not isinstance(self.stats, EventBatchStats):
            raise ValueError("stats must be EventBatchStats")
        for field_name in ("input_digest", "output_digest", "config_digest"):
            object.__setattr__(
                self,
                field_name,
                _sha256(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "component_provenance",
            _freeze(self.component_provenance, "component_provenance"),
        )
        object.__setattr__(
            self, "schema_version", _required(self.schema_version, "schema_version")
        )
        object.__setattr__(
            self,
            "algorithm_version",
            _required(self.algorithm_version, "algorithm_version"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "algorithm_version": self.algorithm_version,
            "events": [item.to_dict() for item in self.events],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "stats": self.stats.to_dict(),
            "input_digest": self.input_digest,
            "output_digest": self.output_digest,
            "config_digest": self.config_digest,
            "component_provenance": _thaw(self.component_provenance),
        }
