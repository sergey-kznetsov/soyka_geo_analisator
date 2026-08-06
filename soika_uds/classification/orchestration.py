"""Orchestrator handler for classification and topic refinement."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..integration import ContractIssue
from ..orchestration import PermanentStageError, PipelineStage, StageContext, StageResult
from .runtime import ClassificationEngine


@dataclass(frozen=True, slots=True)
class ClassificationStageHandler:
    engine: ClassificationEngine

    def run(self, context: StageContext) -> StageResult:
        if context.stage is not PipelineStage.NLP:
            raise PermanentStageError(
                "INVALID_STAGE_HANDLER",
                "ClassificationStageHandler can only run NLP stage",
                details={"actual_stage": context.stage.value},
            )
        previous = context.previous_outputs.get(PipelineStage.PREPROCESSING.value)
        if not isinstance(previous, Mapping):
            raise PermanentStageError(
                "PREPROCESSING_OUTPUT_MISSING",
                "classification requires completed preprocessing output",
            )
        preprocessing = previous.get("preprocessing")
        if not isinstance(preprocessing, Mapping):
            raise PermanentStageError(
                "INVALID_PREPROCESSING_OUTPUT",
                "preprocessing output must contain preprocessing object",
            )
        messages = preprocessing.get("messages")
        if isinstance(messages, str | bytes | bytearray) or not isinstance(
            messages, Sequence
        ):
            raise PermanentStageError(
                "INVALID_PREPROCESSING_OUTPUT",
                "preprocessing.messages must be an array",
            )
        if not all(isinstance(item, Mapping) for item in messages):
            raise PermanentStageError(
                "INVALID_PREPROCESSING_OUTPUT",
                "preprocessing.messages must contain objects",
            )
        try:
            result = self.engine.classify(tuple(messages))
        except (KeyError, TypeError, ValueError) as error:
            raise PermanentStageError(
                "CLASSIFICATION_FAILED",
                str(error) or type(error).__name__,
            ) from error
        warnings: tuple[ContractIssue, ...] = ()
        if result.stats.low_confidence:
            warnings = (
                ContractIssue(
                    code="CLASSIFICATION_LOW_CONFIDENCE",
                    message="some messages require manual validation",
                    retryable=False,
                    stage=PipelineStage.NLP.value,
                    details={"count": result.stats.low_confidence},
                ),
            )
        return StageResult(
            output={"classification": result.to_dict()},
            processed_items=result.stats.classified,
            total_items=result.stats.received,
            warnings=warnings,
        )


__all__ = ["ClassificationStageHandler"]
