"""Local CPU/GPU Transformers backend with immutable model revisions."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from .models import LabelPrediction, ModelDescriptor

PipelineFactory = Callable[..., Any]
TokenizerFactory = Callable[..., Any]


class TransformersPredictionBackend:
    """Load pinned local or Hugging Face models lazily and perform batch inference."""

    def __init__(
        self,
        *,
        pipeline_factory: PipelineFactory | None = None,
        tokenizer_factory: TokenizerFactory | None = None,
    ) -> None:
        if pipeline_factory is None or tokenizer_factory is None:
            from transformers import AutoTokenizer, pipeline

            pipeline_factory = pipeline_factory or pipeline
            tokenizer_factory = tokenizer_factory or AutoTokenizer.from_pretrained
        self._pipeline_factory = pipeline_factory
        self._tokenizer_factory = tokenizer_factory
        self._pipelines: dict[tuple[str, str, str], Any] = {}

    def _pipeline(self, model: ModelDescriptor, device: str) -> Any:
        key = (model.repo_id, model.revision, device)
        cached = self._pipelines.get(key)
        if cached is not None:
            return cached
        tokenizer = self._tokenizer_factory(
            model.tokenizer_id,
            revision=model.tokenizer_revision,
        )
        device_index = 0 if device == "gpu" else -1
        classifier = self._pipeline_factory(
            "text-classification",
            model=model.repo_id,
            revision=model.revision,
            tokenizer=tokenizer,
            device=device_index,
            truncation=True,
        )
        self._pipelines[key] = classifier
        return classifier

    def predict_batch(
        self,
        texts: Sequence[str],
        *,
        model: ModelDescriptor,
        batch_size: int,
        device: str,
    ) -> Sequence[Sequence[LabelPrediction]]:
        if not texts:
            return ()
        classifier = self._pipeline(model, device)
        raw_rows = classifier(
            list(texts),
            batch_size=batch_size,
            top_k=None,
            truncation=True,
        )
        normalized: list[tuple[LabelPrediction, ...]] = []
        for row in raw_rows:
            if isinstance(row, dict):
                row = [row]
            normalized.append(
                tuple(
                    LabelPrediction(
                        label=str(candidate["label"]),
                        score=float(candidate["score"]),
                    )
                    for candidate in row
                )
            )
        return tuple(normalized)


__all__ = ["TransformersPredictionBackend"]
