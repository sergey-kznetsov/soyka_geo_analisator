"""Orchestrator handler for deterministic territory filtering."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..integration import ContractIssue
from ..orchestration import PermanentStageError, PipelineStage, StageContext, StageResult
from .runtime import SpatialFilterEngine


@dataclass(frozen=True, slots=True)
class SpatialFilteringStageHandler:
    engine: SpatialFilterEngine

    def run(self, context: StageContext) -> StageResult:
        if context.stage is not PipelineStage.FILTERING:
            raise PermanentStageError(
                "INVALID_STAGE_HANDLER",
                "SpatialFilteringStageHandler can only run filtering stage",
                details={"actual_stage": context.stage.value},
            )
        geolocation_output = context.previous_outputs.get(PipelineStage.GEOLOCATION.value)
        if not isinstance(geolocation_output, Mapping):
            raise PermanentStageError(
                "SPATIAL_FILTER_INPUT_MISSING",
                "spatial filtering requires geolocation output",
            )
        geolocation = geolocation_output.get("geolocation")
        if not isinstance(geolocation, Mapping):
            raise PermanentStageError(
                "INVALID_SPATIAL_FILTER_INPUT",
                "geolocation output must contain a geolocation object",
            )
        results = geolocation.get("results")
        if isinstance(results, str | bytes | bytearray) or not isinstance(
            results,
            Sequence,
        ):
            raise PermanentStageError(
                "INVALID_SPATIAL_FILTER_INPUT",
                "geolocation results must be an array",
            )
        try:
            filtered = self.engine.filter(
                results,
                territory=context.request.territory,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise PermanentStageError(
                "SPATIAL_FILTER_FAILED",
                str(error),
            ) from error
        warnings: list[ContractIssue] = []
        if filtered.stats.indeterminate:
            warnings.append(
                ContractIssue(
                    code="SPATIAL_FILTER_INDETERMINATE",
                    message="some messages lack exact geometry or a filterable territory",
                    retryable=False,
                    stage=PipelineStage.FILTERING.value,
                    details={"count": filtered.stats.indeterminate},
                )
            )
        return StageResult(
            output={"spatial_filtering": filtered.to_dict()},
            processed_items=filtered.stats.received - filtered.stats.skipped,
            total_items=filtered.stats.received,
            warnings=tuple(warnings),
        )
