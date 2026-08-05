"""Access, fingerprint and export the public JSON Schema bundle."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from .contract import SUPPORTED_CONTRACT_VERSIONS, canonical_json

SCHEMA_FILES = (
    "common.schema.json",
    "analysis-request.schema.json",
    "job-status.schema.json",
    "analysis-result.schema.json",
)


def _schema_root(version: str = "v1"):
    return files("soika_uds.integration").joinpath("schemas", version)


def load_schema(name: str, version: str = "v1") -> dict[str, Any]:
    if name not in SCHEMA_FILES:
        raise ValueError(f"unknown contract schema: {name}")
    resource = _schema_root(version).joinpath(name)
    return json.loads(resource.read_text(encoding="utf-8"))


def load_schema_bundle(version: str = "v1") -> dict[str, dict[str, Any]]:
    return {name: load_schema(name, version) for name in SCHEMA_FILES}


def schema_bundle_digest(version: str = "v1") -> str:
    bundle = load_schema_bundle(version)
    canonical = canonical_json(bundle)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def export_schema_bundle(destination: Path, version: str = "v1") -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    exported: list[Path] = []
    for name in SCHEMA_FILES:
        target = destination / name
        payload = load_schema(name, version)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        exported.append(target)
    return exported


def contract_info() -> dict[str, Any]:
    return {
        "contract_name": "SOIKA UDS Development integration contract",
        "supported_versions": list(SUPPORTED_CONTRACT_VERSIONS),
        "schema_family": "v1",
        "schema_draft": "https://json-schema.org/draft/2020-12/schema",
        "schema_digest": schema_bundle_digest(),
        "schemas": list(SCHEMA_FILES),
    }
