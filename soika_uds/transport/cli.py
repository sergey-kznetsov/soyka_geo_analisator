"""Standalone private HTTP process for the Geo Analyzer module protocol."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

from geoanalyzer_storage import PostgresDatabase, PostgresSettings

from ..integration import ResultProvenance, schema_bundle_digest
from ..orchestration import PostgresJobStore, SoikaOrchestrator
from ..worker import PostgresJobQueue, WorkerControl
from .http_server import ModuleHttpServer
from .module_api import SOIKA_MODULE_VERSION, SoikaModuleApi


def _read_secret(path: Path, *, name: str, max_bytes: int) -> str:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ValueError(f"cannot read {name} secret file: {path}") from error
    if len(raw) > max_bytes:
        raise ValueError(f"{name} secret file is unexpectedly large")
    try:
        value = raw.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise ValueError(f"{name} secret must be UTF-8") from error
    if not value or "\x00" in value:
        raise ValueError(f"{name} secret is empty or malformed")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m soika_uds.transport")
    parser.add_argument(
        "--database-dsn-file",
        type=Path,
        default=(
            Path(os.environ["GEOANALYZER_DATABASE_DSN_FILE"])
            if "GEOANALYZER_DATABASE_DSN_FILE" in os.environ
            else None
        ),
    )
    parser.add_argument(
        "--auth-token-file",
        type=Path,
        default=(
            Path(os.environ["SOIKA_MODULE_AUTH_TOKEN_FILE"])
            if "SOIKA_MODULE_AUTH_TOKEN_FILE" in os.environ
            else None
        ),
    )
    parser.add_argument("--application", default="soika")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9080)
    parser.add_argument("--allow-remote", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.database_dsn_file is None:
        raise SystemExit(
            "GEOANALYZER_DATABASE_DSN_FILE is required; DSN argv is not supported"
        )
    if args.auth_token_file is None:
        raise SystemExit(
            "SOIKA_MODULE_AUTH_TOKEN_FILE is required; token argv is not supported"
        )

    dsn = _read_secret(
        args.database_dsn_file,
        name="database DSN",
        max_bytes=16_384,
    )
    token = _read_secret(
        args.auth_token_file,
        name="module auth token",
        max_bytes=4_096,
    )
    database = PostgresDatabase(
        PostgresSettings(
            dsn=dsn,
            application_name="soika-module-api",
            min_pool_size=1,
            max_pool_size=8,
        )
    )
    try:
        queue = PostgresJobQueue(database, application=args.application)
        if not queue.healthcheck():
            raise SystemExit(
                "ga_core.job_queue is unavailable; apply platform/soika/worker migrations"
            )
        store = PostgresJobStore(database, application=args.application)
        orchestrator = SoikaOrchestrator(store, handlers={})
        control = WorkerControl(orchestrator, queue)
        api = SoikaModuleApi(
            control,
            provenance=ResultProvenance(
                soika_version=SOIKA_MODULE_VERSION,
                schema_digest=schema_bundle_digest(),
            ),
        )
        ModuleHttpServer(
            api,
            auth_token=token,
            host=args.host,
            port=args.port,
            allow_remote=args.allow_remote,
        ).serve_forever()
        return 0
    finally:
        database.close()


__all__ = ["build_parser", "main"]
