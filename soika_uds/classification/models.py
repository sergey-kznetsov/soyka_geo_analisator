"""Immutable contracts for production classification and topic refinement."""

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

_COMMIT_RE = re.compile(r"^[a-f0-9]{40}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_MODEL_ROLES = {"category", "topic"}


def _required(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _score(value: float, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be numeric")
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be finite")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{field_name} must be in [0, 1]")
    return result


def _commit_sha(value: object, field_name: str) -> str:
    normalized = _required(value, field_name).lower()
    if _COMMIT_RE.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} must be an immutable 40-character commit SHA")
    return normalized


def _sha256(value: object, field_name: str) -> str:
    normalized = _required(value, field_name).lower()
    if _SHA256_RE.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} must be SHA-256")
    return normalized


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


def _freeze_json(value: object, field_name: str = "value") -> Any:
    normalized = _json_value(value, field_name)
    if isinstance(normalized, dict):
        return MappingProxyType(
            {
                key: _freeze_json(item, f"{field_name}.{key}")
                for key, item in normalized.items()
            }
        )
    if isinstance(normalized, list):
        return tuple(
            _freeze_json(item, f"{field_name}[{index}]")
            for index, item in enumerate(normalized)
        )
    return normalized


def _thaw_json(value: object) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


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
    schema_version: str = "1.1.0"
    algorithm_version: str = "1.1.0"

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
        if self.high_confidence_threshold < max(
            self.category_threshold,
            self.topic_threshold,
        ):
            raise ValueError(
                "high_confidence_threshold must not be below classification thresholds"
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
    weights_sha256: str
    approved_for_production: bool
    training_data_review: str
    label_space: tuple[str, ...]
    label_map: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "name",
            "repo_id",
            "license",
            "task",
            "tokenizer_id",
            "training_data_review",
        ):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), field_name),
            )
        if self.task not in _MODEL_ROLES:
            raise ValueError(f"unsupported model task: {self.task}")
        object.__setattr__(
            self,
            "revision",
            _commit_sha(self.revision, f"model {self.name} revision"),
        )
        object.__setattr__(
            self,
            "tokenizer_revision",
            _commit_sha(
                self.tokenizer_revision,
                f"model {self.name} tokenizer revision",
            ),
        )
        object.__setattr__(
            self,
            "weights_sha256",
            _sha256(self.weights_sha256, f"model {self.name} weights_sha256"),
        )
        if not isinstance(self.approved_for_production, bool):
            raise TypeError("approved_for_production must be boolean")
        labels = tuple(_required(item, "label_space[]") for item in self.label_space)
        if not labels:
            raise ValueError(f"model {self.name} label_space must not be empty")
        if len(labels) != len(set(labels)):
            raise ValueError(f"model {self.name} label_space must be unique")
        object.__setattr__(self, "label_space", labels)
        mapping = {
            _required(key, "label_map key"): _required(value, "label_map value")
            for key, value in self.label_map.items()
        }
        unknown_targets = sorted(set(mapping.values()) - set(labels))
        if unknown_targets:
            raise ValueError(
                f"model {self.name} label_map targets are outside label_space: "
                + ", ".join(unknown_targets)
            )
        object.__setattr__(self, "label_map", MappingProxyType(mapping))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "repo_id": self.repo_id,
            "revision": self.revision,
            "license": self.license,
            "task": self.task,
            "tokenizer_id": self.tokenizer_id,
            "tokenizer_revision": self.tokenizer_revision,
            "weights_sha256": self.weights_sha256,
            "approved_for_production": self.approved_for_production,
            "training_data_review": self.training_data_review,
            "label_space": list(self.label_space),
            "label_map": dict(self.label_map),
        }

    def artifact_dict(self, role: str) -> dict[str, Any]:
        return {
            "role": role,
            "repo_id": self.repo_id,
            "revision": self.revision,
            "weights_sha256": self.weights_sha256,
            "tokenizer_id": self.tokenizer_id,
            "tokenizer_revision": self.tokenizer_revision,
            "task": self.task,
            "label_space": list(self.label_space),
            "label_map": dict(sorted(self.label_map.items())),
        }


def classification_model_registry_digest(
    models: Mapping[str, ModelDescriptor],
    topic_hierarchy: Mapping[str, Sequence[str]],
) -> str:
    payload = {
        "schema_version": 2,
        "models": {
            role: descriptor.artifact_dict(role)
            for role, descriptor in sorted(models.items())
        },
        "topic_hierarchy": {
            category: sorted(str(topic) for topic in topics)
            for category, topics in sorted(topic_hierarchy.items())
        },
    }
    return digest_json(payload)


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
            _freeze_json(self.model_provenance, "model_provenance"),
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
            "model_provenance": _thaw_json(self.model_provenance),
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
        if self.low_confidence > self.classified:
            raise ValueError("low_confidence must not exceed classified")

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
            object.__setattr__(
                self,
                field_name,
                _sha256(getattr(self, field_name), field_name),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": [item.to_dict() for item in self.results],
            "stats": self.stats.to_dict(),
            "config_digest": self.config_digest,
            "output_digest": self.output_digest,
        }


__all__ = [
    "ClassificationBatchResult",
    "ClassificationConfig",
    "ClassificationStats",
    "ConfidenceBand",
    "ExecutionDevice",
    "LabelPrediction",
    "MessageClassificationResult",
    "ModelDescriptor",
    "classification_model_registry_digest",
    "digest_json",
]
