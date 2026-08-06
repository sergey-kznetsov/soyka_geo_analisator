"""Command-line entry point for reproducible classification qualification."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from .qualification import load_qualification_input, qualify_release


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="soika-classification-qualify")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="return exit code 2 when any production release gate is blocked",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = qualify_release(load_qualification_input(args.input))
    payload = json.dumps(
        report.to_dict(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    if args.output is None:
        print(payload)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{payload}\n", encoding="utf-8")
    return 2 if args.strict and not report.approved_for_production else 0


if __name__ == "__main__":
    raise SystemExit(main())
