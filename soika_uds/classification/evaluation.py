"""Deterministic quality metrics for validation, calibration, and drift."""

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


def _unique_by_key(items: Sequence[Any], *, field_name: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items:
        key = item.message_key
        if key in result:
            raise ValueError(f"{field_name} contains duplicate message_key: {key}")
        result[key] = item
    return result


def _safe_ratio(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _label_metrics(
    expected: Sequence[str],
    predicted: Sequence[str],
) -> dict[str, Any]:
    labels = sorted(set(expected) | set(predicted))
    matrix = [
        [
            sum(
                expected_item == expected_label
                and predicted_item == predicted_label
                for expected_item, predicted_item in zip(
                    expected,
                    predicted,
                    strict=True,
                )
            )
            for predicted_label in labels
        ]
        for expected_label in labels
    ]
    per_label: dict[str, dict[str, float | int]] = {}
    precision_values: list[float] = []
    recall_values: list[float] = []
    f1_values: list[float] = []

    for index, label in enumerate(labels):
        true_positive = matrix[index][index]
        false_positive = sum(row[index] for row in matrix) - true_positive
        false_negative = sum(matrix[index]) - true_positive
        support = sum(matrix[index])
        precision = _safe_ratio(true_positive, true_positive + false_positive)
        recall = _safe_ratio(true_positive, true_positive + false_negative)
        f1 = (
            2.0 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
        precision_values.append(precision)
        recall_values.append(recall)
        f1_values.append(f1)
        per_label[label] = {
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
            "support": support,
        }

    size = len(expected)
    correct = sum(
        expected_item == predicted_item
        for expected_item, predicted_item in zip(
            expected,
            predicted,
            strict=True,
        )
    )
    return {
        "accuracy": round(_safe_ratio(correct, size), 6),
        "macro_precision": (
            round(sum(precision_values) / len(precision_values), 6)
            if precision_values
            else 0.0
        ),
        "macro_recall": (
            round(sum(recall_values) / len(recall_values), 6)
            if recall_values
            else 0.0
        ),
        "macro_f1": (
            round(sum(f1_values) / len(f1_values), 6)
            if f1_values
            else 0.0
        ),
        "per_label": per_label,
        "confusion_matrix": {
            "labels": labels,
            "matrix": matrix,
        },
    }


def _calibration_metrics(
    expected: Sequence[str],
    predicted: Sequence[str],
    scores: Sequence[float],
    *,
    bins: int = 10,
) -> dict[str, Any]:
    if type(bins) is not int or bins < 2:
        raise ValueError("calibration bins must be an integer >= 2")
    if len(expected) != len(predicted) or len(expected) != len(scores):
        raise ValueError("calibration inputs must have equal length")
    if not expected:
        return {
            "bins": bins,
            "expected_calibration_error": 0.0,
            "brier_score": 0.0,
            "bin_stats": [],
        }

    bin_values: list[list[tuple[float, int]]] = [[] for _ in range(bins)]
    brier_total = 0.0
    for expected_label, predicted_label, raw_score in zip(
        expected,
        predicted,
        scores,
        strict=True,
    ):
        score = float(raw_score)
        if not 0.0 <= score <= 1.0:
            raise ValueError("calibration score must be in [0, 1]")
        correct = int(expected_label == predicted_label)
        index = min(int(score * bins), bins - 1)
        bin_values[index].append((score, correct))
        brier_total += (score - correct) ** 2

    ece = 0.0
    bin_stats: list[dict[str, Any]] = []
    total = len(expected)
    for index, values in enumerate(bin_values):
        if not values:
            continue
        confidence = sum(score for score, _ in values) / len(values)
        accuracy = sum(correct for _, correct in values) / len(values)
        ece += len(values) / total * abs(accuracy - confidence)
        bin_stats.append(
            {
                "index": index,
                "lower": round(index / bins, 6),
                "upper": round((index + 1) / bins, 6),
                "samples": len(values),
                "accuracy": round(accuracy, 6),
                "mean_confidence": round(confidence, 6),
            }
        )

    return {
        "bins": bins,
        "expected_calibration_error": round(ece, 6),
        "brier_score": round(brier_total / total, 6),
        "bin_stats": bin_stats,
    }


def evaluate_predictions(
    expected: Sequence[ValidationLabel],
    predictions: Sequence[MessageClassificationResult],
    *,
    calibration_bins: int = 10,
) -> dict[str, Any]:
    expected_by_key = _unique_by_key(expected, field_name="validation labels")
    predicted_by_key = _unique_by_key(predictions, field_name="predictions")
    if set(expected_by_key) != set(predicted_by_key):
        raise ValueError("validation and prediction keys must match")

    keys = sorted(expected_by_key)
    expected_categories = [expected_by_key[key].category for key in keys]
    predicted_categories = [predicted_by_key[key].category.label for key in keys]
    category_scores = [predicted_by_key[key].category.score for key in keys]
    expected_topics = [expected_by_key[key].topic for key in keys]
    predicted_topics = [predicted_by_key[key].topic.label for key in keys]
    topic_scores = [predicted_by_key[key].topic.score for key in keys]
    size = len(keys)
    low_confidence = sum(predicted_by_key[key].low_confidence for key in keys)

    category = _label_metrics(expected_categories, predicted_categories)
    category["calibration"] = _calibration_metrics(
        expected_categories,
        predicted_categories,
        category_scores,
        bins=calibration_bins,
    )
    topic = _label_metrics(expected_topics, predicted_topics)
    topic["calibration"] = _calibration_metrics(
        expected_topics,
        predicted_topics,
        topic_scores,
        bins=calibration_bins,
    )

    payload = {
        "samples": size,
        "category_accuracy": category["accuracy"],
        "category_macro_precision": category["macro_precision"],
        "category_macro_recall": category["macro_recall"],
        "category_macro_f1": category["macro_f1"],
        "topic_accuracy": topic["accuracy"],
        "topic_macro_precision": topic["macro_precision"],
        "topic_macro_recall": topic["macro_recall"],
        "topic_macro_f1": topic["macro_f1"],
        "low_confidence_rate": round(_safe_ratio(low_confidence, size), 6),
        "category": category,
        "topic": topic,
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
