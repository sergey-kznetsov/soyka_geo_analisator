"""Orchestration adapter for the scoring stage."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..events import EventCluster, EventLevel
from ..integration import ContractIssue
from ..orchestration import PermanentStageError, PipelineStage, StageContext, StageResult
from .runtime import RiskScoringEngine


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PermanentStageError("INVALID_SCORING_INPUT", f"{name} must be an object")
    return value


def _sequence(value: object, name: str) -> Sequence[Any]:
    if isinstance(value, str | bytes | bytearray) or not isinstance(value, Sequence):
        raise PermanentStageError("INVALID_SCORING_INPUT", f"{name} must be an array")
    return value


def _event(payload: Mapping[str, Any]) -> EventCluster:
    try:
        return EventCluster(
            event_id=payload.get("event_id"),
            level=EventLevel(payload.get("level")),
            object_id=payload.get("object_id"),
            message_ids=tuple(_sequence(payload.get("message_ids"), "event.message_ids")),
            category=payload.get("category"),
            topic=payload.get("topic"),
            keywords=tuple(_sequence(payload.get("keywords", ()), "event.keywords")),
            representative_message_ids=tuple(
                _sequence(
                    payload.get("representative_message_ids", ()),
                    "event.representative_message_ids",
                )
            ),
            started_at=payload.get("started_at"),
            ended_at=payload.get("ended_at"),
            explanation=_mapping(payload.get("explanation", {}), "event.explanation"),
        )
    except (TypeError, ValueError) as error:
        raise PermanentStageError("INVALID_SCORING_INPUT", str(error)) from error


def _inputs(
    context: StageContext,
) -> tuple[tuple[EventCluster, ...], dict[str, object]]:
    events_output = context.previous_outputs.get(PipelineStage.EVENTS.value)
    if not isinstance(events_output, Mapping):
        raise PermanentStageError(
            "SCORING_INPUT_MISSING",
            "scoring requires completed events output",
        )
    events_payload = _mapping(events_output.get("events"), "events.events")
    event_values = _sequence(events_payload.get("events"), "events.events.events")
    events = tuple(_event(_mapping(item, "event")) for item in event_values)

    filtering_output = context.previous_outputs.get(PipelineStage.FILTERING.value)
    if not isinstance(filtering_output, Mapping):
        raise PermanentStageError(
            "SCORING_INPUT_MISSING",
            "scoring requires completed spatial filtering output for connection geometry",
        )
    spatial = _mapping(filtering_output.get("spatial_filtering"), "filtering.spatial_filtering")
    results = _sequence(spatial.get("results"), "spatial_filtering.results")
    points: dict[str, object] = {}
    for item in results:
        row = _mapping(item, "spatial_filtering.result")
        key = row.get("message_key")
        if not isinstance(key, str) or not key.strip():
            raise PermanentStageError(
                "INVALID_SCORING_INPUT",
                "spatial result message_key must be non-empty",
            )
        if key in points:
            raise PermanentStageError(
                "INVALID_SCORING_INPUT",
                f"duplicate spatial message_key: {key}",
            )
        points[key] = row.get("point")
    stats = _mapping(spatial.get("stats"), "spatial_filtering.stats")
    received = stats.get("received")
    if type(received) is not int or received < 0:
        raise PermanentStageError(
            "INVALID_SCORING_INPUT",
            "spatial_filtering.stats.received must be a non-negative integer",
        )
    return events, points


@dataclass(frozen=True, slots=True)
class RiskScoringStageHandler:
    engine: RiskScoringEngine

    def run(self, context: StageContext) -> StageResult:
        if context.stage is not PipelineStage.SCORING:
            raise PermanentStageError(
                "INVALID_STAGE_HANDLER",
                "RiskScoringStageHandler can only run scoring stage",
                details={"actual_stage": context.stage.value},
            )
        events, points = _inputs(context)
        try:
            result = self.engine.score(events, points)
        except PermanentStageError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise PermanentStageError(
                "RISK_SCORING_FAILED",
                str(error) or type(error).__name__,
            ) from error
        warnings: list[ContractIssue] = []
        if not self.engine.decision_use_approved:
            warnings.append(
                ContractIssue(
                    code="RISK_FORMULA_NOT_EXPERT_VALIDATED",
                    message="risk formula has no matching approved expert validation manifest",
                    retryable=False,
                    stage=PipelineStage.SCORING.value,
                    details={"formula_version": self.engine.config.formula_version},
                )
            )
        if result.stats.unavailable_scores:
            warnings.append(
                ContractIssue(
                    code="RISK_SCORE_DATA_INCOMPLETE",
                    message=(
                        "some event scores are unavailable because required "
                        "observations are missing"
                    ),
                    retryable=False,
                    stage=PipelineStage.SCORING.value,
                    details={"unavailable_scores": result.stats.unavailable_scores},
                )
            )
        return StageResult(
            output={"scoring": result.to_dict()},
            processed_items=len(events),
            total_items=len(events),
            warnings=tuple(warnings),
        )


__all__ = ["RiskScoringStageHandler"]
