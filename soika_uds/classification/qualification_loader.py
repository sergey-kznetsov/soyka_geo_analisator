"""Strict JSON loader for classification qualification documents."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .qualification import (
    QualificationInput,
    qualification_input_from_dict as _parse,
)


def _object_array(value: object, field_name: str) -> None:
    if not isinstance(value, Sequence) or isinstance(
        value,
        str | bytes | bytearray,
    ):
        raise ValueError(f"qualification {field_name} must be an array")
    if any(not isinstance(item, Mapping) for item in value):
        raise ValueError(f"qualification {field_name} entries must be objects")


def qualification_input_from_dict(
    payload: Mapping[str, Any],
) -> QualificationInput:
    _object_array(payload.get("models"), "models")
    _object_array(payload.get("benchmarks", []), "benchmarks")
    return _parse(payload)


def load_qualification_input(path: Path) -> QualificationInput:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("qualification document must be a JSON object")
    return qualification_input_from_dict(payload)


__all__ = ["load_qualification_input", "qualification_input_from_dict"]
