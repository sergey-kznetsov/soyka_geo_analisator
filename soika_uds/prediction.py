"""Prediction normalization shared by the product and legacy adapters."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Any


class PredictionFormatError(ValueError):
    """Raised when a model returns an unsupported prediction payload."""


@dataclass(frozen=True, slots=True)
class Prediction:
    """One classifier candidate with a normalized probability score."""

    label: str
    score: float

    def __post_init__(self) -> None:
        label = self.label.strip()
        if not label:
            raise ValueError("prediction label must not be empty")
        if not isfinite(self.score) or not 0.0 <= self.score <= 1.0:
            raise ValueError("prediction score must be a finite number in [0, 1]")
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "score", float(self.score))


def _unwrap_pipeline_payload(payload: Any) -> Sequence[Mapping[str, Any]]:
    """Convert common Hugging Face pipeline output shapes to a flat sequence."""

    if isinstance(payload, Mapping):
        return [payload]

    if not isinstance(payload, Sequence) or isinstance(payload, str | bytes):
        raise PredictionFormatError(
            f"unsupported classifier output type: {type(payload).__name__}"
        )

    if not payload:
        raise PredictionFormatError("classifier returned an empty result")

    first = payload[0]
    if isinstance(first, Mapping):
        return payload  # type: ignore[return-value]

    if (
        len(payload) == 1
        and isinstance(first, Sequence)
        and not isinstance(first, str | bytes)
        and all(isinstance(item, Mapping) for item in first)
    ):
        return first  # type: ignore[return-value]

    raise PredictionFormatError("unsupported nested classifier output")


def normalize_pipeline_output(
    payload: Any, *, limit: int | None = None
) -> list[Prediction]:
    """Normalize a transformer pipeline result without changing candidate order."""

    if limit is not None and limit < 1:
        raise ValueError("limit must be a positive integer")

    raw_items = _unwrap_pipeline_payload(payload)
    predictions: list[Prediction] = []

    for item in raw_items:
        try:
            label = item["label"]
            score = item["score"]
        except KeyError as exc:
            raise PredictionFormatError(
                f"classifier item is missing required key: {exc.args[0]}"
            ) from exc

        if not isinstance(label, str):
            raise PredictionFormatError("classifier label must be a string")

        try:
            numeric_score = float(score)
        except (TypeError, ValueError) as exc:
            raise PredictionFormatError("classifier score must be numeric") from exc

        predictions.append(Prediction(label=label, score=numeric_score))

    if limit is not None:
        predictions = predictions[:limit]

    if not predictions:
        raise PredictionFormatError("classifier returned no usable predictions")

    return predictions


def to_legacy_pair(predictions: Iterable[Prediction]) -> list[str]:
    """Return the historical ``[labels, scores]`` representation."""

    items = list(predictions)
    if not items:
        raise PredictionFormatError("cannot format an empty prediction sequence")

    labels = "; ".join(item.label for item in items)
    scores = "; ".join(str(round(item.score, 3)) for item in items)
    return [labels, scores]
