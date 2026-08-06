"""Strict production registry for category and topic models."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .models import ModelDescriptor, digest_json


class ClassificationRegistry:
    def __init__(self, models: Mapping[str, ModelDescriptor]) -> None:
        normalized = dict(models)
        required = {"category", "topic"}
        missing = sorted(required - set(normalized))
        unknown = sorted(set(normalized) - required)
        if missing:
            raise ValueError(f"classification registry is missing: {', '.join(missing)}")
        if unknown:
            raise ValueError(f"classification registry has unknown roles: {', '.join(unknown)}")
        if not all(model.approved_for_production for model in normalized.values()):
            raise ValueError("all classification models must be approved for production")
        object.__setattr__(self, "_models", MappingProxyType(normalized))

    def get(self, role: str) -> ModelDescriptor:
        try:
            return self._models[role]
        except KeyError as error:
            raise KeyError(f"unknown classification model role: {role}") from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "models": {
                role: descriptor.to_dict()
                for role, descriptor in sorted(self._models.items())
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
        "approved_for_production",
        "training_data_review",
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
        approved_for_production=payload["approved_for_production"],
        training_data_review=payload["training_data_review"],
        label_map=payload.get("label_map", {}),
    )


def load_classification_registry(path: Path) -> ClassificationRegistry:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported classification registry schema")
    raw_models = payload.get("models")
    if not isinstance(raw_models, Mapping):
        raise ValueError("classification registry models must be an object")
    models: dict[str, ModelDescriptor] = {}
    for role, raw in raw_models.items():
        if not isinstance(role, str) or not isinstance(raw, Mapping):
            raise ValueError("classification registry entries must be objects")
        models[role] = _descriptor(raw)
    return ClassificationRegistry(models)


__all__ = ["ClassificationRegistry", "load_classification_registry"]
