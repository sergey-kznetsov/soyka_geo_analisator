from soika_uds.classification.evaluation import (
    ValidationLabel,
    evaluate_predictions,
    label_distribution,
    total_variation_drift,
)
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
    low_confidence: bool = False,
) -> MessageClassificationResult:
    return MessageClassificationResult(
        message_key=key,
        category=LabelPrediction(category, 0.9),
        topic=LabelPrediction(topic, 0.8),
        confidence_band=ConfidenceBand.HIGH,
        low_confidence=low_confidence,
        included_for_analysis=not low_confidence,
        reasons=("category_below_threshold",) if low_confidence else (),
        model_provenance={"registry_digest": "fixture"},
    )


def test_evaluation_reports_accuracy_f1_and_digest() -> None:
    expected = (
        ValidationLabel("a", "roads", "pothole"),
        ValidationLabel("b", "lighting", "outage"),
    )
    predictions = (
        prediction("a", "roads", "pothole"),
        prediction("b", "roads", "outage", low_confidence=True),
    )
    report = evaluate_predictions(expected, predictions)
    assert report["samples"] == 2
    assert report["category_accuracy"] == 0.5
    assert report["topic_accuracy"] == 1.0
    assert report["low_confidence_rate"] == 0.5
    assert len(report["digest"]) == 64


def test_evaluation_requires_matching_keys() -> None:
    expected = (ValidationLabel("a", "roads", "pothole"),)
    predictions = (prediction("b", "roads", "pothole"),)
    try:
        evaluate_predictions(expected, predictions)
    except ValueError as error:
        assert "keys" in str(error)
    else:
        raise AssertionError("mismatched keys must fail")


def test_distribution_and_total_variation_drift() -> None:
    baseline = label_distribution(("roads", "roads", "lighting"))
    current = label_distribution(("roads", "lighting", "lighting"))
    assert baseline == {"lighting": 0.333333, "roads": 0.666667}
    assert current == {"lighting": 0.666667, "roads": 0.333333}
    assert total_variation_drift(baseline, current) == 0.333334
