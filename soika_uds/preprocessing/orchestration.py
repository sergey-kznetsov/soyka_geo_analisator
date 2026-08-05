"""Orchestrator stage handler for deterministic preprocessing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..integration import ContractIssue
from ..orchestration import (
    PermanentStageError,
    PipelineStage,
    StageContext,
    StageResult,
)
from .models import PreprocessingConfig
from .pipeline import preprocess_messages, source_message_from_dict


def _message_documents(collection: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    messages = collection.get("messages")
    if messages is not None:
        if isinstance(messages, str | bytes | bytearray) or not isinstance(
            messages, Sequence
        ):
            raise PermanentStageError(
                "INVALID_COLLECTION_OUTPUT",
                "collection.messages must be an array",
            )
        if not all(isinstance(item, Mapping) for item in messages):
            raise PermanentStageError(
                "INVALID_COLLECTION_OUTPUT",
                "collection.messages must contain objects",
            )
        return tuple(messages)

    sources = collection.get("sources")
    if isinstance(sources, Mapping):
        flattened: list[Mapping[str, Any]] = []
        for source_id in sorted(sources):
            source_result = sources[source_id]
            if not isinstance(source_result, Mapping):
                raise PermanentStageError(
                    "INVALID_COLLECTION_OUTPUT",
                    f"collection.sources.{source_id} must be an object",
                )
            source_messages = source_result.get("messages", [])
            if isinstance(source_messages, str | bytes | bytearray) or not isinstance(
                source_messages, Sequence
            ):
                raise PermanentStageError(
                    "INVALID_COLLECTION_OUTPUT",
                    f"collection.sources.{source_id}.messages must be an array",
                )
            if not all(isinstance(item, Mapping) for item in source_messages):
                raise PermanentStageError(
                    "INVALID_COLLECTION_OUTPUT",
                    f"collection.sources.{source_id}.messages must contain objects",
                )
            flattened.extend(source_messages)
        return tuple(flattened)

    raise PermanentStageError(
        "COLLECTION_MESSAGES_MISSING",
        "collection stage output must contain messages or source results",
    )


@dataclass(frozen=True, slots=True)
class PreprocessingStageHandler:
    """Read collection output and persist a JSON-only preprocessing result."""

    config: PreprocessingConfig = field(default_factory=PreprocessingConfig)

    def run(self, context: StageContext) -> StageResult:
        if context.stage is not PipelineStage.PREPROCESSING:
            raise PermanentStageError(
                "INVALID_STAGE_HANDLER",
                "PreprocessingStageHandler can only run preprocessing stage",
                details={"actual_stage": context.stage.value},
            )
        collection = context.previous_outputs.get(PipelineStage.COLLECTION.value)
        if not isinstance(collection, Mapping):
            raise PermanentStageError(
                "COLLECTION_OUTPUT_MISSING",
                "preprocessing requires completed collection output",
            )
        documents = _message_documents(collection)
        try:
            messages = tuple(source_message_from_dict(item) for item in documents)
        except (KeyError, TypeError, ValueError) as error:
            raise PermanentStageError(
                "INVALID_SOURCE_MESSAGE",
                str(error) or type(error).__name__,
            ) from error
        result = preprocess_messages(messages, self.config)
        warnings: tuple[ContractIssue, ...] = ()
        if result.stats.rejected:
            warnings = (
                ContractIssue(
                    code="PREPROCESSING_MESSAGES_REJECTED",
                    message=(
                        "some collected messages were rejected during preprocessing"
                    ),
                    retryable=False,
                    stage=PipelineStage.PREPROCESSING.value,
                    details={"count": result.stats.rejected},
                ),
            )
        return StageResult(
            output={"preprocessing": result.to_dict()},
            processed_items=result.stats.received,
            total_items=result.stats.received,
            warnings=warnings,
        )


__all__ = ["PreprocessingStageHandler"]
