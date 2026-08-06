"""Orchestrator handler for the geolocation stage."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..integration import ContractIssue
from ..orchestration import (
    PermanentStageError,
    PipelineStage,
    RetryableStageError,
    StageContext,
    StageResult,
)
from .runtime import GeolocationEngine, GeolocationProviderError


@dataclass(frozen=True, slots=True)
class GeolocationStageHandler:
    engine: GeolocationEngine

    def run(self, context: StageContext) -> StageResult:
        if context.stage is not PipelineStage.GEOLOCATION:
            raise PermanentStageError(
                "INVALID_STAGE_HANDLER",
                "GeolocationStageHandler can only run geolocation stage",
                details={"actual_stage": context.stage.value},
            )
        preprocessing_output = context.previous_outputs.get(
            PipelineStage.PREPROCESSING.value
        )
        nlp_output = context.previous_outputs.get(PipelineStage.NLP.value)
        if not isinstance(preprocessing_output, Mapping) or not isinstance(
            nlp_output,
            Mapping,
        ):
            raise PermanentStageError(
                "GEOLOCATION_INPUT_MISSING",
                "geolocation requires preprocessing and NLP outputs",
            )
        preprocessing = preprocessing_output.get("preprocessing")
        classification = nlp_output.get("classification")
        if not isinstance(preprocessing, Mapping) or not isinstance(
            classification,
            Mapping,
        ):
            raise PermanentStageError(
                "INVALID_GEOLOCATION_INPUT",
                "geolocation inputs must contain preprocessing and classification objects",
            )
        messages = preprocessing.get("messages")
        classifications = classification.get("results")
        if (
            isinstance(messages, str | bytes | bytearray)
            or not isinstance(messages, Sequence)
            or isinstance(classifications, str | bytes | bytearray)
            or not isinstance(classifications, Sequence)
        ):
            raise PermanentStageError(
                "INVALID_GEOLOCATION_INPUT",
                "preprocessing messages and classification results must be arrays",
            )
        classification_by_key = {
            item.get("message_key"): item
            for item in classifications
            if isinstance(item, Mapping)
            and isinstance(item.get("message_key"), str)
        }
        engine_messages = []
        for item in messages:
            if not isinstance(item, Mapping):
                raise PermanentStageError(
                    "INVALID_GEOLOCATION_INPUT",
                    "preprocessing messages must contain objects",
                )
            key = item.get("message_key")
            classification_item = classification_by_key.get(key, {})
            engine_messages.append(
                {
                    "message_key": key,
                    "model_text": item.get("model_text"),
                    "included_for_analysis": classification_item.get(
                        "included_for_analysis",
                        False,
                    ),
                }
            )
        try:
            result = self.engine.geolocate(tuple(engine_messages))
        except GeolocationProviderError as error:
            error_type = (
                RetryableStageError if error.retryable else PermanentStageError
            )
            raise error_type(
                "GEOLOCATION_PROVIDER_FAILED",
                str(error),
            ) from error
        except (KeyError, TypeError, ValueError) as error:
            raise PermanentStageError(
                "GEOLOCATION_FAILED",
                str(error),
            ) from error
        warnings: list[ContractIssue] = []
        if result.stats.low_confidence:
            warnings.append(
                ContractIssue(
                    code="GEOLOCATION_LOW_CONFIDENCE",
                    message="some geolocation results require manual validation",
                    retryable=False,
                    stage=PipelineStage.GEOLOCATION.value,
                    details={"count": result.stats.low_confidence},
                )
            )
        if result.stats.unresolved:
            warnings.append(
                ContractIssue(
                    code="GEOLOCATION_UNRESOLVED",
                    message="some messages could not be geolocated",
                    retryable=False,
                    stage=PipelineStage.GEOLOCATION.value,
                    details={"count": result.stats.unresolved},
                )
            )
        return StageResult(
            output={"geolocation": result.to_dict()},
            processed_items=result.stats.processed,
            total_items=result.stats.received,
            warnings=tuple(warnings),
        )
