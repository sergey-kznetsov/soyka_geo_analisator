from __future__ import annotations

import pytest

from soika_uds.classification.qualification_loader import qualification_input_from_dict

POLICY = {
    "min_validation_samples": 100,
    "min_samples_per_category": 10,
    "min_samples_per_topic": 5,
    "min_annotations_per_item": 2,
    "min_annotation_agreement": 0.8,
    "min_category_macro_f1": 0.75,
    "min_category_macro_recall": 0.7,
    "min_topic_macro_f1": 0.65,
    "min_topic_macro_recall": 0.6,
    "max_low_confidence_rate": 0.25,
    "max_expected_calibration_error": 0.1,
    "max_drift_tvd": 0.2,
    "min_benchmark_repeats": 2,
    "require_gpu": False,
    "allowed_weight_formats": ["safetensors"],
}


def payload() -> dict[str, object]:
    return {
        "schema_version": 2,
        "models": [],
        "topic_hierarchy": {},
        "validation": None,
        "benchmarks": [],
        "quality": None,
        "policy": POLICY,
    }


def test_non_object_model_entry_is_rejected() -> None:
    value = payload()
    value["models"] = ["not-an-object"]
    with pytest.raises(ValueError, match="models entries"):
        qualification_input_from_dict(value)


def test_non_object_benchmark_entry_is_rejected() -> None:
    value = payload()
    value["benchmarks"] = [42]
    with pytest.raises(ValueError, match="benchmarks entries"):
        qualification_input_from_dict(value)


def test_non_object_hierarchy_is_rejected() -> None:
    value = payload()
    value["topic_hierarchy"] = []
    with pytest.raises(ValueError, match="topic_hierarchy"):
        qualification_input_from_dict(value)
