from __future__ import annotations

import socket
from datetime import UTC, datetime

from geo_analyzer.modules import (
    HttpAnalysisModuleConnector,
    ModuleAnalysisRequest,
    ModuleStatus,
)
from soika_uds.integration import ResultProvenance, schema_bundle_digest
from soika_uds.orchestration import (
    PIPELINE_STAGES,
    InMemoryJobStore,
    PipelineStage,
    SoikaOrchestrator,
    StageContext,
    StageResult,
)
from soika_uds.transport import ModuleHttpServer, SoikaModuleApi
from soika_uds.worker import ComputeClass, WorkerControl


class _Queue:
    def __init__(self) -> None:
        self.enqueued: list[str] = []

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
        del compute_class, priority, max_attempts, trace_id
        self.enqueued.append(analysis_id)
        return object()

    def request_cancel(self, analysis_id: str) -> object:
        del analysis_id
        return object()

    def retry(self, analysis_id: str) -> object:
        del analysis_id
        return object()


def _handler(context: StageContext) -> StageResult:
    if context.stage is PipelineStage.FINALIZING:
        return StageResult(
            output={
                "coverage": {
                    "sources_requested": 1,
                    "sources_available": 1,
                    "messages_collected": 12,
                    "messages_relevant": 9,
                    "messages_geocoded": 8,
                    "messages_low_confidence": 1,
                },
                "categories": [{"name": "transport", "count": 4}],
                "geojson": {"type": "FeatureCollection", "features": []},
                "metadata": {"release_candidate": "stage16"},
            }
        )
    return StageResult(output={})


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_geo_analyzer_connector_executes_soika_protocol_end_to_end() -> None:
    store = InMemoryJobStore()
    orchestrator = SoikaOrchestrator(
        store,
        handlers={stage: _handler for stage in PIPELINE_STAGES},
        worker_id="stage16-e2e-worker",
    )
    queue = _Queue()
    api = SoikaModuleApi(
        WorkerControl(orchestrator, queue),
        provenance=ResultProvenance(
            soika_version="0.20.0",
            schema_digest=schema_bundle_digest(),
        ),
    )
    port = _free_port()
    token = "stage16-e2e-token"

    with ModuleHttpServer(api, auth_token=token, port=port):
        connector = HttpAnalysisModuleConnector(
            f"http://127.0.0.1:{port}",
            auth_token=token,
            allow_insecure_http=True,
            timeout_seconds=3,
        )
        manifest = connector.manifest()
        assert manifest.module_id == "soyka.reviews"
        assert manifest.protocol_version == "1.0.0"

        request = ModuleAnalysisRequest(
            module_id=manifest.module_id,
            analysis_id="stage16-cross-repo-e2e",
            requested_at=datetime.now(UTC),
            territory={
                "city": "Ижевск",
                "address": "Пушкинская, 277",
                "point": {"latitude": 56.8526, "longitude": 53.2115},
            },
            idempotency_key="geo-analyzer:stage16-cross-repo-e2e",
            sources=("fixture",),
            options={"release_candidate": True},
            allow_partial=True,
        )

        submitted = connector.submit(request)
        assert submitted.status is ModuleStatus.QUEUED
        assert queue.enqueued == [request.analysis_id]

        orchestrator.resume(request.analysis_id)

        status = connector.status(request.analysis_id)
        assert status.status is ModuleStatus.COMPLETED
        result = connector.result(request.analysis_id)
        assert result.status is ModuleStatus.COMPLETED
        assert result.partial is False
        assert result.coverage["messages_collected"] == 12
        assert result.geojson["type"] == "FeatureCollection"
        assert result.report_sections[0].sheet_name == "СОЙКА"
