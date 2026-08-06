"""Deterministic batch classification over preprocessed messages."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from .calibration import IdentityCalibrator, ScoreCalibrator
from .models import (
    ClassificationBatchResult,
    ClassificationConfig,
    ClassificationStats,
    ConfidenceBand,
    LabelPrediction,
    MessageClassificationResult,
    ModelDescriptor,
    digest_json,
)
from .registry import ClassificationRegistry


class PredictionBackend(Protocol):
    def predict_batch(
        self,
        texts: Sequence[str],
        *,
        model: ModelDescriptor,
        batch_size: int,
        device: str,
    ) -> Sequence[Sequence[LabelPrediction]]: ...


def _top_prediction(
    candidates: Sequence[LabelPrediction],
    model: ModelDescriptor,
    calibrator: ScoreCalibrator,
) -> LabelPrediction:
    if not candidates:
        raise ValueError(f"model {model.name} returned no predictions")
    ordered = sorted(candidates, key=lambda item: (-item.score, item.label))
    top = ordered[0]
    mapped = model.label_map.get(top.label, top.label)
    return LabelPrediction(
        label=mapped,
        score=calibrator.calibrate(top.score),
    )


def _confidence_band(score: float, config: ClassificationConfig) -> ConfidenceBand:
    if score >= config.high_confidence_threshold:
        return ConfidenceBand.HIGH
    if score >= config.category_threshold:
        return ConfidenceBand.MEDIUM
    return ConfidenceBand.LOW


def _eligible(message: Mapping[str, Any], config: ClassificationConfig) -> bool:
    if message.get("decision") != "accepted" and not config.include_rejected:
        return False
    duplicate = message.get("duplicate", {})
    if not isinstance(duplicate, Mapping):
        return False
    excluded_duplicate = not duplicate.get("included_for_analysis", False)
    if excluded_duplicate and not config.include_duplicates:
        return False
    return bool(str(message.get("model_text", "")).strip())


class ClassificationEngine:
    def __init__(
        self,
        registry: ClassificationRegistry,
        backend: PredictionBackend,
        config: ClassificationConfig | None = None,
        *,
        category_calibrator: ScoreCalibrator | None = None,
        topic_calibrator: ScoreCalibrator | None = None,
    ) -> None:
        self._registry = registry
        self._backend = backend
        self._config = config or ClassificationConfig()
        self._category_calibrator = category_calibrator or IdentityCalibrator()
        self._topic_calibrator = topic_calibrator or IdentityCalibrator()

    def classify(
        self,
        messages: Sequence[Mapping[str, Any]],
    ) -> ClassificationBatchResult:
        ordered = sorted(
            (dict(message) for message in messages),
            key=lambda item: str(item.get("message_key", "")),
        )
        eligible = [message for message in ordered if _eligible(message, self._config)]
        texts = [str(message["model_text"]) for message in eligible]
        category_model = self._registry.get("category")
        topic_model = self._registry.get("topic")
        category_rows = self._backend.predict_batch(
            texts,
            model=category_model,
            batch_size=self._config.batch_size,
            device=self._config.device.value,
        )
        topic_rows = self._backend.predict_batch(
            texts,
            model=topic_model,
            batch_size=self._config.batch_size,
            device=self._config.device.value,
        )
        if len(category_rows) != len(eligible) or len(topic_rows) != len(eligible):
            raise ValueError("prediction backend returned an invalid batch length")

        results: list[MessageClassificationResult] = []
        low_count = 0
        for message, category_candidates, topic_candidates in zip(
            eligible,
            category_rows,
            topic_rows,
            strict=True,
        ):
            category = _top_prediction(
                category_candidates,
                category_model,
                self._category_calibrator,
            )
            topic = _top_prediction(
                topic_candidates,
                topic_model,
                self._topic_calibrator,
            )
            reasons: list[str] = []
            if category.score < self._config.category_threshold:
                reasons.append("category_below_threshold")
            if topic.score < self._config.topic_threshold:
                reasons.append("topic_below_threshold")
            low_confidence = bool(reasons)
            if low_confidence:
                low_count += 1
            provenance = {
                "registry_digest": self._registry.digest,
                "config_digest": self._config.digest,
                "device": self._config.device.value,
                "category_model": category_model.to_dict(),
                "topic_model": topic_model.to_dict(),
                "category_calibration": self._category_calibrator.descriptor,
                "topic_calibration": self._topic_calibrator.descriptor,
            }
            results.append(
                MessageClassificationResult(
                    message_key=str(message["message_key"]),
                    category=category,
                    topic=topic,
                    confidence_band=_confidence_band(category.score, self._config),
                    low_confidence=low_confidence,
                    included_for_analysis=not low_confidence,
                    reasons=tuple(reasons),
                    model_provenance=provenance,
                )
            )

        stats = ClassificationStats(
            received=len(ordered),
            classified=len(results),
            skipped=len(ordered) - len(results),
            low_confidence=low_count,
        )
        output_payload = {
            "results": [item.to_dict() for item in results],
            "stats": stats.to_dict(),
            "config_digest": self._config.digest,
        }
        return ClassificationBatchResult(
            results=tuple(results),
            stats=stats,
            config_digest=self._config.digest,
            output_digest=digest_json(output_payload),
        )


__all__ = ["ClassificationEngine", "PredictionBackend"]
