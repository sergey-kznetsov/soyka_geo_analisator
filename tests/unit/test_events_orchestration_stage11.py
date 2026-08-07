from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from soika_uds.contracts import TerritoryContext
from soika_uds.events import (
    CosineGraphClusterer,
    EmbeddingBatch,
    EventClusteringConfig,
    EventClusteringEngine,
    EventClusteringStageHandler,
    EventLevel,
    IdentityReductionBackend,
)
from soika_uds.integration import AnalysisRequestV1
from soika_uds.orchestration import PermanentStageError, PipelineStage, StageContext


@dataclass(frozen=True)
class FlatEmbedder:
    def embed(self, texts):
        return EmbeddingBatch(
            tuple((1.0, 0.0) for _ in texts),
            {"component": "fixture-flat"},
        )


def _context(*, include_geolocation: bool = True) -> StageContext:
    request = AnalysisRequestV1(
        analysis_id="stage11-handler",
        requested_at=datetime(2026, 8, 7, tzinfo=UTC),
        territory=TerritoryContext(
            analysis_id="stage11-handler",
            city="Казань",
            latitude=55.8,
            longitude=49.1,
            radius_meters=1_000,
        ),
    )
    keys = ("source:1", "source:2")
    preprocessing = [
        {
            "message_key": key,
            "model_text": "Не работает освещение во дворе",
            "published_at_utc": f"2026-08-0{index}T10:00:00Z",
        }
        for index, key in enumerate(keys, start=1)
    ]
    classification = [
        {
            "message_key": key,
            "category": {"label": "ЖКХ", "score": 0.9},
            "topic": {"label": "освещение", "score": 0.9},
        }
        for key in keys
    ]
    geolocation = [
        {
            "message_key": key,
            "selected_candidate_id": "house-100",
            "candidates": [
                {
                    "candidate_id": "house-100",
                    "kind": "house",
                    "source": "fixture",
                    "osm_type": "way",
                    "osm_id": 100,
                    "address": {"road": "Тестовая улица", "link_id": "link-7"},
                }
            ],
        }
        for key in keys
    ]
    filtering = [
        {
            "message_key": key,
            "included_for_analysis": True,
            "decision": "included",
            "relation": "inside",
            "point": {"type": "Point", "coordinates": [49.1, 55.8]},
        }
        for key in keys
    ]
    previous_outputs = {
        PipelineStage.PREPROCESSING.value: {
            "preprocessing": {"messages": preprocessing}
        },
        PipelineStage.NLP.value: {"classification": {"results": classification}},
        PipelineStage.FILTERING.value: {
            "spatial_filtering": {
                "results": filtering,
                "stats": {"received": 2},
            }
        },
    }
    if include_geolocation:
        previous_outputs[PipelineStage.GEOLOCATION.value] = {
            "geolocation": {"results": geolocation}
        }
    return StageContext(
        request=request,
        stage=PipelineStage.EVENTS,
        attempt=1,
        worker_id="test-worker",
        previous_outputs=previous_outputs,
    )


def _handler() -> EventClusteringStageHandler:
    engine = EventClusteringEngine(
        embedder=FlatEmbedder(),
        reducer=IdentityReductionBackend(),
        clusterer=CosineGraphClusterer(similarity_threshold=0.95),
        config=EventClusteringConfig(
            levels=(
                EventLevel.BUILDING,
                EventLevel.LINK,
                EventLevel.ROAD,
                EventLevel.GLOBAL,
            ),
            min_scope_messages=2,
            min_event_size=2,
        ),
    )
    return EventClusteringStageHandler(engine)


def test_events_stage_joins_structured_upstream_outputs() -> None:
    result = _handler().run(_context())

    payload = result.output["events"]
    assert payload["stats"]["events"] == 4
    assert {item["level"] for item in payload["events"]} == {
        "building",
        "link",
        "road",
        "global",
    }
    assert all(item["message_ids"] == ["source:1", "source:2"] for item in payload["events"])
    assert result.processed_items == 2
    assert result.total_items == 2


def test_events_stage_fails_closed_when_upstream_join_is_incomplete() -> None:
    with pytest.raises(PermanentStageError, match="geolocation"):
        _handler().run(_context(include_geolocation=False))
