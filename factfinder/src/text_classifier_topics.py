"""Classifier of citizens' messages by detailed urban topic."""

from __future__ import annotations

from typing import Any

from ._classifier_base import BaseTextClassifier, PipelineFactory


class TextClassifierTopics(BaseTextClassifier):
    """Classify text into the detailed themes used by SOIKA."""

    DEFAULT_REPOSITORY_ID = "Sandrro/text_to_subfunction_v10"

    def __init__(
        self,
        repository_id: str = DEFAULT_REPOSITORY_ID,
        number_of_categories: int = 1,
        device_type: Any = None,
        *,
        tokenizer_id: str = BaseTextClassifier.DEFAULT_TOKENIZER_ID,
        model_revision: str | None = None,
        pipeline_factory: PipelineFactory | None = None,
        max_length: int = 2048,
    ) -> None:
        super().__init__(
            repository_id=repository_id,
            number_of_categories=number_of_categories,
            device_type=device_type,
            tokenizer_id=tokenizer_id,
            model_revision=model_revision,
            pipeline_factory=pipeline_factory,
            max_length=max_length,
        )
