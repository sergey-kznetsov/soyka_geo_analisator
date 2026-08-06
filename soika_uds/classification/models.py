"""Immutable contracts for production classification and topic refinement."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any


def _required(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _score(value: float, field_name: str) -> float:
    if not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{field_name} must be finite")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{field_name} must be in [0, 1]")
    return result


def _json_value(value: object, field_name: str) -> Any:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item, f"{field_name}.{key}")
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [
            _json_value(item, f"{field_name}[{index}]")
            for index, item in enumerate(value)
        ]
    raise ValueError(f"{field_name} contains unsupported value")


def digest_json(value: object) -> str:
    encoded = json.dumps(
        _json_value(value, "digest"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ConfidenceBand(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ExecutionDevice(str, Enum):
    CPU = "cpu"
    GPU = "gpu"


@dataclass(frozen=True, slots=True)
class ClassificationConfig:
    category_threshold: float = 0.65
    topic_threshold: float = 0.60
    high_confidence_threshold: float = 0.85
    batch_size: int = 32
    device: ExecutionDevice = ExecutionDevice.CPU
    include_duplicates: bool = False
    include_rejected: bool = False
    schema_version: str = "1.0.0"
    algorithm_version: str = "1.0.0"

    def __post_init__(self) -> None:
        for field_name in (
            "category_threshold",
            "topic_threshold",
            "high_confidence_threshold",
        ):
            object.__setattr__(
                self,
                field_name,
                _score(getattr(self, field_name), field_name),
            )
        if self.high_confidence_threshold < self.category_threshold:
            raise ValueError(
                "high_confidence_threshold must not be below category_threshold"
            )
        if type(self.batch_size) is not int or self.batch_size < 1:
            raise ValueError("batch_size must be positive")
        if not isinstance(self.device, ExecutionDevice):
            object.__setattr__(self, "device", ExecutionDevice(self.device))
        for field_name in ("include_duplicates", "include_rejected"):
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
            "category_threshold": self.category_threshold,
            "topic_threshold": self.topic_threshold,
            "high_confidence_threshold": self.high_confidence_threshold,
            "batch_size": self.batch_size,
            "device": self.device.value,
            "include_duplicates": self.include_duplicates,
            "include_rejected": self.include_rejected,
            "schema_version": self.schema_version,
            "algorithm_version": self.algorithm_version,
        }

    @property
    def digest(self) -> str:
        return digest_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class ModelDescriptor:
    name: str
    repo_id: str
    revision: str
    license: str
    task: str
    tokenizer_id: str
    tokenizer_revision: str
    approved_for_production: bool
    training_data_review: str
    label_map: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "name",
            "repo_id",
            "revision",
            "license",
            "task",
            "tokenizer_id",
            "tokenizer_revision",
            "training_data_review",
        ):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), field_name),
            )
        if self.revision in {"main", "master"} or len(self.revision) < 7:
            raise ValueError(f"model {self.name} revision must be immutable")
        if self.tokenizer_revision in {"main", "master"} or len(
            self.tokenizer_revision
        ) < 7:
            raise ValueError(f"model {self.name} tokenizer revision must be immutable")
        if not isinstance(self.approved_for_production, bool):
            raise TypeError("approved_for_production must be boolean")
        object.__setattr__(
            self,
            "label_map",
            MappingProxyType(
                {
                    _required(key, "label_map key"): _required(value, "label_map value")
                    for key, value in self.label_map.items()
                }
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "repo_id": self.repo_id,
            "revision": self.revision,
            "license": self.license,
            "task": self.task,
            "tokenizer_id": self.tokenizer_id,
            "tokenizer_revision": self.tokenizer_revision,
            "approved_for_production": self.approved_for_production,
            "training_data_review": self.training_data_review,
            "label_map": dict(self.label_map),
        }


@dataclass(frozen=True, slots=True)
class LabelPrediction:
    label: str
    score: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "label", _required(self.label, "label"))
        object.__setattr__(self, "score", _score(self.score, "score"))

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "score": round(self.score, 6)}


@dataclass(frozen=True, slots=True)
class MessageClassificationResult:
    message_key: str
    category: LabelPrediction
    topic: LabelPrediction
    confidence_band: ConfidenceBand
    low_confidence: bool
    included_for_analysis: bool
    reasons: tuple[str, ...]
    model_provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "message_key", _required(self.message_key, "message_key")
        )
        if not isinstance(self.confidence_band, ConfidenceBand):
            object.__setattr__(
                self,
                "confidence_band",
                ConfidenceBand(self.confidence_band),
            )
        for field_name in ("low_confidence", "included_for_analysis"):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"{field_name} must be boolean")
        object.__setattr__(
            self,
            "reasons",
            tuple(_required(item, "reasons[]") for item in self.reasons),
        )
        object.__setattr__(
            self,
            "model_provenance",
            MappingProxyType(_json_value(self.model_provenance, "model_provenance")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_key": self.message_key,
            "category": self.category.to_dict(),
            "topic": self.topic.to_dict(),
            "confidence_band": self.confidence_band.value,
            "low_confidence": self.low_confidence,
            "included_for_analysis": self.included_for_analysis,
            "reasons": list(self.reasons),
            "model_provenance": dict(self.model_provenance),
        }


@dataclass(frozen=True, slots=True)
class ClassificationStats:
    received: int
    classified: int
    skipped: int
    low_confidence: int

    def __post_init__(self) -> None:
        for field_name in ("received", "classified", "skipped", "low_confidence"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{field_name} must be non-negative")
        if self.classified + self.skipped != self.received:
            raise ValueError("classified plus skipped must equal received")

    def to_dict(self) -> dict[str, int]:
        return {
            "received": self.received,
            "classified": self.classified,
            "skipped": self.skipped,
            "low_confidence": self.low_confidence,
        }


@dataclass(frozen=True, slots=True)
class ClassificationBatchResult:
    results: tuple[MessageClassificationResult, ...]
    stats: ClassificationStats
    config_digest: str
    output_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "results", tuple(self.results))
        for field_name in ("config_digest", "output_digest"):
            value = _required(getattr(self, field_name), field_name)
            if len(value) != 64:
                raise ValueError(f"{field_name} must be SHA-256")
            object.__setattr__(self, field_name, value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": [item.to_dict() for item in self.results],
            "stats": self.stats.to_dict(),
            "config_digest": self.config_digest,
            "output_digest": self.output_digest,
        }
