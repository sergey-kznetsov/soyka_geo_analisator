from __future__ import annotations

import pytest

from soika_uds.classification.evaluation import ValidationLabel, evaluate_predictions
from soika_uds.classification.models import (
    ConfidenceBand,
    LabelPrediction,
    MessageClassificationResult,
)


def prediction(
    key: str,
    category: str,
    topic: str,
    *,
    category_score: float,
    topic_score: float,
) -> MessageClassificationResult:
    return MessageClassificationResult(
        message_key=key,
        category=LabelPrediction(category, category_score),
        topic=LabelPrediction(topic, topic_score),
        confidence_band=ConfidenceBand.HIGH,
        low_confidence=False,
        included_for_analysis=True,
        reasons=(),
        model_provenance={"registry_digest": "fixture"},
    )


def test_detailed_report_contains_precision_recall_and_confusion_matrix() -> None:
    expected = (
        ValidationLabel("a", "roads", "pothole"),
        ValidationLabel("b", "roads", "surface"),
        ValidationLabel("c", "lighting", "outage"),
        ValidationLabel("d", "lighting", "outage"),
    )
    predictions = (
        prediction(
            "a",
            "roads",
            "pothole",
            category_score=0.9,
            topic_score=0.9,
        ),
        prediction(
            "b",
            "lighting",
            "surface",
            category_score=0.8,
            topic_score=0.8,
        ),
        prediction(
            "c",
            "lighting",
            "outage",
            category_score=0.7,
            topic_score=0.7,
        ),
        prediction(
            "d",
            "lighting",
            "surface",
            category_score=0.6,
            topic_score=0.6,
        ),
    )
    report = evaluate_predictions(expected, predictions, calibration_bins=5)

    assert report["category_macro_precision"] == 0.833333
    assert report["category_macro_recall"] == 0.75
    assert report["category_macro_f1"] == 0.733333
    assert report["category"]["confusion_matrix"] == {
        "labels": ["lighting", "roads"],
        "matrix": [[2, 0], [1, 1]],
    }
    assert report["category"]["per_label"]["roads"]["support"] == 2
    assert report["topic"]["confusion_matrix"]["labels"] == [
        "outage",
        "pothole",
        "surface",
    ]


def test_calibration_metrics_are_bounded_and_deterministic() -> None:
    expected = (
        ValidationLabel("a", "roads", "pothole"),
        ValidationLabel("b", "lighting", "outage"),
    )
    predictions = (
        prediction(
            "a",
            "roads",
            "pothole",
            category_score=0.9,
            topic_score=0.8,
        ),
        prediction(
            "b",
            "roads",
            "outage",
            category_score=0.7,
            topic_score=0.6,
        ),
    )
    first = evaluate_predictions(expected, predictions, calibration_bins=5)
    second = evaluate_predictions(expected, predictions, calibration_bins=5)
    category = first["category"]["calibration"]
    topic = first["topic"]["calibration"]

    assert 0.0 <= category["expected_calibration_error"] <= 1.0
    assert 0.0 <= category["brier_score"] <= 1.0
    assert 0.0 <= topic["expected_calibration_error"] <= 1.0
    assert first == second


def test_duplicate_validation_keys_fail_closed() -> None:
    expected = (
        ValidationLabel("a", "roads", "pothole"),
        ValidationLabel("a", "roads", "surface"),
    )
    predictions = (
        prediction(
            "a",
            "roads",
            "pothole",
            category_score=0.9,
            topic_score=0.9,
        ),
    )
    with pytest.raises(ValueError, match="duplicate message_key"):
        evaluate_predictions(expected, predictions)


def test_invalid_calibration_bin_count_is_rejected() -> None:
    expected = (ValidationLabel("a", "roads", "pothole"),)
    predictions = (
        prediction(
            "a",
            "roads",
            "pothole",
            category_score=0.9,
            topic_score=0.9,
        ),
    )
    with pytest.raises(ValueError, match="bins"):
        evaluate_predictions(expected, predictions, calibration_bins=1)
