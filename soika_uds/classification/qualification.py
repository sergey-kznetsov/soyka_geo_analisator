"""Fail-closed release qualification for concrete classification weights."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .models import ExecutionDevice, digest_json

_COMMIT_RE = re.compile(r"^[a-f0-9]{40}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_ALLOWED_ROLES = {"category", "topic"}


def _required(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _required(value, field_name)


def _finite(value: object, field_name: str, *, minimum: float = 0.0) -> float:
    if not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{field_name} must be finite")
    result = float(value)
    if result < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}")
    return result


def _probability(value: object, field_name: str) -> float:
    result = _finite(value, field_name)
    if result > 1.0:
        raise ValueError(f"{field_name} must be in [0, 1]")
    return result


def _positive_int(value: object, field_name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _sha256(value: object, field_name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    normalized = _required(value, field_name).lower()
    if _SHA256_RE.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} must be a SHA-256 digest")
    return normalized


def _commit(value: object, field_name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    normalized = _required(value, field_name).lower()
    if _COMMIT_RE.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} must be an immutable 40-character commit SHA")
    return normalized


def _strict_fields(
    payload: Mapping[str, Any],
    allowed: set[str],
    field_name: str,
) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"{field_name} has unknown fields: {', '.join(unknown)}")


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        raise ValueError(f"{field_name} must be an array")
    return tuple(_required(item, f"{field_name}[]") for item in value)


def _count_mapping(value: object, field_name: str) -> Mapping[str, int]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    result: dict[str, int] = {}
    for raw_label, raw_count in value.items():
        label = _required(raw_label, f"{field_name} label")
        result[label] = _positive_int(raw_count, f"{field_name}.{label}")
    return MappingProxyType(dict(sorted(result.items())))


class GateState(str, Enum):
    PASSED = "passed"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class GateResult:
    code: str
    state: GateState
    detail: str
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _required(self.code, "gate.code"))
        if not isinstance(self.state, GateState):
            object.__setattr__(self, "state", GateState(self.state))
        object.__setattr__(self, "detail", _required(self.detail, "gate.detail"))
        object.__setattr__(
            self,
            "evidence",
            tuple(_required(item, "gate.evidence[]") for item in self.evidence),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "state": self.state.value,
            "detail": self.detail,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class ModelAuditRecord:
    role: str
    repo_id: str
    repository_exists: bool
    requested_revision: str
    resolved_revision: str | None
    license_id: str | None
    license_reviewed: bool
    training_data_documented: bool
    training_data_reviewed: bool
    intended_use_documented: bool
    weights_format: str | None
    weights_sha256: str | None
    tokenizer_id: str
    tokenizer_revision: str | None
    evidence_urls: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _required(self.role, "model.role"))
        if self.role not in _ALLOWED_ROLES:
            raise ValueError(f"unsupported model role: {self.role}")
        object.__setattr__(self, "repo_id", _required(self.repo_id, "model.repo_id"))
        if not isinstance(self.repository_exists, bool):
            raise TypeError("model.repository_exists must be boolean")
        object.__setattr__(
            self,
            "requested_revision",
            _required(self.requested_revision, "model.requested_revision"),
        )
        object.__setattr__(
            self,
            "resolved_revision",
            _commit(
                self.resolved_revision,
                "model.resolved_revision",
                optional=True,
            ),
        )
        object.__setattr__(
            self,
            "license_id",
            _optional(self.license_id, "model.license_id"),
        )
        for field_name in (
            "license_reviewed",
            "training_data_documented",
            "training_data_reviewed",
            "intended_use_documented",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"model.{field_name} must be boolean")
        object.__setattr__(
            self,
            "weights_format",
            _optional(self.weights_format, "model.weights_format"),
        )
        object.__setattr__(
            self,
            "weights_sha256",
            _sha256(self.weights_sha256, "model.weights_sha256", optional=True),
        )
        object.__setattr__(
            self,
            "tokenizer_id",
            _required(self.tokenizer_id, "model.tokenizer_id"),
        )
        object.__setattr__(
            self,
            "tokenizer_revision",
            _commit(
                self.tokenizer_revision,
                "model.tokenizer_revision",
                optional=True,
            ),
        )
        object.__setattr__(
            self,
            "evidence_urls",
            _string_tuple(self.evidence_urls, "model.evidence_urls"),
        )
        object.__setattr__(
            self,
            "notes",
            _string_tuple(self.notes, "model.notes"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "repo_id": self.repo_id,
            "repository_exists": self.repository_exists,
            "requested_revision": self.requested_revision,
            "resolved_revision": self.resolved_revision,
            "license_id": self.license_id,
            "license_reviewed": self.license_reviewed,
            "training_data_documented": self.training_data_documented,
            "training_data_reviewed": self.training_data_reviewed,
            "intended_use_documented": self.intended_use_documented,
            "weights_format": self.weights_format,
            "weights_sha256": self.weights_sha256,
            "tokenizer_id": self.tokenizer_id,
            "tokenizer_revision": self.tokenizer_revision,
            "evidence_urls": list(self.evidence_urls),
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class ValidationSetEvidence:
    dataset_id: str
    version: str
    digest: str
    sample_count: int
    category_counts: Mapping[str, int]
    topic_counts: Mapping[str, int]
    annotations_per_item: int
    agreement: float
    approved: bool
    approval_reference: str | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "dataset_id",
            _required(self.dataset_id, "validation.dataset_id"),
        )
        object.__setattr__(
            self,
            "version",
            _required(self.version, "validation.version"),
        )
        object.__setattr__(self, "digest", _sha256(self.digest, "validation.digest"))
        object.__setattr__(
            self,
            "sample_count",
            _positive_int(self.sample_count, "validation.sample_count"),
        )
        object.__setattr__(
            self,
            "category_counts",
            _count_mapping(self.category_counts, "validation.category_counts"),
        )
        object.__setattr__(
            self,
            "topic_counts",
            _count_mapping(self.topic_counts, "validation.topic_counts"),
        )
        if sum(self.category_counts.values()) != self.sample_count:
            raise ValueError("validation category counts must equal sample_count")
        if sum(self.topic_counts.values()) != self.sample_count:
            raise ValueError("validation topic counts must equal sample_count")
        object.__setattr__(
            self,
            "annotations_per_item",
            _positive_int(
                self.annotations_per_item,
                "validation.annotations_per_item",
            ),
        )
        object.__setattr__(
            self,
            "agreement",
            _probability(self.agreement, "validation.agreement"),
        )
        if not isinstance(self.approved, bool):
            raise TypeError("validation.approved must be boolean")
        object.__setattr__(
            self,
            "approval_reference",
            _optional(
                self.approval_reference,
                "validation.approval_reference",
            ),
        )
        if self.approved and self.approval_reference is None:
            raise ValueError("approved validation set requires approval_reference")

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "version": self.version,
            "digest": self.digest,
            "sample_count": self.sample_count,
            "category_counts": dict(self.category_counts),
            "topic_counts": dict(self.topic_counts),
            "annotations_per_item": self.annotations_per_item,
            "agreement": self.agreement,
            "approved": self.approved,
            "approval_reference": self.approval_reference,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkEvidence:
    device: ExecutionDevice
    completed: bool
    samples: int
    batch_size: int
    repeat_count: int
    duration_seconds: float
    throughput_per_second: float
    peak_memory_mb: float
    model_registry_digest: str
    validation_digest: str
    output_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.device, ExecutionDevice):
            object.__setattr__(self, "device", ExecutionDevice(self.device))
        if not isinstance(self.completed, bool):
            raise TypeError("benchmark.completed must be boolean")
        for field_name in ("samples", "batch_size", "repeat_count"):
            object.__setattr__(
                self,
                field_name,
                _positive_int(getattr(self, field_name), f"benchmark.{field_name}"),
            )
        for field_name in (
            "duration_seconds",
            "throughput_per_second",
            "peak_memory_mb",
        ):
            object.__setattr__(
                self,
                field_name,
                _finite(getattr(self, field_name), f"benchmark.{field_name}"),
            )
        for field_name in (
            "model_registry_digest",
            "validation_digest",
            "output_digest",
        ):
            object.__setattr__(
                self,
                field_name,
                _sha256(getattr(self, field_name), f"benchmark.{field_name}"),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "device": self.device.value,
            "completed": self.completed,
            "samples": self.samples,
            "batch_size": self.batch_size,
            "repeat_count": self.repeat_count,
            "duration_seconds": self.duration_seconds,
            "throughput_per_second": self.throughput_per_second,
            "peak_memory_mb": self.peak_memory_mb,
            "model_registry_digest": self.model_registry_digest,
            "validation_digest": self.validation_digest,
            "output_digest": self.output_digest,
        }


@dataclass(frozen=True, slots=True)
class QualityEvidence:
    report_digest: str
    samples: int
    category_macro_f1: float
    category_macro_recall: float
    topic_macro_f1: float
    topic_macro_recall: float
    low_confidence_rate: float
    category_ece: float
    topic_ece: float
    drift_tvd: float
    calibration_digest: str | None
    baseline_digest: str | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "report_digest",
            _sha256(self.report_digest, "quality.report_digest"),
        )
        object.__setattr__(
            self,
            "samples",
            _positive_int(self.samples, "quality.samples"),
        )
        for field_name in (
            "category_macro_f1",
            "category_macro_recall",
            "topic_macro_f1",
            "topic_macro_recall",
            "low_confidence_rate",
            "category_ece",
            "topic_ece",
            "drift_tvd",
        ):
            object.__setattr__(
                self,
                field_name,
                _probability(getattr(self, field_name), f"quality.{field_name}"),
            )
        object.__setattr__(
            self,
            "calibration_digest",
            _sha256(
                self.calibration_digest,
                "quality.calibration_digest",
                optional=True,
            ),
        )
        object.__setattr__(
            self,
            "baseline_digest",
            _sha256(
                self.baseline_digest,
                "quality.baseline_digest",
                optional=True,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_digest": self.report_digest,
            "samples": self.samples,
            "category_macro_f1": self.category_macro_f1,
            "category_macro_recall": self.category_macro_recall,
            "topic_macro_f1": self.topic_macro_f1,
            "topic_macro_recall": self.topic_macro_recall,
            "low_confidence_rate": self.low_confidence_rate,
            "category_ece": self.category_ece,
            "topic_ece": self.topic_ece,
            "drift_tvd": self.drift_tvd,
            "calibration_digest": self.calibration_digest,
            "baseline_digest": self.baseline_digest,
        }


@dataclass(frozen=True, slots=True)
class QualificationPolicy:
    min_validation_samples: int
    min_samples_per_category: int
    min_samples_per_topic: int
    min_annotations_per_item: int
    min_annotation_agreement: float
    min_category_macro_f1: float
    min_category_macro_recall: float
    min_topic_macro_f1: float
    min_topic_macro_recall: float
    max_low_confidence_rate: float
    max_expected_calibration_error: float
    max_drift_tvd: float
    min_benchmark_repeats: int
    require_gpu: bool = True
    allowed_weight_formats: tuple[str, ...] = ("safetensors",)

    def __post_init__(self) -> None:
        for field_name in (
            "min_validation_samples",
            "min_samples_per_category",
            "min_samples_per_topic",
            "min_annotations_per_item",
            "min_benchmark_repeats",
        ):
            object.__setattr__(
                self,
                field_name,
                _positive_int(getattr(self, field_name), f"policy.{field_name}"),
            )
        for field_name in (
            "min_annotation_agreement",
            "min_category_macro_f1",
            "min_category_macro_recall",
            "min_topic_macro_f1",
            "min_topic_macro_recall",
            "max_low_confidence_rate",
            "max_expected_calibration_error",
            "max_drift_tvd",
        ):
            object.__setattr__(
                self,
                field_name,
                _probability(getattr(self, field_name), f"policy.{field_name}"),
            )
        if not isinstance(self.require_gpu, bool):
            raise TypeError("policy.require_gpu must be boolean")
        formats = tuple(
            _required(item, "policy.allowed_weight_formats[]").casefold()
            for item in self.allowed_weight_formats
        )
        if len(formats) != len(set(formats)):
            raise ValueError("policy.allowed_weight_formats must be unique")
        object.__setattr__(self, "allowed_weight_formats", formats)

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_validation_samples": self.min_validation_samples,
            "min_samples_per_category": self.min_samples_per_category,
            "min_samples_per_topic": self.min_samples_per_topic,
            "min_annotations_per_item": self.min_annotations_per_item,
            "min_annotation_agreement": self.min_annotation_agreement,
            "min_category_macro_f1": self.min_category_macro_f1,
            "min_category_macro_recall": self.min_category_macro_recall,
            "min_topic_macro_f1": self.min_topic_macro_f1,
            "min_topic_macro_recall": self.min_topic_macro_recall,
            "max_low_confidence_rate": self.max_low_confidence_rate,
            "max_expected_calibration_error": (
                self.max_expected_calibration_error
            ),
            "max_drift_tvd": self.max_drift_tvd,
            "min_benchmark_repeats": self.min_benchmark_repeats,
            "require_gpu": self.require_gpu,
            "allowed_weight_formats": list(self.allowed_weight_formats),
        }


@dataclass(frozen=True, slots=True)
class QualificationInput:
    models: tuple[ModelAuditRecord, ...]
    policy: QualificationPolicy
    validation: ValidationSetEvidence | None = None
    benchmarks: tuple[BenchmarkEvidence, ...] = ()
    quality: QualityEvidence | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "models", tuple(self.models))
        object.__setattr__(self, "benchmarks", tuple(self.benchmarks))
        roles = [model.role for model in self.models]
        if len(roles) != len(set(roles)):
            raise ValueError("qualification models must have unique roles")
        devices = [benchmark.device for benchmark in self.benchmarks]
        if len(devices) != len(set(devices)):
            raise ValueError("qualification benchmarks must have unique devices")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "models": [model.to_dict() for model in self.models],
            "validation": self.validation.to_dict() if self.validation else None,
            "benchmarks": [item.to_dict() for item in self.benchmarks],
            "quality": self.quality.to_dict() if self.quality else None,
            "policy": self.policy.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class QualificationReport:
    approved_for_production: bool
    gates: tuple[GateResult, ...]
    input_digest: str
    report_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.approved_for_production, bool):
            raise TypeError("approved_for_production must be boolean")
        object.__setattr__(self, "gates", tuple(self.gates))
        object.__setattr__(
            self,
            "input_digest",
            _sha256(self.input_digest, "input_digest"),
        )
        object.__setattr__(
            self,
            "report_digest",
            _sha256(self.report_digest, "report_digest"),
        )

    @property
    def blockers(self) -> tuple[str, ...]:
        return tuple(
            gate.code for gate in self.gates if gate.state is GateState.BLOCKED
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved_for_production": self.approved_for_production,
            "gates": [gate.to_dict() for gate in self.gates],
            "blockers": list(self.blockers),
            "input_digest": self.input_digest,
            "report_digest": self.report_digest,
        }


def _gate(
    code: str,
    passed: bool,
    passed_detail: str,
    blocked_detail: str,
    evidence: Sequence[str] = (),
) -> GateResult:
    return GateResult(
        code=code,
        state=GateState.PASSED if passed else GateState.BLOCKED,
        detail=passed_detail if passed else blocked_detail,
        evidence=tuple(item for item in evidence if item),
    )


def _minimum_count(values: Mapping[str, int]) -> int:
    return min(values.values()) if values else 0


def qualify_release(inputs: QualificationInput) -> QualificationReport:
    gates: list[GateResult] = []
    models = {model.role: model for model in inputs.models}

    for role in sorted(_ALLOWED_ROLES):
        model = models.get(role)
        gates.append(
            _gate(
                f"model.{role}.present",
                model is not None,
                f"{role} model audit is present",
                f"{role} model audit is missing",
            )
        )
        if model is None:
            continue
        evidence = model.evidence_urls
        gates.extend(
            [
                _gate(
                    f"model.{role}.repository",
                    model.repository_exists,
                    f"{model.repo_id} exists",
                    f"{model.repo_id} was not confirmed",
                    evidence,
                ),
                _gate(
                    f"model.{role}.revision",
                    model.resolved_revision is not None,
                    "immutable model revision is recorded",
                    "immutable model revision is missing",
                    evidence,
                ),
                _gate(
                    f"model.{role}.license",
                    model.license_reviewed
                    and model.license_id is not None
                    and model.license_id.casefold() not in {"unknown", "none"},
                    f"license {model.license_id} was reviewed",
                    "license is unknown or not reviewed",
                    evidence,
                ),
                _gate(
                    f"model.{role}.training_data",
                    model.training_data_documented
                    and model.training_data_reviewed,
                    "training data origin was documented and reviewed",
                    "training data origin is missing or not reviewed",
                    evidence,
                ),
                _gate(
                    f"model.{role}.intended_use",
                    model.intended_use_documented,
                    "intended use and limitations are documented",
                    "intended use or limitations are not documented",
                    evidence,
                ),
                _gate(
                    f"model.{role}.weights_format",
                    model.weights_format is not None
                    and model.weights_format.casefold()
                    in inputs.policy.allowed_weight_formats,
                    f"weights format {model.weights_format} is allowed",
                    "weights format is missing or not allowed",
                    evidence,
                ),
                _gate(
                    f"model.{role}.weights_digest",
                    model.weights_sha256 is not None,
                    "weights SHA-256 is recorded",
                    "weights SHA-256 is missing",
                    evidence,
                ),
                _gate(
                    f"model.{role}.tokenizer_revision",
                    model.tokenizer_revision is not None,
                    "immutable tokenizer revision is recorded",
                    "immutable tokenizer revision is missing",
                    evidence,
                ),
                _gate(
                    f"model.{role}.evidence",
                    bool(model.evidence_urls),
                    "source evidence is recorded",
                    "source evidence is missing",
                    evidence,
                ),
            ]
        )

    validation = inputs.validation
    gates.append(
        _gate(
            "validation.present",
            validation is not None,
            "validation set manifest is present",
            "validation set manifest is missing",
        )
    )
    if validation is not None:
        gates.extend(
            [
                _gate(
                    "validation.approved",
                    validation.approved,
                    "validation set is approved",
                    "validation set is not approved",
                    (validation.approval_reference or "",),
                ),
                _gate(
                    "validation.samples",
                    validation.sample_count
                    >= inputs.policy.min_validation_samples,
                    "validation sample count meets policy",
                    "validation sample count is below policy",
                ),
                _gate(
                    "validation.category_coverage",
                    _minimum_count(validation.category_counts)
                    >= inputs.policy.min_samples_per_category,
                    "category coverage meets policy",
                    "at least one category is underrepresented",
                ),
                _gate(
                    "validation.topic_coverage",
                    _minimum_count(validation.topic_counts)
                    >= inputs.policy.min_samples_per_topic,
                    "topic coverage meets policy",
                    "at least one topic is underrepresented",
                ),
                _gate(
                    "validation.annotation_depth",
                    validation.annotations_per_item
                    >= inputs.policy.min_annotations_per_item,
                    "annotation depth meets policy",
                    "annotation depth is below policy",
                ),
                _gate(
                    "validation.agreement",
                    validation.agreement
                    >= inputs.policy.min_annotation_agreement,
                    "annotator agreement meets policy",
                    "annotator agreement is below policy",
                ),
            ]
        )

    benchmarks = {item.device: item for item in inputs.benchmarks}
    required_devices = [ExecutionDevice.CPU]
    if inputs.policy.require_gpu:
        required_devices.append(ExecutionDevice.GPU)
    for device in required_devices:
        benchmark = benchmarks.get(device)
        gates.append(
            _gate(
                f"benchmark.{device.value}.present",
                benchmark is not None,
                f"{device.value} benchmark is present",
                f"{device.value} benchmark is missing",
            )
        )
        if benchmark is None:
            continue
        validation_matches = (
            validation is not None
            and benchmark.validation_digest == validation.digest
            and benchmark.samples == validation.sample_count
        )
        gates.extend(
            [
                _gate(
                    f"benchmark.{device.value}.completed",
                    benchmark.completed,
                    f"{device.value} benchmark completed",
                    f"{device.value} benchmark did not complete",
                ),
                _gate(
                    f"benchmark.{device.value}.repeats",
                    benchmark.repeat_count
                    >= inputs.policy.min_benchmark_repeats,
                    f"{device.value} repeat count meets policy",
                    f"{device.value} repeat count is below policy",
                ),
                _gate(
                    f"benchmark.{device.value}.validation",
                    validation_matches,
                    f"{device.value} benchmark used the approved validation set",
                    f"{device.value} benchmark validation evidence differs",
                ),
            ]
        )

    cpu = benchmarks.get(ExecutionDevice.CPU)
    gpu = benchmarks.get(ExecutionDevice.GPU)
    if inputs.policy.require_gpu:
        outputs_match = (
            cpu is not None
            and gpu is not None
            and cpu.output_digest == gpu.output_digest
            and cpu.model_registry_digest == gpu.model_registry_digest
        )
        gates.append(
            _gate(
                "benchmark.cpu_gpu_equivalence",
                outputs_match,
                "CPU and GPU produced equivalent deterministic outputs",
                "CPU and GPU outputs or model registries differ",
            )
        )

    quality = inputs.quality
    gates.append(
        _gate(
            "quality.present",
            quality is not None,
            "quality report is present",
            "quality report is missing",
        )
    )
    if quality is not None:
        sample_match = validation is not None and quality.samples == validation.sample_count
        gates.extend(
            [
                _gate(
                    "quality.samples",
                    sample_match,
                    "quality report covers the approved validation set",
                    "quality report sample count differs from validation",
                ),
                _gate(
                    "quality.category_macro_f1",
                    quality.category_macro_f1
                    >= inputs.policy.min_category_macro_f1,
                    "category macro-F1 meets policy",
                    "category macro-F1 is below policy",
                ),
                _gate(
                    "quality.category_macro_recall",
                    quality.category_macro_recall
                    >= inputs.policy.min_category_macro_recall,
                    "category macro-recall meets policy",
                    "category macro-recall is below policy",
                ),
                _gate(
                    "quality.topic_macro_f1",
                    quality.topic_macro_f1 >= inputs.policy.min_topic_macro_f1,
                    "topic macro-F1 meets policy",
                    "topic macro-F1 is below policy",
                ),
                _gate(
                    "quality.topic_macro_recall",
                    quality.topic_macro_recall
                    >= inputs.policy.min_topic_macro_recall,
                    "topic macro-recall meets policy",
                    "topic macro-recall is below policy",
                ),
                _gate(
                    "quality.low_confidence_rate",
                    quality.low_confidence_rate
                    <= inputs.policy.max_low_confidence_rate,
                    "low-confidence rate meets policy",
                    "low-confidence rate exceeds policy",
                ),
                _gate(
                    "quality.category_calibration",
                    quality.category_ece
                    <= inputs.policy.max_expected_calibration_error,
                    "category calibration meets policy",
                    "category calibration error exceeds policy",
                ),
                _gate(
                    "quality.topic_calibration",
                    quality.topic_ece
                    <= inputs.policy.max_expected_calibration_error,
                    "topic calibration meets policy",
                    "topic calibration error exceeds policy",
                ),
                _gate(
                    "quality.calibration_evidence",
                    quality.calibration_digest is not None,
                    "calibration evidence digest is recorded",
                    "calibration evidence digest is missing",
                ),
                _gate(
                    "quality.drift",
                    quality.drift_tvd <= inputs.policy.max_drift_tvd,
                    "drift is within policy",
                    "drift exceeds policy",
                ),
                _gate(
                    "quality.baseline",
                    quality.baseline_digest is not None,
                    "drift baseline digest is recorded",
                    "drift baseline digest is missing",
                ),
            ]
        )

    input_digest = digest_json(inputs.to_dict())
    approved = all(gate.state is GateState.PASSED for gate in gates)
    report_payload = {
        "approved_for_production": approved,
        "gates": [gate.to_dict() for gate in gates],
        "input_digest": input_digest,
    }
    return QualificationReport(
        approved_for_production=approved,
        gates=tuple(gates),
        input_digest=input_digest,
        report_digest=digest_json(report_payload),
    )


def _model_from_dict(payload: Mapping[str, Any]) -> ModelAuditRecord:
    allowed = {
        "role",
        "repo_id",
        "repository_exists",
        "requested_revision",
        "resolved_revision",
        "license_id",
        "license_reviewed",
        "training_data_documented",
        "training_data_reviewed",
        "intended_use_documented",
        "weights_format",
        "weights_sha256",
        "tokenizer_id",
        "tokenizer_revision",
        "evidence_urls",
        "notes",
    }
    _strict_fields(payload, allowed, "model")
    return ModelAuditRecord(**payload)


def _validation_from_dict(payload: Mapping[str, Any]) -> ValidationSetEvidence:
    allowed = {
        "dataset_id",
        "version",
        "digest",
        "sample_count",
        "category_counts",
        "topic_counts",
        "annotations_per_item",
        "agreement",
        "approved",
        "approval_reference",
    }
    _strict_fields(payload, allowed, "validation")
    return ValidationSetEvidence(**payload)


def _benchmark_from_dict(payload: Mapping[str, Any]) -> BenchmarkEvidence:
    allowed = {
        "device",
        "completed",
        "samples",
        "batch_size",
        "repeat_count",
        "duration_seconds",
        "throughput_per_second",
        "peak_memory_mb",
        "model_registry_digest",
        "validation_digest",
        "output_digest",
    }
    _strict_fields(payload, allowed, "benchmark")
    return BenchmarkEvidence(**payload)


def _quality_from_dict(payload: Mapping[str, Any]) -> QualityEvidence:
    allowed = {
        "report_digest",
        "samples",
        "category_macro_f1",
        "category_macro_recall",
        "topic_macro_f1",
        "topic_macro_recall",
        "low_confidence_rate",
        "category_ece",
        "topic_ece",
        "drift_tvd",
        "calibration_digest",
        "baseline_digest",
    }
    _strict_fields(payload, allowed, "quality")
    return QualityEvidence(**payload)


def _policy_from_dict(payload: Mapping[str, Any]) -> QualificationPolicy:
    allowed = {
        "min_validation_samples",
        "min_samples_per_category",
        "min_samples_per_topic",
        "min_annotations_per_item",
        "min_annotation_agreement",
        "min_category_macro_f1",
        "min_category_macro_recall",
        "min_topic_macro_f1",
        "min_topic_macro_recall",
        "max_low_confidence_rate",
        "max_expected_calibration_error",
        "max_drift_tvd",
        "min_benchmark_repeats",
        "require_gpu",
        "allowed_weight_formats",
    }
    _strict_fields(payload, allowed, "policy")
    return QualificationPolicy(**payload)


def qualification_input_from_dict(payload: Mapping[str, Any]) -> QualificationInput:
    allowed = {
        "schema_version",
        "models",
        "validation",
        "benchmarks",
        "quality",
        "policy",
    }
    _strict_fields(payload, allowed, "qualification")
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported qualification schema_version")
    raw_models = payload.get("models")
    raw_benchmarks = payload.get("benchmarks", [])
    raw_policy = payload.get("policy")
    if not isinstance(raw_models, Sequence) or isinstance(raw_models, str):
        raise ValueError("qualification models must be an array")
    if not isinstance(raw_benchmarks, Sequence) or isinstance(raw_benchmarks, str):
        raise ValueError("qualification benchmarks must be an array")
    if not isinstance(raw_policy, Mapping):
        raise ValueError("qualification policy must be an object")
    raw_validation = payload.get("validation")
    raw_quality = payload.get("quality")
    if raw_validation is not None and not isinstance(raw_validation, Mapping):
        raise ValueError("qualification validation must be an object or null")
    if raw_quality is not None and not isinstance(raw_quality, Mapping):
        raise ValueError("qualification quality must be an object or null")
    return QualificationInput(
        models=tuple(
            _model_from_dict(item)
            for item in raw_models
            if isinstance(item, Mapping)
        ),
        validation=(
            _validation_from_dict(raw_validation)
            if isinstance(raw_validation, Mapping)
            else None
        ),
        benchmarks=tuple(
            _benchmark_from_dict(item)
            for item in raw_benchmarks
            if isinstance(item, Mapping)
        ),
        quality=(
            _quality_from_dict(raw_quality)
            if isinstance(raw_quality, Mapping)
            else None
        ),
        policy=_policy_from_dict(raw_policy),
    )


def load_qualification_input(path: Path) -> QualificationInput:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("qualification document must be a JSON object")
    return qualification_input_from_dict(payload)


__all__ = [
    "BenchmarkEvidence",
    "GateResult",
    "GateState",
    "ModelAuditRecord",
    "QualificationInput",
    "QualificationPolicy",
    "QualificationReport",
    "QualityEvidence",
    "ValidationSetEvidence",
    "load_qualification_input",
    "qualification_input_from_dict",
    "qualify_release",
]
