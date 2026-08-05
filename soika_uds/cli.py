"""Command-line utilities for installation and server diagnostics."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path

from .diagnostics import diagnostics_payload
from .environment import liveness_payload, readiness_payload
from .model_registry import install_models, lock_manifest, verify_models
from .probes import serve_probes


def _add_repository_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=None,
        help="optional repository root for package-data checks",
    )


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
    probes.add_argument("--host", default="0.0.0.0")
    probes.add_argument("--port", type=int, default=8080)
    _add_repository_root(probes)

    models = subparsers.add_parser("models", help="manage reproducible ML model files")
    model_commands = models.add_subparsers(dest="models_command", required=True)

    lock = model_commands.add_parser("lock", help="resolve mutable model refs to commit SHAs")
    lock.add_argument("--manifest", type=Path, default=None)
    lock.add_argument("--output", type=Path, required=True)

    install = model_commands.add_parser("install", help="download a locked model manifest")
    install.add_argument("--manifest", type=Path, required=True)
    install.add_argument(
        "--destination",
        type=Path,
        default=Path(os.getenv("SOIKA_MODEL_DIR", "/var/cache/soika/models")),
    )

    verify = model_commands.add_parser("verify", help="verify installed model checksums")
    verify.add_argument(
        "--destination",
        type=Path,
        default=Path(os.getenv("SOIKA_MODEL_DIR", "/var/cache/soika/models")),
    )
    verify.add_argument("--strict", action="store_true")
    return parser


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

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
            _print([item.__dict__ for item in install_models(args.manifest, args.destination)])
            return 0
        if args.models_command == "verify":
            payload = verify_models(args.destination)
            _print(payload)
            return 1 if args.strict and not payload["ok"] else 0

    raise RuntimeError(f"unsupported command: {args.command}")
