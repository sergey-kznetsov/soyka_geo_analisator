from __future__ import annotations

import json
from pathlib import Path

import pytest

from soika_uds.classification import (
    ModelDescriptor,
    classification_model_registry_digest,
    load_classification_registry,
)
from soika_uds.classification.models import digest_json

MODEL_COMMIT = "a" * 40
TOKENIZER_COMMIT = "b" * 40
CATEGORY_SHA = "c" * 64
TOPIC_SHA = "d" * 64
INPUT_SHA = "e" * 64
VALIDATION_SHA = "f" * 64


def descriptor(role: str) -> ModelDescriptor:
    return ModelDescriptor(
        name=role,
        repo_id=f"example/{role}",
        revision=MODEL_COMMIT,
        license="MIT",
        task=role,
        tokenizer_id="example/tokenizer",
        tokenizer_revision=TOKENIZER_COMMIT,
        weights_sha256=CATEGORY_SHA if role == "category" else TOPIC_SHA,
        approved_for_production=True,
        training_data_review="approved",
        label_space=(
            ("roads", "lighting")
            if role == "category"
            else ("pothole", "road_surface", "lamp_outage", "dark_yard")
        ),
    )


def hierarchy() -> dict[str, tuple[str, ...]]:
    return {
        "roads": ("pothole", "road_surface"),
        "lighting": ("lamp_outage", "dark_yard"),
    }


def write_documents(
    tmp_path: Path,
    *,
    gate_state: str = "passed",
) -> tuple[Path, Path]:
    models = {
        "category": descriptor("category"),
        "topic": descriptor("topic"),
    }
    registry_digest = classification_model_registry_digest(models, hierarchy())
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "models": {
                    role: model.to_dict() for role, model in models.items()
                },
                "topic_hierarchy": {
                    category: list(topics)
                    for category, topics in hierarchy().items()
                },
            }
        ),
        encoding="utf-8",
    )
    gates = [
        {
            "code": "fixture.gate",
            "state": gate_state,
            "detail": "fixture evidence",
            "evidence": [],
        }
    ]
    canonical = {
        "approved_for_production": True,
        "gates": gates,
        "input_digest": INPUT_SHA,
        "model_registry_digest": registry_digest,
        "validation_digest": VALIDATION_SHA,
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(
            {
                **canonical,
                "blockers": [],
                "report_digest": digest_json(canonical),
            }
        ),
        encoding="utf-8",
    )
    return registry_path, report_path


def test_loader_binds_registry_to_successful_qualification(tmp_path: Path) -> None:
    registry_path, report_path = write_documents(tmp_path)
    registry = load_classification_registry(registry_path, report_path)

    assert registry.get("category").task == "category"
    assert registry.allowed_topics("roads") == ("pothole", "road_surface")
    assert len(registry.qualification_report_digest) == 64


def test_loader_rejects_approved_report_with_blocked_gate(tmp_path: Path) -> None:
    registry_path, report_path = write_documents(tmp_path, gate_state="blocked")

    with pytest.raises(ValueError, match="blocked gate"):
        load_classification_registry(registry_path, report_path)
