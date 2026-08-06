from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from soika_uds.contracts import TerritoryContext
from soika_uds.integration import AnalysisRequestV1
from soika_uds.orchestration import (
    PIPELINE_STAGES,
    CheckpointState,
    FileJobStore,
    JobRecord,
    PipelineStage,
)


def _legacy_payload(*, downstream_started: bool) -> dict:
    request = AnalysisRequestV1(
        analysis_id="legacy-stage10-job",
        requested_at=datetime(2026, 8, 6, tzinfo=UTC),
        territory=TerritoryContext(
            analysis_id="legacy-stage10-job",
            city="Казань",
            latitude=55.8,
            longitude=49.1,
            radius_meters=1_000,
        ),
    )
    record = JobRecord.new(request, datetime(2026, 8, 6, tzinfo=UTC))
    payload = record.to_dict()
    payload["revision"] = 4
    payload["checkpoints"] = [
        checkpoint
        for checkpoint in payload["checkpoints"]
        if checkpoint["stage"] != PipelineStage.FILTERING.value
    ]
    if downstream_started:
        events = next(
            checkpoint
            for checkpoint in payload["checkpoints"]
            if checkpoint["stage"] == PipelineStage.EVENTS.value
        )
        events["state"] = CheckpointState.COMPLETED.value
        events["output"] = {"events": []}
    return payload


def _write_legacy_record(root: Path, payload: dict) -> None:
    analysis_id = payload["analysis_id"]
    file_name = hashlib.sha256(analysis_id.encode("utf-8")).hexdigest() + ".json"
    (root / file_name).write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def test_legacy_pending_job_receives_pending_filtering_checkpoint(tmp_path: Path) -> None:
    payload = _legacy_payload(downstream_started=False)
    _write_legacy_record(tmp_path, payload)

    loaded = FileJobStore(tmp_path).load(payload["analysis_id"])

    assert tuple(checkpoint.stage for checkpoint in loaded.checkpoints) == PIPELINE_STAGES
    filtering = loaded.checkpoint(PipelineStage.FILTERING)
    assert filtering.state is CheckpointState.PENDING
    assert filtering.output == {}


def test_legacy_downstream_job_receives_completed_bypass(tmp_path: Path) -> None:
    payload = _legacy_payload(downstream_started=True)
    _write_legacy_record(tmp_path, payload)

    loaded = FileJobStore(tmp_path).load(payload["analysis_id"])

    filtering = loaded.checkpoint(PipelineStage.FILTERING)
    assert filtering.state is CheckpointState.COMPLETED
    assert filtering.output["spatial_filtering"]["migration_status"] == "legacy_bypass"
    assert loaded.checkpoint(PipelineStage.EVENTS).state is CheckpointState.COMPLETED
