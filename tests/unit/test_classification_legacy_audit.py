from __future__ import annotations

from pathlib import Path

from soika_uds.classification.qualification import (
    load_qualification_input,
    qualify_release,
)


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_legacy_model_audit_remains_fail_closed() -> None:
    inputs = load_qualification_input(
        repository_root()
        / "configs"
        / "classification"
        / "stage8b-legacy-qualification.json"
    )
    report = qualify_release(inputs)

    assert report.approved_for_production is False
    assert "model.category.revision" in report.blockers
    assert "model.category.training_data" in report.blockers
    assert "model.topic.repository" in report.blockers
    assert "model.topic.license" in report.blockers
    assert "validation.present" in report.blockers
    assert "benchmark.cpu.present" in report.blockers
    assert "benchmark.gpu.present" in report.blockers
    assert "quality.present" in report.blockers


def test_legacy_audit_does_not_substitute_another_topic_model() -> None:
    inputs = load_qualification_input(
        repository_root()
        / "configs"
        / "classification"
        / "stage8b-legacy-qualification.json"
    )
    topic = next(model for model in inputs.models if model.role == "topic")

    assert topic.repo_id == "Sandrro/text_to_subfunction_v10"
    assert topic.repository_exists is False
    assert topic.resolved_revision is None
