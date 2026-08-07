"""Orchestrator handler for reproducible event clustering."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..integration import ContractIssue
from ..orchestration import PermanentStageError, PipelineStage, StageContext, StageResult
from .models import EventLevel, EventMessage, ScopeStatus
from .runtime import EventClusteringEngine

_SPACE_RE = re.compile(r"\s+")
_BUILDING_KINDS = frozenset({"house", "poi", "landmark"})
_ROAD_KEYS = (
    "road",
    "pedestrian",
    "residential",
    "footway",
    "path",
    "cycleway",
)
_LINK_KEYS = ("link_id", "road_segment_id", "osm_link_id")


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PermanentStageError(
            "INVALID_EVENT_INPUT",
            f"{field_name} must be an object",
        )
    return value


def _sequence(value: object, field_name: str) -> Sequence[Any]:
    if isinstance(value, str | bytes | bytearray) or not isinstance(value, Sequence):
        raise PermanentStageError(
            "INVALID_EVENT_INPUT",
            f"{field_name} must be an array",
        )
    return value


def _results(
    previous_outputs: Mapping[str, Mapping[str, Any]],
    stage: PipelineStage,
    object_name: str,
) -> tuple[Mapping[str, Any], ...]:
    stage_output = previous_outputs.get(stage.value)
    if not isinstance(stage_output, Mapping):
        raise PermanentStageError(
            "EVENT_INPUT_MISSING",
            f"events require completed {stage.value} output",
        )
    payload = _mapping(stage_output.get(object_name), f"{stage.value}.{object_name}")
    raw_values = (
        payload.get("results") if "results" in payload else payload.get("messages")
    )
    values = _sequence(raw_values, object_name)
    if not all(isinstance(item, Mapping) for item in values):
        raise PermanentStageError(
            "INVALID_EVENT_INPUT",
            f"{object_name} values must contain objects",
        )
    return tuple(values)


def _index(
    values: Sequence[Mapping[str, Any]],
    *,
    field_name: str = "message_key",
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for item in values:
        key = item.get(field_name)
        if not isinstance(key, str) or not key.strip():
            raise PermanentStageError(
                "INVALID_EVENT_INPUT",
                f"{field_name} must be non-empty",
            )
        if key in result:
            raise PermanentStageError(
                "INVALID_EVENT_INPUT",
                f"duplicate {field_name}: {key}",
            )
        result[key] = item
    return result


def _selected_candidate(geolocation: Mapping[str, Any]) -> Mapping[str, Any] | None:
    selected_id = geolocation.get("selected_candidate_id")
    if selected_id is None:
        return None
    candidates = _sequence(geolocation.get("candidates", ()), "geolocation.candidates")
    for candidate in candidates:
        candidate_mapping = _mapping(candidate, "geolocation.candidate")
        if candidate_mapping.get("candidate_id") == selected_id:
            return candidate_mapping
    raise PermanentStageError(
        "INVALID_EVENT_INPUT",
        "selected geolocation candidate is absent from candidates",
    )


def _normalized_identifier(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if not isinstance(value, str):
        return None
    normalized = _SPACE_RE.sub(" ", value.strip()).casefold()
    return normalized or None


def _road_name(candidate: Mapping[str, Any], geolocation: Mapping[str, Any]) -> str | None:
    address = candidate.get("address")
    if isinstance(address, Mapping):
        for key in _ROAD_KEYS:
            normalized = _normalized_identifier(address.get(key))
            if normalized:
                return normalized
    mention = geolocation.get("mention")
    if isinstance(mention, Mapping):
        normalized = _normalized_identifier(mention.get("street"))
        if normalized:
            return normalized
    return None


def _link_id(candidate: Mapping[str, Any]) -> str | None:
    address = candidate.get("address")
    if not isinstance(address, Mapping):
        return None
    for key in _LINK_KEYS:
        normalized = _normalized_identifier(address.get(key))
        if normalized:
            return normalized
    return None


def _scopes(
    geolocation: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    city: str | None,
) -> dict[str, str]:
    scopes = {EventLevel.GLOBAL.value: "global"}
    candidate_id = candidate.get("candidate_id")
    kind = candidate.get("kind")
    if kind in _BUILDING_KINDS and isinstance(candidate_id, str) and candidate_id.strip():
        osm_type = candidate.get("osm_type")
        osm_id = candidate.get("osm_id")
        if (
            isinstance(osm_type, str)
            and isinstance(osm_id, int)
            and not isinstance(osm_id, bool)
        ):
            scopes[EventLevel.BUILDING.value] = f"osm:{osm_type.casefold()}:{osm_id}"
        else:
            scopes[EventLevel.BUILDING.value] = f"candidate:{candidate_id.strip()}"
    link_id = _link_id(candidate)
    if link_id:
        scopes[EventLevel.LINK.value] = f"link:{link_id}"
    road = _road_name(candidate, geolocation)
    if road:
        city_part = _normalized_identifier(city) or "unknown-city"
        scopes[EventLevel.ROAD.value] = f"road:{city_part}:{road}"
    return scopes


def event_messages_from_previous_outputs(
    context: StageContext,
) -> tuple[EventMessage, ...]:
    preprocessing_values = _results(
        context.previous_outputs,
        PipelineStage.PREPROCESSING,
        "preprocessing",
    )
    classification_values = _results(
        context.previous_outputs,
        PipelineStage.NLP,
        "classification",
    )
    geolocation_values = _results(
        context.previous_outputs,
        PipelineStage.GEOLOCATION,
        "geolocation",
    )
    filtering_values = _results(
        context.previous_outputs,
        PipelineStage.FILTERING,
        "spatial_filtering",
    )
    preprocessing = _index(preprocessing_values)
    classification = _index(classification_values)
    geolocation = _index(geolocation_values)

    messages: list[EventMessage] = []
    for spatial in sorted(filtering_values, key=lambda item: str(item.get("message_key", ""))):
        key = spatial.get("message_key")
        if not isinstance(key, str) or not key.strip():
            raise PermanentStageError(
                "INVALID_EVENT_INPUT",
                "spatial filtering message_key must be non-empty",
            )
        if spatial.get("included_for_analysis") is not True:
            continue
        try:
            preprocessed = preprocessing[key]
            classified = classification[key]
            located = geolocation[key]
        except KeyError as error:
            raise PermanentStageError(
                "EVENT_INPUT_JOIN_FAILED",
                f"message {key} is missing from an upstream stage",
            ) from error
        model_text = preprocessed.get("model_text")
        if not isinstance(model_text, str) or not model_text.strip():
            raise PermanentStageError(
                "INVALID_EVENT_INPUT",
                f"message {key} has no model_text",
            )
        candidate = _selected_candidate(located)
        if candidate is None:
            raise PermanentStageError(
                "EVENT_INPUT_JOIN_FAILED",
                f"included message {key} has no selected geolocation candidate",
            )
        category_payload = _mapping(classified.get("category"), "classification.category")
        topic_payload = _mapping(classified.get("topic"), "classification.topic")
        point = spatial.get("point")
        if point is not None and not isinstance(point, Mapping):
            raise PermanentStageError(
                "INVALID_EVENT_INPUT",
                f"message {key} point must be GeoJSON object or null",
            )
        messages.append(
            EventMessage(
                message_key=key,
                model_text=model_text,
                published_at_utc=preprocessed.get("published_at_utc"),
                category=category_payload.get("label"),
                topic=topic_payload.get("label"),
                point=point,
                scopes=_scopes(
                    located,
                    candidate,
                    city=context.request.territory.city,
                ),
                provenance={
                    "selected_candidate_id": candidate.get("candidate_id"),
                    "candidate_source": candidate.get("source"),
                    "osm_type": candidate.get("osm_type"),
                    "osm_id": candidate.get("osm_id"),
                    "spatial_decision": spatial.get("decision"),
                    "spatial_relation": spatial.get("relation"),
                },
            )
        )
    return tuple(messages)


@dataclass(frozen=True, slots=True)
class EventClusteringStageHandler:
    engine: EventClusteringEngine

    def run(self, context: StageContext) -> StageResult:
        if context.stage is not PipelineStage.EVENTS:
            raise PermanentStageError(
                "INVALID_STAGE_HANDLER",
                "EventClusteringStageHandler can only run events stage",
                details={"actual_stage": context.stage.value},
            )
        filtering_output = context.previous_outputs.get(PipelineStage.FILTERING.value)
        if not isinstance(filtering_output, Mapping):
            raise PermanentStageError(
                "EVENT_INPUT_MISSING",
                "events require completed filtering output",
            )
        spatial = _mapping(
            filtering_output.get("spatial_filtering"),
            "filtering.spatial_filtering",
        )
        stats = _mapping(spatial.get("stats"), "spatial_filtering.stats")
        total_items = stats.get("received")
        if type(total_items) is not int or total_items < 0:
            raise PermanentStageError(
                "INVALID_EVENT_INPUT",
                "spatial_filtering.stats.received must be non-negative integer",
            )
        try:
            messages = event_messages_from_previous_outputs(context)
            result = self.engine.cluster(messages)
        except PermanentStageError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise PermanentStageError(
                "EVENT_CLUSTERING_FAILED",
                str(error) or type(error).__name__,
            ) from error

        warnings: list[ContractIssue] = []
        if not result.events and messages:
            warnings.append(
                ContractIssue(
                    code="EVENTS_NONE_DETECTED",
                    message="eligible messages did not form an event cluster",
                    retryable=False,
                    stage=PipelineStage.EVENTS.value,
                    details={"eligible_messages": len(messages)},
                )
            )
        unavailable = sum(
            item.status is ScopeStatus.UNAVAILABLE for item in result.diagnostics
        )
        if unavailable:
            warnings.append(
                ContractIssue(
                    code="EVENT_SCOPE_UNAVAILABLE",
                    message="some event levels lack an explicit spatial object identifier",
                    retryable=False,
                    stage=PipelineStage.EVENTS.value,
                    details={"scope_diagnostics": unavailable},
                )
            )
        return StageResult(
            output={"events": result.to_dict()},
            processed_items=len(messages),
            total_items=total_items,
            warnings=tuple(warnings),
        )


__all__ = ["EventClusteringStageHandler", "event_messages_from_previous_outputs"]
