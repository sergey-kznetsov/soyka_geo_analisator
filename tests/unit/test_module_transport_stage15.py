from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from datetime import UTC, datetime

import pytest

from soika_uds.integration import ContractIssue, ResultProvenance, schema_bundle_digest
from soika_uds.orchestration import (
    PIPELINE_STAGES,
    InMemoryJobStore,
    PipelineStage,
    SoikaOrchestrator,
    StageResult,
)
from soika_uds.transport import (
    MODULE_PROTOCOL_VERSION,
    SOIKA_MODULE_ID,
    ModuleHttpServer,
    SoikaModuleApi,
)
from soika_uds.worker import ComputeClass, WorkerControl


class FakeQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, ComputeClass]] = []
        self.cancelled: list[str] = []

    def healthcheck(self) -> bool:
        return True

    def enqueue(
        self,
        analysis_id: str,
        *,
        compute_class: ComputeClass,
        priority: int = 0,
        max_attempts: int = 3,
        trace_id: str | None = None,
    ) -> object:
        del priority, max_attempts, trace_id
        self.enqueued.append((analysis_id, compute_class))
        return object()

    def request_cancel(self, analysis_id: str) -> object:
        self.cancelled.append(analysis_id)
        return object()

    def retry(self, analysis_id: str) -> object:
        return object()


def _handler(stage: PipelineStage) -> StageResult:
    if stage is PipelineStage.FINALIZING:
        return StageResult(
            output={
                "coverage": {
                    "sources_requested": 2,
                    "sources_available": 2,
                    "messages_collected": 8,
                    "messages_relevant": 6,
                    "messages_geocoded": 5,
                    "messages_low_confidence": 1,
                },
                "categories": [
                    {"name": "transport", "count": 3},
                    {"name": "environment", "count": 2},
                ],
                "geojson": {
                    "type": "FeatureCollection",
                    "features": [],
                },
                "metadata": {"fixture": "stage15"},
            }
        )
    return StageResult(output={})


def _warning_handler(stage: PipelineStage) -> StageResult:
    if stage is PipelineStage.FINALIZING:
        return StageResult(
            output={
                "coverage": {
                    "sources_requested": 2,
                    "sources_available": 1,
                    "messages_collected": 4,
                    "messages_relevant": 3,
                    "messages_geocoded": 2,
                    "messages_low_confidence": 0,
                },
                "partial": True,
            },
            warnings=(
                ContractIssue(
                    code="SOURCE_UNAVAILABLE",
                    message="one source was unavailable",
                ),
            ),
        )
    return StageResult(output={})


def _api(*, partial: bool = False) -> tuple[SoikaModuleApi, SoikaOrchestrator, FakeQueue]:
    store = InMemoryJobStore()
    function = _warning_handler if partial else _handler
    orchestrator = SoikaOrchestrator(
        store,
        handlers={stage: function for stage in PIPELINE_STAGES},
        worker_id="stage15-test-worker",
    )
    queue = FakeQueue()
    api = SoikaModuleApi(
        WorkerControl(orchestrator, queue),
        provenance=ResultProvenance(
            soika_version="0.20.0",
            schema_digest=schema_bundle_digest(),
        ),
    )
    return api, orchestrator, queue


def _request(analysis_id: str = "stage15-analysis") -> dict[str, object]:
    return {
        "protocol_version": MODULE_PROTOCOL_VERSION,
        "module_id": SOIKA_MODULE_ID,
        "analysis_id": analysis_id,
        "requested_at": datetime.now(UTC).isoformat(),
        "idempotency_key": f"geo-analyzer:{analysis_id}:fixture",
        "territory": {
            "city": "Ижевск",
            "address": "Пушкинская, 277",
            "point": {"latitude": 56.8526, "longitude": 53.2115},
        },
        "sources": ["fixture"],
        "options": {},
        "allow_partial": True,
    }


def test_manifest_declares_optional_universal_ui_slots() -> None:
    api, _orchestrator, _queue = _api()

    manifest = api.manifest()

    assert manifest["protocol_version"] == "1.0.0"
    assert manifest["module_id"] == "soyka.reviews"
    assert manifest["ui"] == {
        "optional": True,
        "default_enabled": False,
        "analysis_launch_toggle": True,
        "capability_card": True,
    }
    assert "analysis.submit" in manifest["capabilities"]
    assert "analysis.result" in manifest["capabilities"]


def test_submit_status_and_result_are_transport_neutral() -> None:
    api, orchestrator, queue = _api()

    submitted = api.submit(_request())

    assert submitted["status"] == "queued"
    assert queue.enqueued == [("stage15-analysis", ComputeClass.CPU)]

    orchestrator.resume("stage15-analysis")
    result = api.result("stage15-analysis")

    assert result["status"] == "completed"
    assert result["partial"] is False
    assert result["coverage"]["messages_collected"] == 8
    assert result["geojson"]["type"] == "FeatureCollection"
    assert result["report_sections"][0]["sheet_name"] == "СОЙКА"
    assert result["report_sections"][1]["sheet_name"] == "СОЙКА категории"


def test_partial_result_keeps_warnings_and_coverage() -> None:
    api, orchestrator, _queue = _api(partial=True)
    api.submit(_request("stage15-partial"))
    orchestrator.resume("stage15-partial")

    result = api.result("stage15-partial")

    assert result["status"] == "completed_with_warnings"
    assert result["partial"] is True
    assert result["coverage"]["sources_available"] == 1
    assert result["warnings"][0]["code"] == "SOURCE_UNAVAILABLE"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _http_json(
    url: str,
    *,
    token: str | None,
    method: str = "GET",
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, object]]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def test_http_transport_requires_bearer_auth_and_serves_manifest() -> None:
    api, _orchestrator, _queue = _api()
    port = _free_port()
    token = "stage15-secret-token"

    with ModuleHttpServer(api, auth_token=token, port=port):
        unauthorized_status, problem = _http_json(
            f"http://127.0.0.1:{port}/v1/manifest",
            token=None,
        )
        ok_status, manifest = _http_json(
            f"http://127.0.0.1:{port}/v1/manifest",
            token=token,
        )

    assert unauthorized_status == 401
    assert problem["status"] == 401
    assert ok_status == 200
    assert manifest["module_id"] == "soyka.reviews"


def test_http_submit_is_accepted_without_executing_untrusted_code() -> None:
    api, _orchestrator, queue = _api()
    port = _free_port()
    token = "stage15-secret-token"

    with ModuleHttpServer(api, auth_token=token, port=port):
        status, submitted = _http_json(
            f"http://127.0.0.1:{port}/v1/analyses",
            token=token,
            method="POST",
            payload=_request("stage15-http"),
        )

    assert status == 202
    assert submitted["status"] == "queued"
    assert queue.enqueued == [("stage15-http", ComputeClass.CPU)]


def test_remote_bind_is_explicit() -> None:
    api, _orchestrator, _queue = _api()

    with pytest.raises(ValueError, match="allow_remote"):
        ModuleHttpServer(api, auth_token="secret", host="0.0.0.0")
