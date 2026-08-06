"""Deterministic quality metrics for validation and drift monitoring."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .models import MessageClassificationResult, digest_json


@dataclass(frozen=True, slots=True)
class ValidationLabel:
    message_key: str
    category: str
    topic: str

    def __post_init__(self) -> None:
        for field_name in ("message_key", "category", "topic"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")


def _macro_f1(expected: Sequence[str], predicted: Sequence[str]) -> float:
    labels = sorted(set(expected) | set(predicted))
    if not labels:
        return 0.0
    scores: list[float] = []
    for label in labels:
        true_positive = sum(
            expected_item == label and predicted_item == label
            for expected_item, predicted_item in zip(expected, predicted, strict=True)
        )
        false_positive = sum(
            expected_item != label and predicted_item == label
            for expected_item, predicted_item in zip(expected, predicted, strict=True)
        )
        false_negative = sum(
            expected_item == label and predicted_item != label
            for expected_item, predicted_item in zip(expected, predicted, strict=True)
        )
        precision_denominator = true_positive + false_positive
        recall_denominator = true_positive + false_negative
        precision = (
            true_positive / precision_denominator if precision_denominator else 0.0
        )
        recall = true_positive / recall_denominator if recall_denominator else 0.0
        scores.append(
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
    return round(sum(scores) / len(scores), 6)


def evaluate_predictions(
    expected: Sequence[ValidationLabel],
    predictions: Sequence[MessageClassificationResult],
) -> dict[str, Any]:
    expected_by_key = {item.message_key: item for item in expected}
    predicted_by_key = {item.message_key: item for item in predictions}
    if set(expected_by_key) != set(predicted_by_key):
        raise ValueError("validation and prediction keys must match")
    keys = sorted(expected_by_key)
    expected_categories = [expected_by_key[key].category for key in keys]
    predicted_categories = [predicted_by_key[key].category.label for key in keys]
    expected_topics = [expected_by_key[key].topic for key in keys]
    predicted_topics = [predicted_by_key[key].topic.label for key in keys]
    size = len(keys)
    category_correct = sum(
        expected_item == predicted_item
        for expected_item, predicted_item in zip(
            expected_categories,
            predicted_categories,
            strict=True,
        )
    )
    topic_correct = sum(
        expected_item == predicted_item
        for expected_item, predicted_item in zip(
            expected_topics,
            predicted_topics,
            strict=True,
        )
    )
    low_confidence = sum(predicted_by_key[key].low_confidence for key in keys)
    payload = {
        "samples": size,
        "category_accuracy": round(category_correct / size, 6) if size else 0.0,
        "category_macro_f1": _macro_f1(
            expected_categories,
            predicted_categories,
        ),
        "topic_accuracy": round(topic_correct / size, 6) if size else 0.0,
        "topic_macro_f1": _macro_f1(expected_topics, predicted_topics),
        "low_confidence_rate": round(low_confidence / size, 6) if size else 0.0,
    }
    payload["digest"] = digest_json(payload)
    return payload


def label_distribution(labels: Sequence[str]) -> dict[str, float]:
    counts = Counter(labels)
    total = sum(counts.values())
    if not total:
        return {}
    return {
        label: round(count / total, 6)
        for label, count in sorted(counts.items())
    }


def total_variation_drift(
    baseline: Mapping[str, float],
    current: Mapping[str, float],
) -> float:
    labels = set(baseline) | set(current)
    drift = 0.5 * sum(
        abs(float(baseline.get(label, 0.0)) - float(current.get(label, 0.0)))
        for label in labels
    )
    return round(drift, 6)


__all__ = [
    "ValidationLabel",
    "evaluate_predictions",
    "label_distribution",
    "total_variation_drift",
]
