"""Private health, readiness and Prometheus endpoints for one worker."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from .observability import WorkerMetrics
from .runtime import WorkerRuntime

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class WorkerProbeServer:
    def __init__(
        self,
        runtime: WorkerRuntime,
        metrics: WorkerMetrics,
        *,
        host: str = "127.0.0.1",
        port: int = 9090,
        allow_remote: bool = False,
    ) -> None:
        if not isinstance(runtime, WorkerRuntime):
            raise TypeError("runtime must be WorkerRuntime")
        if not isinstance(metrics, WorkerMetrics):
            raise TypeError("metrics must be WorkerMetrics")
        if host not in _LOOPBACK_HOSTS and not allow_remote:
            raise ValueError(
                "worker probes must bind to loopback unless allow_remote is explicit"
            )
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("port must be in [1, 65535]")
        self.runtime = runtime
        self.metrics = metrics
        self.host = host
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("worker probe server is already running")
        runtime = self.runtime
        metrics = self.metrics

        class Handler(BaseHTTPRequestHandler):
            def _json(self, payload: dict[str, object], status: HTTPStatus) -> None:
                body = json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
                snapshot = runtime.snapshot()
                if self.path == "/healthz":
                    self._json(
                        {
                            "status": "ok",
                            "worker_id": snapshot.worker_id,
                            "compute_class": snapshot.compute_class,
                        },
                        HTTPStatus.OK,
                    )
                    return
                if self.path == "/readyz":
                    status = HTTPStatus.OK if snapshot.ready else HTTPStatus.SERVICE_UNAVAILABLE
                    self._json(
                        {
                            "status": "ready" if snapshot.ready else "not_ready",
                            "worker_id": snapshot.worker_id,
                            "compute_class": snapshot.compute_class,
                            "stopping": snapshot.stopping,
                            "active_analysis_id": snapshot.active_analysis_id,
                        },
                        status,
                    )
                    return
                if self.path == "/metrics":
                    body = metrics.render_prometheus().encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header(
                        "Content-Type",
                        "text/plain; version=0.0.4; charset=utf-8",
                    )
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = Thread(
            target=self._server.serve_forever,
            name="soika-worker-probes",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5.0)

    def __enter__(self) -> WorkerProbeServer:
        self.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()


__all__ = ["WorkerProbeServer"]
