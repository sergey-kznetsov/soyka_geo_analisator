from __future__ import annotations

from datetime import UTC, datetime

from soika_uds.contracts import JobStatus, TerritoryContext
from soika_uds.integration import AnalysisRequestV1
from soika_uds.orchestration import PipelineStage, StageContext, stage_job_status
from soika_uds.spatial_filtering import SpatialFilterEngine, SpatialFilteringStageHandler


def _geolocation_result() -> dict:
    return {
        "message_key": "a",
        "included_for_analysis": True,
        "selected_candidate_id": "candidate-a",
        "candidates": [
            {
                "candidate_id": "candidate-a",
                "kind": "house",
                "geometry": {"type": "Point", "coordinates": [49.1, 55.8]},
            }
        ],
        "reasons": [],
    }


def test_filtering_stage_is_between_geolocation_and_events() -> None:
    stages = list(PipelineStage)
    assert stages.index(PipelineStage.GEOLOCATION) < stages.index(PipelineStage.FILTERING)
    assert stages.index(PipelineStage.FILTERING) < stages.index(PipelineStage.EVENTS)
    assert stage_job_status(PipelineStage.FILTERING) is JobStatus.FILTERING


def test_stage_handler_filters_geolocation_output() -> None:
    territory = TerritoryContext(
        analysis_id="stage10-handler",
        city="Казань",
        territory_geojson={
            "type": "Polygon",
            "coordinates": [
                [
                    [49.0, 55.7],
                    [49.2, 55.7],
                    [49.2, 55.9],
                    [49.0, 55.9],
                    [49.0, 55.7],
                ]
            ],
        },
    )
    request = AnalysisRequestV1(
        analysis_id="stage10-handler",
        requested_at=datetime(2026, 8, 6, tzinfo=UTC),
        territory=territory,
    )
    context = StageContext(
        request=request,
        stage=PipelineStage.FILTERING,
        attempt=1,
        worker_id="test-worker",
        previous_outputs={
            PipelineStage.GEOLOCATION.value: {
                "geolocation": {"results": [_geolocation_result()]}
            }
        },
    )
    result = SpatialFilteringStageHandler(SpatialFilterEngine()).run(context)

    payload = result.output["spatial_filtering"]
    assert payload["stats"]["included"] == 1
    assert payload["results"][0]["included_for_analysis"] is True
    assert result.processed_items == 1
