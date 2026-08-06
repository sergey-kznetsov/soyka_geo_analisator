"""Strict production registry for qualified category and topic models."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .models import (
    ModelDescriptor,
    classification_model_registry_digest,
    digest_json,
)

_REQUIRED_ROLES = {"category", "topic"}
_MODEL_GATE_SUFFIXES = {
    "present",
    "repository",
    "revision",
    "license",
    "training_data",
    "intended_use",
    "weights_format",
    "weights_digest",
    "tokenizer_revision",
    "label_space",
    "evidence",
}
_CORE_GATE_CODES = {
    *(f"model.{role}.{suffix}" for role in _REQUIRED_ROLES for suffix in _MODEL_GATE_SUFFIXES),
    "taxonomy.hierarchy",
    "validation.present",
    "validation.approved",
    "validation.samples",
    "validation.category_label_space",
    "validation.topic_label_space",
    "validation.category_coverage",
    "validation.topic_coverage",
    "validation.annotation_depth",
    "validation.agreement",
    "benchmark.cpu.present",
    "benchmark.cpu.completed",
    "benchmark.cpu.repeats",
    "benchmark.cpu.validation",
    "benchmark.cpu.model_registry",
    "quality.present",
    "quality.samples",
    "quality.validation_digest",
    "quality.model_registry",
    "quality.category_macro_f1",
    "quality.category_macro_recall",
    "quality.topic_macro_f1",
    "quality.topic_macro_recall",
    "quality.low_confidence_rate",
    "quality.category_calibration",
    "quality.topic_calibration",
    "quality.calibration_evidence",
    "quality.drift",
    "quality.baseline",
}


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{field_name} must be SHA-256")
    try:
        int(value, 16)
    except ValueError as error:
        raise ValueError(f"{field_name} must be SHA-256") from error
    return value.lower()


def _normalize_hierarchy(
    raw: Mapping[str, Sequence[str]],
) -> Mapping[str, tuple[str, ...]]:
    hierarchy: dict[str, tuple[str, ...]] = {}
    for category, topics in raw.items():
        if not isinstance(category, str) or not category.strip():
            raise ValueError("topic_hierarchy category must be non-empty")
        if isinstance(topics, str | bytes | bytearray) or not isinstance(
            topics, Sequence
        ):
            raise ValueError("topic_hierarchy values must be arrays")
        normalized = tuple(str(topic).strip() for topic in topics)
        if not normalized or any(not topic for topic in normalized):
            raise ValueError("topic_hierarchy topics must be non-empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("topic_hierarchy topics must be unique per category")
        hierarchy[category.strip()] = normalized
    return MappingProxyType(dict(sorted(hierarchy.items())))


class ClassificationRegistry:
    def __init__(
        self,
        models: Mapping[str, ModelDescriptor],
        *,
        topic_hierarchy: Mapping[str, Sequence[str]],
        qualification_report_digest: str,
        qualified_model_registry_digest: str,
    ) -> None:
        normalized = dict(models)
        missing = sorted(_REQUIRED_ROLES - set(normalized))
        unknown = sorted(set(normalized) - _REQUIRED_ROLES)
        if missing:
            raise ValueError(
                f"classification registry is missing: {', '.join(missing)}"
            )
        if unknown:
            raise ValueError(
                f"classification registry has unknown roles: {', '.join(unknown)}"
            )
        for role, model in normalized.items():
            if model.task != role:
                raise ValueError(
                    f"classification model task {model.task} does not match role {role}"
                )
            if not model.approved_for_production:
                raise ValueError(
                    "all classification models must be approved for production"
                )

        hierarchy = _normalize_hierarchy(topic_hierarchy)
        category_labels = set(normalized["category"].label_space)
        topic_labels = set(normalized["topic"].label_space)
        if set(hierarchy) != category_labels:
            missing_categories = sorted(category_labels - set(hierarchy))
            unknown_categories = sorted(set(hierarchy) - category_labels)
            details: list[str] = []
            if missing_categories:
                details.append("missing: " + ", ".join(missing_categories))
            if unknown_categories:
                details.append("unknown: " + ", ".join(unknown_categories))
            raise ValueError(
                "topic_hierarchy category mismatch (" + "; ".join(details) + ")"
            )
        hierarchy_topics = {
            topic for topics in hierarchy.values() for topic in topics
        }
        unknown_topics = sorted(hierarchy_topics - topic_labels)
        missing_topics = sorted(topic_labels - hierarchy_topics)
        if unknown_topics or missing_topics:
            details = []
            if missing_topics:
                details.append("missing: " + ", ".join(missing_topics))
            if unknown_topics:
                details.append("unknown: " + ", ".join(unknown_topics))
            raise ValueError(
                "topic_hierarchy topic mismatch (" + "; ".join(details) + ")"
            )

        expected = classification_model_registry_digest(normalized, hierarchy)
        qualified = _sha256(
            qualified_model_registry_digest,
            "qualified_model_registry_digest",
        )
        if expected != qualified:
            raise ValueError(
                "classification registry does not match qualification model digest"
            )
        object.__setattr__(self, "_models", MappingProxyType(normalized))
        object.__setattr__(self, "_topic_hierarchy", hierarchy)
        object.__setattr__(self, "_model_registry_digest", expected)
        object.__setattr__(
            self,
            "_qualification_report_digest",
            _sha256(qualification_report_digest, "qualification_report_digest"),
        )

    def get(self, role: str) -> ModelDescriptor:
        try:
            return self._models[role]
        except KeyError as error:
            raise KeyError(f"unknown classification model role: {role}") from error

    def allowed_topics(self, category: str) -> tuple[str, ...]:
        try:
            return self._topic_hierarchy[category]
        except KeyError as error:
            raise KeyError(f"unknown category for topic refinement: {category}") from error

    @property
    def model_registry_digest(self) -> str:
        return self._model_registry_digest

    @property
    def qualification_report_digest(self) -> str:
        return self._qualification_report_digest

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "qualification": {
                "report_digest": self.qualification_report_digest,
                "model_registry_digest": self.model_registry_digest,
            },
            "models": {
                role: descriptor.to_dict()
                for role, descriptor in sorted(self._models.items())
            },
            "topic_hierarchy": {
                category: list(topics)
                for category, topics in self._topic_hierarchy.items()
            },
        }

    @property
    def digest(self) -> str:
        return digest_json(self.to_dict())


def _descriptor(payload: Mapping[str, Any]) -> ModelDescriptor:
    allowed = {
        "name",
        "repo_id",
        "revision",
        "license",
        "task",
        "tokenizer_id",
        "tokenizer_revision",
        "weights_sha256",
        "approved_for_production",
        "training_data_review",
        "label_space",
        "label_map",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"model descriptor has unknown fields: {', '.join(unknown)}")
    return ModelDescriptor(
        name=payload["name"],
        repo_id=payload["repo_id"],
        revision=payload["revision"],
        license=payload["license"],
        task=payload["task"],
        tokenizer_id=payload["tokenizer_id"],
        tokenizer_revision=payload["tokenizer_revision"],
        weights_sha256=payload["weights_sha256"],
        approved_for_production=payload["approved_for_production"],
        training_data_review=payload["training_data_review"],
        label_space=tuple(payload["label_space"]),
        label_map=payload.get("label_map", {}),
    )


def _load_object(path: Path, field_name: str) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")
    return payload


def _validated_gate_states(raw_gates: object) -> tuple[set[str], list[str]]:
    if not isinstance(raw_gates, list) or not raw_gates:
        raise ValueError("qualification report must contain gates")
    codes: set[str] = set()
    blockers: list[str] = []
    for raw_gate in raw_gates:
        if not isinstance(raw_gate, Mapping):
            raise ValueError("qualification report gates must be objects")
        if set(raw_gate) != {"code", "state", "detail", "evidence"}:
            raise ValueError("qualification report gate fields do not match schema")
        code = raw_gate["code"]
        state = raw_gate["state"]
        detail = raw_gate["detail"]
        evidence = raw_gate["evidence"]
        if not isinstance(code, str) or not code:
            raise ValueError("qualification gate code must be non-empty")
        if code in codes:
            raise ValueError(f"qualification report contains duplicate gate: {code}")
        if state not in {"passed", "blocked"}:
            raise ValueError(f"qualification gate has invalid state: {code}")
        if not isinstance(detail, str) or not detail:
            raise ValueError(f"qualification gate detail must be non-empty: {code}")
        if not isinstance(evidence, list) or any(
            not isinstance(item, str) or not item for item in evidence
        ):
            raise ValueError(f"qualification gate evidence is invalid: {code}")
        codes.add(code)
        if state == "blocked":
            blockers.append(code)
    missing = sorted(_CORE_GATE_CODES - codes)
    if missing:
        raise ValueError(
            "qualification report is missing required gates: " + ", ".join(missing)
        )
    return codes, blockers


def _verified_qualification_report(path: Path) -> Mapping[str, Any]:
    payload = _load_object(path, "qualification report")
    required = {
        "approved_for_production",
        "gates",
        "blockers",
        "input_digest",
        "model_registry_digest",
        "validation_digest",
        "report_digest",
    }
    if set(payload) != required:
        raise ValueError("qualification report fields do not match schema")
    _, derived_blockers = _validated_gate_states(payload["gates"])
    derived_approval = not derived_blockers
    if payload["blockers"] != derived_blockers:
        raise ValueError("qualification report blockers do not match gate states")
    if payload["approved_for_production"] is not derived_approval:
        raise ValueError("qualification approval does not match gate states")
    if not derived_approval:
        raise ValueError("qualification report is not approved for production")
    report_digest = _sha256(payload["report_digest"], "report_digest")
    canonical = {
        key: payload[key]
        for key in (
            "approved_for_production",
            "gates",
            "input_digest",
            "model_registry_digest",
            "validation_digest",
        )
    }
    if digest_json(canonical) != report_digest:
        raise ValueError("qualification report digest does not match content")
    return payload


def _validate_embedded_qualification(
    raw: object,
    report: Mapping[str, Any],
) -> None:
    if raw is None:
        return
    if not isinstance(raw, Mapping):
        raise ValueError("classification registry qualification must be an object")
    if set(raw) != {"report_digest", "model_registry_digest"}:
        raise ValueError("classification registry qualification fields do not match schema")
    if raw["report_digest"] != report["report_digest"]:
        raise ValueError("classification registry qualification report digest differs")
    if raw["model_registry_digest"] != report["model_registry_digest"]:
        raise ValueError("classification registry qualification model digest differs")


def load_classification_registry(
    path: Path,
    qualification_report_path: Path,
) -> ClassificationRegistry:
    payload = _load_object(path, "classification registry")
    if payload.get("schema_version") != 2:
        raise ValueError("unsupported classification registry schema")
    allowed = {"schema_version", "qualification", "models", "topic_hierarchy"}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(
            f"classification registry has unknown fields: {', '.join(unknown)}"
        )
    raw_models = payload.get("models")
    raw_hierarchy = payload.get("topic_hierarchy")
    if not isinstance(raw_models, Mapping):
        raise ValueError("classification registry models must be an object")
    if not isinstance(raw_hierarchy, Mapping):
        raise ValueError("classification registry topic_hierarchy must be an object")
    models: dict[str, ModelDescriptor] = {}
    for role, raw in raw_models.items():
        if not isinstance(role, str) or not isinstance(raw, Mapping):
            raise ValueError("classification registry entries must be objects")
        models[role] = _descriptor(raw)
    report = _verified_qualification_report(qualification_report_path)
    _validate_embedded_qualification(payload.get("qualification"), report)
    return ClassificationRegistry(
        models,
        topic_hierarchy=raw_hierarchy,
        qualification_report_digest=report["report_digest"],
        qualified_model_registry_digest=report["model_registry_digest"],
    )


__all__ = ["ClassificationRegistry", "load_classification_registry"]
