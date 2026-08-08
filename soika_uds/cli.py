"""Command-line utilities for installation and server diagnostics."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .diagnostics import diagnostics_payload
from .environment import liveness_payload, readiness_payload
from .integration import (
    AnalysisRequestV1,
    AnalysisResultV1,
    ContractValidationError,
    JobStatusV1,
    contract_info,
    export_schema_bundle,
)
from .model_registry import install_models, lock_manifest, verify_models
from .orchestration import FileJobStore, OrchestrationError, SoikaOrchestrator
from .probes import serve_probes


def _add_repository_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=None,
        help="optional repository root for package-data checks",
    )


def _add_contract_commands(subparsers: argparse._SubParsersAction) -> None:
    contract = subparsers.add_parser(
        "contract",
        help="inspect and validate the versioned Geo Analyzer contract",
    )
    commands = contract.add_subparsers(dest="contract_command", required=True)
    commands.add_parser("info", help="print supported versions and schema digest")

    export = commands.add_parser("export", help="export the JSON Schema bundle")
    export.add_argument("--destination", type=Path, required=True)

    validate = commands.add_parser(
        "validate",
        help="validate and normalize one contract document",
    )
    validate.add_argument(
        "--kind",
        choices=("request", "status", "result"),
        required=True,
    )
    validate.add_argument("--input", type=Path, required=True)


def _default_job_state_dir() -> Path:
    return Path(os.getenv("SOIKA_DATA_DIR", "/var/lib/soika")) / "jobs"


def _add_job_commands(subparsers: argparse._SubParsersAction) -> None:
    jobs = subparsers.add_parser(
        "jobs",
        help="inspect and control durable orchestration jobs",
    )
    jobs.add_argument(
        "--state-dir",
        type=Path,
        default=_default_job_state_dir(),
        help="directory containing durable orchestration state",
    )
    commands = jobs.add_subparsers(dest="jobs_command", required=True)
    commands.add_parser("list", help="list persisted jobs")

    for name, help_text in (
        ("status", "print one persisted job status"),
        ("cancel", "request cancellation of one job"),
        ("retry", "reset a failed job for explicit retry"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--analysis-id", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="soika-uds")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser(
        "doctor", help="check dependencies and required package data"
    )
    _add_repository_root(doctor)
    doctor.add_argument(
        "--strict",
        action="store_true",
        help="exit with status 1 when a required check fails",
    )

    subparsers.add_parser("health", help="return a lightweight liveness payload")

    ready = subparsers.add_parser("ready", help="check server runtime readiness")
    _add_repository_root(ready)
    ready.add_argument("--strict", action="store_true")

    probes = subparsers.add_parser(
        "serve-probes", help="serve internal /healthz and /readyz endpoints"
    )
    probes.add_argument("--host", default="127.0.0.1")
    probes.add_argument("--port", type=int, default=8080)
    _add_repository_root(probes)

    models = subparsers.add_parser("models", help="manage reproducible ML models")
    model_commands = models.add_subparsers(dest="models_command", required=True)

    lock = model_commands.add_parser(
        "lock",
        help="resolve mutable model refs to commit SHAs",
    )
    lock.add_argument("--manifest", type=Path, default=None)
    lock.add_argument("--output", type=Path, required=True)

    install = model_commands.add_parser(
        "install",
        help="download a locked model manifest",
    )
    install.add_argument("--manifest", type=Path, required=True)
    install.add_argument(
        "--destination",
        type=Path,
        default=Path(os.getenv("SOIKA_MODEL_DIR", "/var/cache/soika/models")),
    )

    verify = model_commands.add_parser(
        "verify",
        help="verify installed model checksums",
    )
    verify.add_argument(
        "--destination",
        type=Path,
        default=Path(os.getenv("SOIKA_MODEL_DIR", "/var/cache/soika/models")),
    )
    verify.add_argument("--strict", action="store_true")

    _add_contract_commands(subparsers)
    _add_job_commands(subparsers)
    return parser


def _print(payload: object, *, stream=None) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, indent=2),
        file=stream or sys.stdout,
    )


def _load_json_object(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractValidationError(f"cannot read JSON document: {error}") from error
    if not isinstance(payload, dict):
        raise ContractValidationError("contract document must be a JSON object")
    return payload


def _validate_contract_document(kind: str, path: Path) -> dict[str, Any]:
    payload = _load_json_object(path)
    if kind == "request":
        return AnalysisRequestV1.from_dict(payload).to_dict()
    if kind == "status":
        return JobStatusV1.from_dict(payload).to_dict()
    if kind == "result":
        return AnalysisResultV1.from_dict(payload).to_dict()
    raise ContractValidationError(f"unsupported contract kind: {kind}")


def _job_summary(record) -> dict[str, Any]:
    return {
        "analysis_id": record.analysis_id,
        "status": record.status.value,
        "stage": record.current_stage.value if record.current_stage else None,
        "progress_percent": record.progress_percent,
        "job_attempt": record.job_attempt,
        "revision": record.revision,
        "updated_at": record.updated_at.isoformat().replace("+00:00", "Z"),
    }


def _run_job_command(args) -> int:
    orchestrator = SoikaOrchestrator(FileJobStore(args.state_dir), {})
    if args.jobs_command == "list":
        _print([_job_summary(record) for record in orchestrator.list_jobs()])
        return 0
    if args.jobs_command == "status":
        _print(orchestrator.status(args.analysis_id).to_dict())
        return 0
    if args.jobs_command == "cancel":
        record = orchestrator.request_cancel(args.analysis_id)
        _print(record.to_status().to_dict())
        return 0
    if args.jobs_command == "retry":
        record = orchestrator.retry_failed(args.analysis_id)
        _print(record.to_status().to_dict())
        return 0
    raise OrchestrationError(f"unsupported jobs command: {args.jobs_command}")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.command == "doctor":
            payload = diagnostics_payload(args.repository_root)
            _print(payload)
            return 1 if args.strict and not payload["ok"] else 0

        if args.command == "health":
            _print(liveness_payload())
            return 0

        if args.command == "ready":
            payload = readiness_payload(repository_root=args.repository_root)
            _print(payload)
            return 1 if args.strict and payload["status"] != "ready" else 0

        if args.command == "serve-probes":
            serve_probes(args.host, args.port, repository_root=args.repository_root)
            return 0

        if args.command == "models":
            if args.models_command == "lock":
                _print(lock_manifest(args.manifest, args.output))
                return 0
            if args.models_command == "install":
                installed = install_models(args.manifest, args.destination)
                _print([asdict(item) for item in installed])
                return 0
            if args.models_command == "verify":
                payload = verify_models(args.destination)
                _print(payload)
                return 1 if args.strict and not payload["ok"] else 0

        if args.command == "contract":
            if args.contract_command == "info":
                _print(contract_info())
                return 0
            if args.contract_command == "export":
                exported = export_schema_bundle(args.destination)
                _print(
                    {
                        **contract_info(),
                        "destination": str(args.destination),
                        "exported": [str(path) for path in exported],
                    }
                )
                return 0
            if args.contract_command == "validate":
                normalized = _validate_contract_document(args.kind, args.input)
                _print({"valid": True, "document": normalized})
                return 0

        if args.command == "jobs":
            return _run_job_command(args)
    except ContractValidationError as error:
        _print(
            {
                "valid": False,
                "error": {
                    "code": "CONTRACT_VALIDATION_ERROR",
                    "message": str(error),
                },
            },
            stream=sys.stderr,
        )
        return 2
    except OrchestrationError as error:
        _print(
            {
                "ok": False,
                "error": {
                    "code": "ORCHESTRATION_ERROR",
                    "message": str(error),
                },
            },
            stream=sys.stderr,
        )
        return 3

    raise RuntimeError(f"unsupported command: {args.command}")
