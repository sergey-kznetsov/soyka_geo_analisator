from __future__ import annotations

import json
from pathlib import Path

import pytest

from soika_uds.classification import ExecutionDevice
from soika_uds.classification.qualification import (
    BenchmarkEvidence,
    GateState,
    ModelAuditRecord,
    QualificationInput,
    QualificationPolicy,
    QualityEvidence,
    ValidationSetEvidence,
    audited_model_registry_digest,
    load_qualification_input,
    qualify_release,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
COMMIT_A = "c" * 40
COMMIT_B = "d" * 40


def policy(*, require_gpu: bool = True) -> QualificationPolicy:
    return QualificationPolicy(
        min_validation_samples=100,
        min_samples_per_category=20,
        min_samples_per_topic=20,
        min_annotations_per_item=2,
        min_annotation_agreement=0.8,
        min_category_macro_f1=0.75,
        min_category_macro_recall=0.70,
        min_topic_macro_f1=0.65,
        min_topic_macro_recall=0.60,
        max_low_confidence_rate=0.25,
        max_expected_calibration_error=0.10,
        max_drift_tvd=0.20,
        min_benchmark_repeats=2,
        require_gpu=require_gpu,
        allowed_weight_formats=("safetensors",),
    )


def model(role: str) -> ModelAuditRecord:
    labels = (
        ("roads", "lighting")
        if role == "category"
        else ("pothole", "road_surface", "lamp_outage", "dark_yard")
    )
    return ModelAuditRecord(
        role=role,
        repo_id=f"example/{role}",
        repository_exists=True,
        requested_revision="main",
        resolved_revision=COMMIT_A if role == "category" else COMMIT_B,
        license_id="MIT",
        license_reviewed=True,
        training_data_documented=True,
        training_data_reviewed=True,
        intended_use_documented=True,
        weights_format="safetensors",
        weights_sha256=SHA_A if role == "category" else SHA_B,
        tokenizer_id="example/tokenizer",
        tokenizer_revision=COMMIT_B,
        label_space=labels,
        evidence_urls=(f"https://example.test/{role}",),
        notes=(),
    )


def hierarchy() -> dict[str, tuple[str, ...]]:
    return {
        "roads": ("pothole", "road_surface"),
        "lighting": ("lamp_outage", "dark_yard"),
    }


def validation(*, approved: bool = True) -> ValidationSetEvidence:
    return ValidationSetEvidence(
        dataset_id="soika-validation",
        version="1.0.0",
        digest=SHA_A,
        sample_count=100,
        category_counts={"roads": 50, "lighting": 50},
        topic_counts={
            "pothole": 25,
            "road_surface": 25,
            "lamp_outage": 25,
            "dark_yard": 25,
        },
        annotations_per_item=2,
        agreement=0.9,
        approved=approved,
        approval_reference="review-2026-08" if approved else None,
    )


def base_input(*, require_gpu: bool = True) -> QualificationInput:
    return QualificationInput(
        models=(model("category"), model("topic")),
        topic_hierarchy=hierarchy(),
        validation=validation(),
        policy=policy(require_gpu=require_gpu),
    )


def benchmark(
    device: ExecutionDevice,
    registry_digest: str,
    *,
    output_digest: str = SHA_B,
) -> BenchmarkEvidence:
    return BenchmarkEvidence(
        device=device,
        completed=True,
        samples=100,
        batch_size=16,
        repeat_count=2,
        duration_seconds=5.0,
        throughput_per_second=20.0,
        peak_memory_mb=512.0,
        model_registry_digest=registry_digest,
        validation_digest=SHA_A,
        output_digest=output_digest,
    )


def quality(registry_digest: str) -> QualityEvidence:
    return QualityEvidence(
        report_digest=SHA_B,
        model_registry_digest=registry_digest,
        validation_digest=SHA_A,
        samples=100,
        category_macro_f1=0.80,
        category_macro_recall=0.78,
        topic_macro_f1=0.70,
        topic_macro_recall=0.68,
        low_confidence_rate=0.15,
        category_ece=0.05,
        topic_ece=0.08,
        drift_tvd=0.10,
        calibration_digest=SHA_A,
        baseline_digest=SHA_B,
    )


def complete_input() -> QualificationInput:
    seed = base_input()
    registry_digest = audited_model_registry_digest(seed)
    return QualificationInput(
        models=seed.models,
        topic_hierarchy=seed.topic_hierarchy,
        validation=seed.validation,
        benchmarks=(
            benchmark(ExecutionDevice.CPU, registry_digest),
            benchmark(ExecutionDevice.GPU, registry_digest),
        ),
        quality=quality(registry_digest),
        policy=seed.policy,
    )


def test_complete_evidence_passes_all_gates() -> None:
    report = qualify_release(complete_input())
    assert report.approved_for_production is True
    assert report.blockers == ()
    assert all(gate.state is GateState.PASSED for gate in report.gates)
    assert len(report.input_digest) == 64
    assert len(report.model_registry_digest) == 64
    assert report.validation_digest == SHA_A
    assert len(report.report_digest) == 64


def test_missing_topic_and_validation_fail_closed() -> None:
    inputs = QualificationInput(
        models=(model("category"),),
        topic_hierarchy={},
        policy=policy(require_gpu=False),
    )
    report = qualify_release(inputs)
    assert report.approved_for_production is False
    assert "model.topic.present" in report.blockers
    assert "taxonomy.hierarchy" in report.blockers
    assert "validation.present" in report.blockers
    assert "benchmark.cpu.present" in report.blockers
    assert "quality.present" in report.blockers


def test_unreviewed_training_data_blocks_model() -> None:
    category = model("category")
    blocked_category = ModelAuditRecord(
        **{
            **category.to_dict(),
            "training_data_documented": False,
            "training_data_reviewed": False,
        }
    )
    inputs = complete_input()
    report = qualify_release(
        QualificationInput(
            models=(blocked_category, model("topic")),
            topic_hierarchy=inputs.topic_hierarchy,
            validation=inputs.validation,
            benchmarks=inputs.benchmarks,
            quality=inputs.quality,
            policy=inputs.policy,
        )
    )
    assert "model.category.training_data" in report.blockers


def test_cpu_gpu_output_mismatch_blocks_release() -> None:
    inputs = complete_input()
    registry_digest = audited_model_registry_digest(inputs)
    report = qualify_release(
        QualificationInput(
            models=inputs.models,
            topic_hierarchy=inputs.topic_hierarchy,
            validation=inputs.validation,
            benchmarks=(
                benchmark(ExecutionDevice.CPU, registry_digest),
                benchmark(
                    ExecutionDevice.GPU,
                    registry_digest,
                    output_digest="f" * 64,
                ),
            ),
            quality=inputs.quality,
            policy=inputs.policy,
        )
    )
    assert "benchmark.cpu_gpu_equivalence" in report.blockers


def test_benchmark_from_another_registry_blocks_release() -> None:
    inputs = complete_input()
    bad_cpu = BenchmarkEvidence(
        **{
            **inputs.benchmarks[0].to_dict(),
            "model_registry_digest": SHA_A,
        }
    )
    report = qualify_release(
        QualificationInput(
            models=inputs.models,
            topic_hierarchy=inputs.topic_hierarchy,
            validation=inputs.validation,
            benchmarks=(bad_cpu, inputs.benchmarks[1]),
            quality=inputs.quality,
            policy=inputs.policy,
        )
    )
    assert "benchmark.cpu.model_registry" in report.blockers


def test_quality_from_another_registry_blocks_release() -> None:
    inputs = complete_input()
    bad_quality = QualityEvidence(
        **{
            **inputs.quality.to_dict(),
            "model_registry_digest": SHA_A,
        }
    )
    report = qualify_release(
        QualificationInput(
            models=inputs.models,
            topic_hierarchy=inputs.topic_hierarchy,
            validation=inputs.validation,
            benchmarks=inputs.benchmarks,
            quality=bad_quality,
            policy=inputs.policy,
        )
    )
    assert "quality.model_registry" in report.blockers


def test_quality_from_another_validation_set_blocks_release() -> None:
    inputs = complete_input()
    bad_quality = QualityEvidence(
        **{
            **inputs.quality.to_dict(),
            "validation_digest": SHA_B,
        }
    )
    report = qualify_release(
        QualificationInput(
            models=inputs.models,
            topic_hierarchy=inputs.topic_hierarchy,
            validation=inputs.validation,
            benchmarks=inputs.benchmarks,
            quality=bad_quality,
            policy=inputs.policy,
        )
    )
    assert "quality.validation_digest" in report.blockers


def test_incomplete_label_space_coverage_blocks_release() -> None:
    inputs = complete_input()
    incomplete = ValidationSetEvidence(
        dataset_id="soika-validation",
        version="1.0.0",
        digest=SHA_A,
        sample_count=100,
        category_counts={"roads": 100},
        topic_counts={"pothole": 100},
        annotations_per_item=2,
        agreement=0.9,
        approved=True,
        approval_reference="review-2026-08",
    )
    report = qualify_release(
        QualificationInput(
            models=inputs.models,
            topic_hierarchy=inputs.topic_hierarchy,
            validation=incomplete,
            benchmarks=inputs.benchmarks,
            quality=inputs.quality,
            policy=inputs.policy,
        )
    )
    assert "validation.category_label_space" in report.blockers
    assert "validation.topic_label_space" in report.blockers


def test_incomplete_taxonomy_blocks_release() -> None:
    inputs = complete_input()
    report = qualify_release(
        QualificationInput(
            models=inputs.models,
            topic_hierarchy={"roads": ("pothole", "road_surface")},
            validation=inputs.validation,
            benchmarks=inputs.benchmarks,
            quality=inputs.quality,
            policy=inputs.policy,
        )
    )
    assert "taxonomy.hierarchy" in report.blockers


def test_unapproved_validation_set_returns_blocker() -> None:
    inputs = complete_input()
    report = qualify_release(
        QualificationInput(
            models=inputs.models,
            topic_hierarchy=inputs.topic_hierarchy,
            validation=validation(approved=False),
            benchmarks=inputs.benchmarks,
            quality=inputs.quality,
            policy=inputs.policy,
        )
    )
    assert "validation.approved" in report.blockers


def test_quality_thresholds_are_enforced() -> None:
    inputs = complete_input()
    weak = QualityEvidence(
        report_digest=SHA_A,
        model_registry_digest=audited_model_registry_digest(inputs),
        validation_digest=SHA_A,
        samples=100,
        category_macro_f1=0.50,
        category_macro_recall=0.50,
        topic_macro_f1=0.40,
        topic_macro_recall=0.40,
        low_confidence_rate=0.50,
        category_ece=0.20,
        topic_ece=0.20,
        drift_tvd=0.30,
        calibration_digest=None,
        baseline_digest=None,
    )
    report = qualify_release(
        QualificationInput(
            models=inputs.models,
            topic_hierarchy=inputs.topic_hierarchy,
            validation=inputs.validation,
            benchmarks=inputs.benchmarks,
            quality=weak,
            policy=inputs.policy,
        )
    )
    assert "quality.category_macro_f1" in report.blockers
    assert "quality.topic_macro_recall" in report.blockers
    assert "quality.low_confidence_rate" in report.blockers
    assert "quality.category_calibration" in report.blockers
    assert "quality.calibration_evidence" in report.blockers
    assert "quality.drift" in report.blockers
    assert "quality.baseline" in report.blockers


def test_boolean_numeric_field_is_rejected() -> None:
    with pytest.raises(ValueError, match="numeric"):
        QualityEvidence(
            report_digest=SHA_A,
            model_registry_digest=SHA_A,
            validation_digest=SHA_A,
            samples=100,
            category_macro_f1=True,
            category_macro_recall=0.8,
            topic_macro_f1=0.7,
            topic_macro_recall=0.7,
            low_confidence_rate=0.1,
            category_ece=0.1,
            topic_ece=0.1,
            drift_tvd=0.1,
            calibration_digest=SHA_A,
            baseline_digest=SHA_A,
        )


def test_loader_rejects_unknown_fields(tmp_path: Path) -> None:
    payload = complete_input().to_dict()
    payload["unexpected"] = True
    path = tmp_path / "qualification.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown fields"):
        load_qualification_input(path)


def test_report_is_deterministic() -> None:
    first = qualify_release(complete_input()).to_dict()
    second = qualify_release(complete_input()).to_dict()
    assert first == second
