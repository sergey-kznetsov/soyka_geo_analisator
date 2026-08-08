"""Authenticated private HTTP transport for the Geo Analyzer module protocol."""

from __future__ import annotations

import json
import re
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import unquote, urlsplit

from ..orchestration import JobNotFoundError, OrchestrationError
from .module_api import (
    ModuleConflictError,
    ModuleProtocolError,
    ModuleResultNotReadyError,
    SoikaModuleApi,
)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_ANALYSIS_PATH = re.compile(r"^/v1/analyses/([^/]+)$")
_ANALYSIS_ACTION_PATH = re.compile(r"^/v1/analyses/([^/]+)/(result|cancel|retry)$")
_MAX_BODY_BYTES = 2 * 1024 * 1024


class ModuleHttpServer:
    """Run the module API on a private HTTP endpoint.

    Remote binding is opt-in. Authentication is mandatory even on loopback so
    the same deployment contract is exercised in development and production.
    """

    def __init__(
        self,
        api: SoikaModuleApi,
        *,
        auth_token: str,
        host: str = "127.0.0.1",
        port: int = 9080,
        allow_remote: bool = False,
        max_body_bytes: int = _MAX_BODY_BYTES,
    ) -> None:
        if not isinstance(api, SoikaModuleApi):
            raise TypeError("api must be SoikaModuleApi")
        if not isinstance(auth_token, str) or not auth_token.strip():
            raise ValueError("auth_token must be non-empty")
        if host not in _LOOPBACK_HOSTS and not allow_remote:
            raise ValueError(
                "module API must bind to loopback unless allow_remote is explicit"
            )
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("port must be in [1, 65535]")
        if type(max_body_bytes) is not int or max_body_bytes < 1024:
            raise ValueError("max_body_bytes must be at least 1024")
        self.api = api
        self.auth_token = auth_token.strip()
        self.host = host
        self.port = port
        self.max_body_bytes = max_body_bytes
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None

    def _handler(self):
        api = self.api
        expected_token = self.auth_token
        max_body_bytes = self.max_body_bytes

        class Handler(BaseHTTPRequestHandler):
            server_version = "SOIKA-Module-API/1"

            def _authorized(self) -> bool:
                value = self.headers.get("Authorization", "")
                prefix = "Bearer "
                if not value.startswith(prefix):
                    return False
                supplied = value[len(prefix) :].strip()
                return bool(supplied) and secrets.compare_digest(
                    supplied,
                    expected_token,
                )

            def _send_json(
                self,
                payload: dict[str, object],
                status: HTTPStatus = HTTPStatus.OK,
                *,
                content_type: str = "application/json; charset=utf-8",
            ) -> None:
                body = json.dumps(
                    payload,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def _problem(
                self,
                status: HTTPStatus,
                *,
                title: str,
                detail: str,
                problem_type: str = "about:blank",
            ) -> None:
                self._send_json(
                    {
                        "type": problem_type,
                        "title": title,
                        "status": int(status),
                        "detail": detail,
                    },
                    status,
                    content_type="application/problem+json; charset=utf-8",
                )

            def _require_auth(self) -> bool:
                if self._authorized():
                    return True
                self._problem(
                    HTTPStatus.UNAUTHORIZED,
                    title="Unauthorized",
                    detail="valid bearer credentials are required",
                )
                return False

            def _read_json(self) -> dict[str, object] | None:
                content_type = self.headers.get("Content-Type", "")
                if "application/json" not in content_type.lower():
                    self._problem(
                        HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                        title="Unsupported Media Type",
                        detail="request body must use application/json",
                    )
                    return None
                raw_length = self.headers.get("Content-Length")
                try:
                    length = int(raw_length or "0")
                except ValueError:
                    self._problem(
                        HTTPStatus.BAD_REQUEST,
                        title="Bad Request",
                        detail="invalid Content-Length header",
                    )
                    return None
                if length <= 0:
                    self._problem(
                        HTTPStatus.BAD_REQUEST,
                        title="Bad Request",
                        detail="JSON request body is required",
                    )
                    return None
                if length > max_body_bytes:
                    self._problem(
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        title="Content Too Large",
                        detail="request body exceeds the configured limit",
                    )
                    return None
                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._problem(
                        HTTPStatus.BAD_REQUEST,
                        title="Bad Request",
                        detail="request body is not valid UTF-8 JSON",
                    )
                    return None
                if not isinstance(payload, dict):
                    self._problem(
                        HTTPStatus.BAD_REQUEST,
                        title="Bad Request",
                        detail="request JSON root must be an object",
                    )
                    return None
                return payload

            def _dispatch_error(self, error: Exception) -> None:
                if isinstance(error, ModuleConflictError):
                    self._problem(
                        HTTPStatus.CONFLICT,
                        title="Conflict",
                        detail=str(error),
                    )
                    return
                if isinstance(error, ModuleResultNotReadyError):
                    self._problem(
                        HTTPStatus.CONFLICT,
                        title="Result Not Ready",
                        detail=str(error),
                    )
                    return
                if isinstance(error, JobNotFoundError):
                    self._problem(
                        HTTPStatus.NOT_FOUND,
                        title="Analysis Not Found",
                        detail=str(error),
                    )
                    return
                if isinstance(error, ModuleProtocolError):
                    self._problem(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        title="Invalid Module Request",
                        detail=str(error),
                    )
                    return
                if isinstance(error, OrchestrationError):
                    self._problem(
                        HTTPStatus.CONFLICT,
                        title="Orchestration Conflict",
                        detail=str(error),
                    )
                    return
                self._problem(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    title="Internal Server Error",
                    detail="module request failed",
                )

            def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
                if not self._require_auth():
                    return
                path = urlsplit(self.path).path
                try:
                    if path == "/v1/manifest":
                        self._send_json(api.manifest())
                        return
                    if path == "/v1/health":
                        payload = api.health()
                        status = (
                            HTTPStatus.OK
                            if payload.get("status") == "ok"
                            else HTTPStatus.SERVICE_UNAVAILABLE
                        )
                        self._send_json(payload, status)
                        return
                    match = _ANALYSIS_PATH.fullmatch(path)
                    if match is not None:
                        self._send_json(api.status(unquote(match.group(1))))
                        return
                    action_match = _ANALYSIS_ACTION_PATH.fullmatch(path)
                    if action_match is not None and action_match.group(2) == "result":
                        self._send_json(api.result(unquote(action_match.group(1))))
                        return
                    self._problem(
                        HTTPStatus.NOT_FOUND,
                        title="Not Found",
                        detail="unknown module API endpoint",
                    )
                except Exception as error:  # noqa: BLE001 - HTTP boundary
                    self._dispatch_error(error)

            def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
                if not self._require_auth():
                    return
                path = urlsplit(self.path).path
                try:
                    if path == "/v1/analyses":
                        payload = self._read_json()
                        if payload is None:
                            return
                        self._send_json(api.submit(payload), HTTPStatus.ACCEPTED)
                        return
                    action_match = _ANALYSIS_ACTION_PATH.fullmatch(path)
                    if action_match is not None:
                        analysis_id = unquote(action_match.group(1))
                        action = action_match.group(2)
                        if action == "cancel":
                            self._send_json(api.cancel(analysis_id), HTTPStatus.ACCEPTED)
                            return
                        if action == "retry":
                            self._send_json(api.retry(analysis_id), HTTPStatus.ACCEPTED)
                            return
                    self._problem(
                        HTTPStatus.NOT_FOUND,
                        title="Not Found",
                        detail="unknown module API endpoint",
                    )
                except Exception as error:  # noqa: BLE001 - HTTP boundary
                    self._dispatch_error(error)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        return Handler

    def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("module HTTP server is already running")
        self._server = ThreadingHTTPServer((self.host, self.port), self._handler())
        self._thread = Thread(
            target=self._server.serve_forever,
            name="soika-module-api",
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

    def serve_forever(self) -> None:
        if self._server is not None:
            raise RuntimeError("module HTTP server is already running")
        server = ThreadingHTTPServer((self.host, self.port), self._handler())
        self._server = server
        try:
            server.serve_forever()
        finally:
            server.server_close()
            self._server = None

    def __enter__(self) -> ModuleHttpServer:
        self.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()


__all__ = ["ModuleHttpServer"]
