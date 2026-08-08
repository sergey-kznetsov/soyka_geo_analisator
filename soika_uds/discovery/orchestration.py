"""Preparing-stage handler that resolves geography before source discovery."""

from __future__ import annotations

from dataclasses import dataclass

from ..integration import ContractIssue
from ..orchestration import PermanentStageError, PipelineStage, StageContext, StageResult
from .engine import DiscoveryEngine
from .models import SourceState
from .territory import TerritoryResolutionError, TerritoryResolver


@dataclass(frozen=True, slots=True)
class GeoDiscoveryPreparingHandler:
    resolver: TerritoryResolver
    discovery: DiscoveryEngine

    def run(self, context: StageContext) -> StageResult:
        if context.stage is not PipelineStage.PREPARING:
            raise PermanentStageError(
                "INVALID_STAGE_HANDLER",
                "GeoDiscoveryPreparingHandler can only run preparing stage",
                details={"actual_stage": context.stage.value},
            )
        try:
            scope = self.resolver.resolve(context.request.territory)
        except TerritoryResolutionError as error:
            raise PermanentStageError(
                error.code.value,
                str(error),
                details={"address": context.request.territory.address},
            ) from error

        plan = self.discovery.plan(scope)
        warnings: list[ContractIssue] = []
        unavailable = [
            item
            for item in plan.outcomes
            if item.state
            in {
                SourceState.UNAVAILABLE,
                SourceState.AUTH_REQUIRED,
                SourceState.CONFIGURATION_MISSING,
                SourceState.FAILED,
            }
        ]
        if unavailable:
            warnings.append(
                ContractIssue(
                    code="DISCOVERY_SOURCE_UNAVAILABLE",
                    message="one or more discovery dependencies are unavailable",
                    retryable=False,
                    stage=PipelineStage.PREPARING.value,
                    details={
                        "count": len(unavailable),
                        "reasons": [
                            {
                                "source_id": item.source_id,
                                "reason_code": item.reason_code.value,
                                "reason": item.reason,
                            }
                            for item in unavailable
                        ],
                    },
                )
            )
        if not plan.active_candidates:
            warnings.append(
                ContractIssue(
                    code="DISCOVERY_NO_ACTIVE_SOURCES",
                    message="geo-first discovery produced no active source candidates",
                    retryable=False,
                    stage=PipelineStage.PREPARING.value,
                    details={
                        "city": plan.scope.city,
                        "region": plan.scope.region,
                        "queries": len(plan.queries),
                    },
                )
            )
        return StageResult(
            output={
                "territory_context": plan.scope.to_dict(),
                "discovery_plan": plan.to_dict(),
            },
            processed_items=len(plan.active_candidates),
            total_items=len(plan.candidates),
            warnings=tuple(warnings),
        )


__all__ = ["GeoDiscoveryPreparingHandler"]
