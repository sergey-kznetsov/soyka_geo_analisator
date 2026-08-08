"""Collection routing for discovered sources with explicit per-source outcomes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC
from types import MappingProxyType
from typing import Any, Protocol

from ..contracts import SourceMessage
from ..integration import ContractIssue
from ..orchestration import PermanentStageError, PipelineStage, StageContext, StageResult
from .models import (
    GeoScope,
    SourceCandidate,
    SourceKind,
    SourceOutcome,
    SourceReasonCode,
    SourceState,
)


class CandidateCollectionError(RuntimeError):
    def __init__(
        self,
        code: SourceReasonCode,
        message: str,
        *,
        state: SourceState = SourceState.UNAVAILABLE,
        retryable: bool = False,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.state = state
        self.retryable = retryable
        self.details = dict(details or {})


@dataclass(frozen=True, slots=True)
class CandidateCollectionResult:
    messages: tuple[SourceMessage, ...]
    outcome: SourceOutcome

    def __post_init__(self) -> None:
        messages = tuple(self.messages)
        if not all(isinstance(item, SourceMessage) for item in messages):
            raise TypeError("messages must contain SourceMessage values")
        object.__setattr__(self, "messages", messages)
        if not isinstance(self.outcome, SourceOutcome):
            raise TypeError("outcome must be SourceOutcome")
        if self.outcome.messages_collected != len(messages):
            raise ValueError("outcome.messages_collected must equal emitted message count")


class CandidateCollector(Protocol):
    source_kind: SourceKind

    def collect(
        self,
        candidate: SourceCandidate,
        scope: GeoScope,
    ) -> CandidateCollectionResult: ...


@dataclass(frozen=True, slots=True)
class CollectorRouter:
    collectors: Mapping[SourceKind, CandidateCollector] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized: dict[SourceKind, CandidateCollector] = {}
        for key, collector in self.collectors.items():
            kind = key if isinstance(key, SourceKind) else SourceKind(key)
            if collector.source_kind is not kind:
                raise ValueError("collector key must equal collector.source_kind")
            normalized[kind] = collector
        object.__setattr__(self, "collectors", MappingProxyType(normalized))

    def collect(
        self,
        candidate: SourceCandidate,
        scope: GeoScope,
    ) -> CandidateCollectionResult:
        collector = self.collectors.get(candidate.kind)
        if collector is None:
            return CandidateCollectionResult(
                messages=(),
                outcome=SourceOutcome(
                    source_id=candidate.candidate_id,
                    kind=candidate.kind,
                    state=SourceState.CONFIGURATION_MISSING,
                    reason_code=SourceReasonCode.SOURCE_CONFIGURATION_MISSING,
                    reason=f"collector for {candidate.kind.value} is not configured",
                    attempted_urls=(candidate.url,),
                ),
            )
        try:
            return collector.collect(candidate, scope)
        except CandidateCollectionError as error:
            return CandidateCollectionResult(
                messages=(),
                outcome=SourceOutcome(
                    source_id=candidate.candidate_id,
                    kind=candidate.kind,
                    state=error.state,
                    reason_code=error.code,
                    reason=str(error),
                    attempted_urls=(candidate.url,),
                    details={**error.details, "retryable": error.retryable},
                ),
            )


def _scope_from_mapping(payload: Mapping[str, Any]) -> GeoScope:
    point = payload.get("point")
    if not isinstance(point, Mapping):
        raise ValueError("territory_context.point must be an object")
    return GeoScope(
        raw_address=payload.get("raw_address"),
        city=payload.get("city"),
        region=payload.get("region"),
        district=payload.get("district"),
        street=payload.get("street"),
        house_number=payload.get("house_number"),
        longitude=point.get("longitude"),
        latitude=point.get("latitude"),
        precision=payload.get("precision"),
        confidence=payload.get("confidence"),
        candidate_id=payload.get("candidate_id"),
        label=payload.get("label"),
        osm_type=payload.get("osm_type"),
        osm_id=payload.get("osm_id"),
        aliases=tuple(payload.get("aliases", ())),
        metadata=payload.get("metadata", {}),
    )


def _candidate_from_mapping(payload: Mapping[str, Any]) -> SourceCandidate:
    return SourceCandidate(
        candidate_id=payload.get("candidate_id"),
        kind=SourceKind(payload.get("kind")),
        url=payload.get("url"),
        domain=payload.get("domain"),
        title=payload.get("title"),
        discovered_by=payload.get("discovered_by"),
        query=payload.get("query"),
        geo_evidence=tuple(payload.get("geo_evidence", ())),
        active=payload.get("active", True),
        metadata=payload.get("metadata", {}),
    )


def source_message_document(message: SourceMessage) -> dict[str, Any]:
    published = message.published_at
    if published.tzinfo is None or published.utcoffset() is None:
        raise ValueError("SourceMessage.published_at must include a UTC offset")
    payload: dict[str, Any] = {
        "source": message.source,
        "external_id": message.external_id,
        "text": message.text,
        "published_at": published.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "url": message.url,
        "author_id": message.author_id,
        "latitude": message.latitude,
        "longitude": message.longitude,
        "metadata": dict(message.metadata),
    }
    return payload


@dataclass(frozen=True, slots=True)
class DiscoveryCollectionStageHandler:
    router: CollectorRouter

    def run(self, context: StageContext) -> StageResult:
        if context.stage is not PipelineStage.COLLECTION:
            raise PermanentStageError(
                "INVALID_STAGE_HANDLER",
                "DiscoveryCollectionStageHandler can only run collection stage",
                details={"actual_stage": context.stage.value},
            )
        preparing = context.previous_outputs.get(PipelineStage.PREPARING.value)
        if not isinstance(preparing, Mapping):
            raise PermanentStageError(
                "DISCOVERY_PLAN_MISSING",
                "collection requires completed geo-first preparing output",
            )
        scope_payload = preparing.get("territory_context")
        plan_payload = preparing.get("discovery_plan")
        if not isinstance(scope_payload, Mapping) or not isinstance(plan_payload, Mapping):
            raise PermanentStageError(
                "DISCOVERY_PLAN_MISSING",
                "preparing output must contain territory_context and discovery_plan",
            )
        raw_candidates = plan_payload.get("candidates", [])
        if isinstance(raw_candidates, str | bytes | bytearray) or not isinstance(
            raw_candidates,
            Sequence,
        ):
            raise PermanentStageError(
                "INVALID_DISCOVERY_PLAN",
                "discovery_plan.candidates must be an array",
            )
        try:
            scope = _scope_from_mapping(scope_payload)
            candidates = tuple(
                _candidate_from_mapping(item)
                for item in raw_candidates
                if isinstance(item, Mapping) and item.get("active") is True
            )
        except (TypeError, ValueError) as error:
            raise PermanentStageError(
                "INVALID_DISCOVERY_PLAN",
                str(error),
            ) from error

        messages: list[SourceMessage] = []
        outcomes: list[SourceOutcome] = []
        for candidate in candidates:
            result = self.router.collect(candidate, scope)
            messages.extend(result.messages)
            outcomes.append(result.outcome)

        unavailable = [
            item
            for item in outcomes
            if item.state
            in {
                SourceState.UNAVAILABLE,
                SourceState.BLOCKED,
                SourceState.AUTH_REQUIRED,
                SourceState.CONFIGURATION_MISSING,
                SourceState.FAILED,
            }
        ]
        warnings: list[ContractIssue] = []
        if unavailable:
            warnings.append(
                ContractIssue(
                    code="COLLECTION_SOURCES_UNAVAILABLE",
                    message="some discovered sources could not be collected",
                    retryable=False,
                    stage=PipelineStage.COLLECTION.value,
                    details={
                        "count": len(unavailable),
                        "sources": [
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
        relevant = sum(item.relevant_messages for item in outcomes)
        output = {
            "messages": [source_message_document(item) for item in messages],
            "source_coverage": [item.to_dict() for item in outcomes],
            "coverage": {
                "sources_discovered": len(candidates),
                "sources_collected": sum(
                    item.state in {SourceState.COLLECTED, SourceState.PARTIAL}
                    for item in outcomes
                ),
                "sources_unavailable": len(unavailable),
                "sources_no_relevant_results": sum(
                    item.state is SourceState.NO_RELEVANT_RESULTS for item in outcomes
                ),
                "messages_collected": len(messages),
                "messages_relevant": relevant,
            },
        }
        return StageResult(
            output=output,
            processed_items=len(candidates),
            total_items=len(candidates),
            warnings=tuple(warnings),
        )


__all__ = [
    "CandidateCollectionError",
    "CandidateCollectionResult",
    "CandidateCollector",
    "CollectorRouter",
    "DiscoveryCollectionStageHandler",
    "source_message_document",
]
