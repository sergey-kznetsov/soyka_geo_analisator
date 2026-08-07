"""Transport-neutral contracts for shared Geo Analyzer persistence."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Any, Protocol

_APPLICATION_ID = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
_NAMESPACE = re.compile(r"^[a-z][a-z0-9_.:-]{0,126}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


def application_id(value: object) -> str:
    if not isinstance(value, str) or _APPLICATION_ID.fullmatch(value) is None:
        raise ValueError(
            "application_id must start with a letter and contain only "
            "lowercase letters, digits, '_' or '-'"
        )
    return value


def cache_namespace(value: object) -> str:
    if not isinstance(value, str) or _NAMESPACE.fullmatch(value) is None:
        raise ValueError("cache namespace contains unsupported characters")
    return value


def sha256_digest(value: object, field_name: str = "digest") -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _json_value(value: object, field_name: str) -> Any:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field_name} keys must be strings")
            result[key] = _json_value(item, f"{field_name}.{key}")
        return result
    if isinstance(value, (list, tuple)):
        return [
            _json_value(item, f"{field_name}[{index}]")
            for index, item in enumerate(value)
        ]
    raise ValueError(f"{field_name} contains unsupported {type(value).__name__}")


def canonical_json(value: object) -> str:
    return json.dumps(
        _json_value(value, "json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def digest_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def cache_key(operation: str, parameters: Mapping[str, Any]) -> str:
    if not isinstance(operation, str) or not operation.strip():
        raise ValueError("cache operation must be a non-empty string")
    return digest_json(
        {"operation": operation.strip(), "parameters": dict(parameters)}
    )


class JsonCache(Protocol):
    """Small durable cache surface shared by ecosystem services."""

    @staticmethod
    def key(operation: str, parameters: Mapping[str, Any]) -> str: ...

    def get(self, cache_key: str) -> Any | None: ...

    def set(self, cache_key: str, payload: Any, *, ttl_seconds: int) -> None: ...


__all__ = [
    "JsonCache",
    "application_id",
    "cache_key",
    "cache_namespace",
    "canonical_json",
    "digest_json",
    "sha256_digest",
]
