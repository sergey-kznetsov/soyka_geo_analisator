from __future__ import annotations

from datetime import UTC, datetime

import pytest

from soika_uds.contracts import TerritoryContext
from soika_uds.integration import AnalysisRequestV1
from soika_uds.orchestration import PermanentStageError, PipelineStage, StageContext
from soika_uds.scoring import RiskScoringEngine, RiskScoringStageHandler


def _event(event_id: str, level: str, ids: list[str]) -> dict:
    return {
        "event_id": event_id,
        "level": level,
        "object_id": f"object:{level}",
        "message_ids": ids,
        "size": len(ids),
        "category": "ЖКХ",
        "topic": "освещение",
        "keywords": ["фонарь"],
        "representative_message_ids": [ids[0]],
        "started_at": "2026-08-01T10:00:00Z",
        "ended_at": "2026-08-02T10:00:00Z",
        "explanation": {"basis": ["fixture"]},
    }


def _context(*, include_filtering: bool = True) -> StageContext:
    request = AnalysisRequestV1(
        analysis_id="stage12-handler",
        requested_at=datetime(2026, 8, 7, tzinfo=UTC),
        territory=TerritoryContext(
            analysis_id="stage12-handler",
            city="Казань",
            latitude=55.8,
            longitude=49.1,
            radius_meters=1_000,
        ),
    )
    previous_outputs = {
        PipelineStage.EVENTS.value: {
            "events": {
                "events": [
                    _event("evt-a", "building", ["m1", "m2"]),
                    _event("evt-b", "road", ["m2", "m3"]),
                ]
            }
        }
    }
    if include_filtering:
        previous_outputs[PipelineStage.FILTERING.value] = {
            "spatial_filtering": {
                "results": [
                    {
                        "message_key": key,
                        "point": {
                            "type": "Point",
                            "coordinates": [49.1 + index * 0.001, 55.8],
                        },
                    }
                    for index, key in enumerate(("m1", "m2", "m3"))
                ],
                "stats": {"received": 3},
            }
        }
    return StageContext(
        request=request,
        stage=PipelineStage.SCORING,
        attempt=1,
        worker_id="test-worker",
        previous_outputs=previous_outputs,
    )


def test_scoring_handler_builds_json_checkpoint_and_validation_warning() -> None:
    result = RiskScoringStageHandler(RiskScoringEngine()).run(_context())

    payload = result.output["scoring"]
    assert payload["stats"]["events"] == 2
    assert payload["stats"]["connections"] == 1
    assert payload["connections"][0]["shared_message_ids"] == ["m2"]
    assert payload["formula_validation"]["approved"] is False
    assert {warning.code for warning in result.warnings} == {
        "RISK_FORMULA_NOT_EXPERT_VALIDATED"
    }
    assert result.processed_items == 2
    assert result.total_items == 2


def test_scoring_handler_requires_spatial_output_for_geometry() -> None:
    with pytest.raises(PermanentStageError, match="spatial filtering"):
        RiskScoringStageHandler(RiskScoringEngine()).run(
            _context(include_filtering=False)
        )
