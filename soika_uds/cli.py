"""Command-line utilities for installation and server diagnostics."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from .diagnostics import diagnostics_payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="soika-uds")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser(
        "doctor", help="check dependencies and required package data"
    )
    doctor.add_argument(
        "--repository-root",
        type=Path,
        default=None,
        help="optional repository root for package-data checks",
    )
    doctor.add_argument(
        "--strict",
        action="store_true",
        help="exit with status 1 when a required check fails",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        payload = diagnostics_payload(args.repository_root)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1 if args.strict and not payload["ok"] else 0
    raise RuntimeError(f"unsupported command: {args.command}")
