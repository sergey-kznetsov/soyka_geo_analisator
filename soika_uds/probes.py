"""Minimal internal HTTP server for container liveness and readiness probes."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .environment import liveness_payload, readiness_payload


class ProbeHandler(BaseHTTPRequestHandler):
    repository_root: Path | None = None

    def _write_json(self, payload: dict[str, Any], status: HTTPStatus) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        if self.path == "/healthz":
            self._write_json(liveness_payload(), HTTPStatus.OK)
            return
        if self.path == "/readyz":
            payload = readiness_payload(repository_root=self.repository_root)
            status = (
                HTTPStatus.OK
                if payload["status"] == "ready"
                else HTTPStatus.SERVICE_UNAVAILABLE
            )
            self._write_json(payload, status)
            return
        self._write_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:
        return


def serve_probes(host: str, port: int, repository_root: Path | None = None) -> None:
    handler = type(
        "ConfiguredProbeHandler",
        (ProbeHandler,),
        {"repository_root": repository_root},
    )
    server = ThreadingHTTPServer((host, port), handler)
    server.serve_forever()
