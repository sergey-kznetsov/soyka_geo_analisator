"""Local CPU/GPU Transformers backend with immutable model revisions."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from .models import LabelPrediction, ModelDescriptor

PipelineFactory = Callable[..., Any]
TokenizerFactory = Callable[..., Any]
ArtifactVerifier = Callable[[ModelDescriptor], None]


class TransformersPredictionBackend:
    """Load commit-pinned models lazily and perform deterministic batch inference."""

    def __init__(
        self,
        *,
        pipeline_factory: PipelineFactory | None = None,
        tokenizer_factory: TokenizerFactory | None = None,
        artifact_verifier: ArtifactVerifier | None = None,
        local_files_only: bool = True,
    ) -> None:
        if pipeline_factory is None or tokenizer_factory is None:
            from transformers import AutoTokenizer, pipeline

            pipeline_factory = pipeline_factory or pipeline
            tokenizer_factory = tokenizer_factory or AutoTokenizer.from_pretrained
        if not isinstance(local_files_only, bool):
            raise TypeError("local_files_only must be boolean")
        self._pipeline_factory = pipeline_factory
        self._tokenizer_factory = tokenizer_factory
        self._artifact_verifier = artifact_verifier
        self._local_files_only = local_files_only
        self._pipelines: dict[tuple[str, ...], Any] = {}

    def _pipeline(self, model: ModelDescriptor, device: str) -> Any:
        key = (
            model.repo_id,
            model.revision,
            model.weights_sha256,
            model.tokenizer_id,
            model.tokenizer_revision,
            device,
        )
        cached = self._pipelines.get(key)
        if cached is not None:
            return cached
        if self._artifact_verifier is not None:
            self._artifact_verifier(model)
        tokenizer = self._tokenizer_factory(
            model.tokenizer_id,
            revision=model.tokenizer_revision,
            local_files_only=self._local_files_only,
        )
        device_index = 0 if device == "gpu" else -1
        classifier = self._pipeline_factory(
            "text-classification",
            model=model.repo_id,
            revision=model.revision,
            tokenizer=tokenizer,
            device=device_index,
            truncation=True,
            model_kwargs={"local_files_only": self._local_files_only},
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
