"""Shared implementation for the imported SOIKA text classifiers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from soika_uds.prediction import Prediction, normalize_pipeline_output, to_legacy_pair

PipelineFactory = Callable[..., Any]


class BaseTextClassifier:
    """Lazy, testable wrapper around a Hugging Face text-classification pipeline."""

    DEFAULT_TOKENIZER_ID = "cointegrated/rubert-tiny2"

    def __init__(
        self,
        repository_id: str,
        number_of_categories: int = 1,
        device_type: Any = None,
        *,
        tokenizer_id: str = DEFAULT_TOKENIZER_ID,
        model_revision: str | None = None,
        pipeline_factory: PipelineFactory | None = None,
        max_length: int = 2048,
    ) -> None:
        repository_id = repository_id.strip()
        tokenizer_id = tokenizer_id.strip()
        if not repository_id:
            raise ValueError("repository_id must not be empty")
        if not tokenizer_id:
            raise ValueError("tokenizer_id must not be empty")
        if number_of_categories < 1:
            raise ValueError("number_of_categories must be a positive integer")
        if max_length < 1:
            raise ValueError("max_length must be a positive integer")

        if pipeline_factory is None:
            from transformers import pipeline

            pipeline_factory = pipeline

        self.REP_ID = repository_id
        self.CATS_NUM = int(number_of_categories)
        self.TOKENIZER_ID = tokenizer_id
        self.MODEL_REVISION = model_revision
        self.MAX_LENGTH = int(max_length)

        pipeline_kwargs: dict[str, Any] = {
            "model": self.REP_ID,
            "tokenizer": self.TOKENIZER_ID,
            "max_length": self.MAX_LENGTH,
            "truncation": True,
        }
        if device_type is not None:
            pipeline_kwargs["device"] = device_type
        if model_revision is not None:
            pipeline_kwargs["revision"] = model_revision

        self.classifier = pipeline_factory("text-classification", **pipeline_kwargs)

    def predict(self, text: str) -> list[Prediction]:
        """Return typed predictions while preserving model candidate order."""

        if not isinstance(text, str):
            raise TypeError("text must be a string")
        text = text.strip()
        if not text:
            raise ValueError("text must not be empty")

        raw = self.classifier(text, top_k=self.CATS_NUM)
        return normalize_pipeline_output(raw, limit=self.CATS_NUM)

    def run(self, text: str) -> list[str]:
        """Keep the historical SOIKA return format for existing notebooks."""

        return to_legacy_pair(self.predict(text))
