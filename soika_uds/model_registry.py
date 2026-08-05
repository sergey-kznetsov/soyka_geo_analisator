"""Versioned model download, locking and checksum verification."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ModelSpec:
    name: str
    repo_id: str
    revision: str
    license: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class InstalledModel:
    name: str
    repo_id: str
    revision: str
    path: str
    sha256: str


def default_manifest_path() -> Path:
    return Path(str(files("soika_uds.resources").joinpath("model_manifest.json")))


def load_manifest(path: Path | None = None) -> list[ModelSpec]:
    manifest_path = path or default_manifest_path()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported model manifest schema")

    models = [
        ModelSpec(
            name=str(raw["name"]),
            repo_id=str(raw["repo_id"]),
            revision=str(raw["revision"]),
            license=str(raw["license"]),
            required=bool(raw.get("required", True)),
        )
        for raw in payload.get("models", [])
    ]
    if not models:
        raise ValueError("model manifest does not contain models")
    return models


def tree_digest(path: Path) -> str:
    digest = hashlib.sha256()
    model_files = sorted(
        candidate for candidate in path.rglob("*") if candidate.is_file()
    )
    for file_path in model_files:
        relative = file_path.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        with file_path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def lock_manifest(source: Path | None, destination: Path) -> dict[str, Any]:
    from huggingface_hub import HfApi

    api = HfApi()
    locked: list[dict[str, Any]] = []
    for spec in load_manifest(source):
        info = api.model_info(spec.repo_id, revision=spec.revision)
        if not info.sha:
            raise RuntimeError(f"no commit SHA returned for {spec.repo_id}")
        locked.append(
            {
                "name": spec.name,
                "repo_id": spec.repo_id,
                "revision": info.sha,
                "license": spec.license,
                "required": spec.required,
            }
        )

    payload: dict[str, Any] = {"schema_version": 1, "models": locked}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def install_models(manifest: Path, destination: Path) -> list[InstalledModel]:
    from huggingface_hub import snapshot_download

    destination.mkdir(parents=True, exist_ok=True)
    installed: list[InstalledModel] = []
    for spec in load_manifest(manifest):
        if len(spec.revision) < 7 or spec.revision in {"main", "master"}:
            raise ValueError(f"model {spec.name} is not locked: {spec.revision}")

        model_path = destination / spec.name
        model_path.mkdir(parents=True, exist_ok=True)
        snapshot_download(
            repo_id=spec.repo_id,
            revision=spec.revision,
            local_dir=model_path,
            local_dir_use_symlinks=False,
        )
        installed.append(
            InstalledModel(
                name=spec.name,
                repo_id=spec.repo_id,
                revision=spec.revision,
                path=str(model_path),
                sha256=tree_digest(model_path),
            )
        )

    registry_payload = {
        "schema_version": 1,
        "models": [asdict(item) for item in installed],
    }
    (destination / "installed-models.json").write_text(
        json.dumps(registry_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return installed


def verify_models(destination: Path) -> dict[str, Any]:
    registry_path = destination / "installed-models.json"
    if not registry_path.is_file():
        return {
            "ok": False,
            "error": f"registry is missing: {registry_path}",
            "models": [],
        }

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = []
    for raw in registry.get("models", []):
        model_path = Path(raw["path"])
        actual = tree_digest(model_path) if model_path.is_dir() else None
        expected = str(raw["sha256"])
        results.append(
            {
                "name": raw["name"],
                "path": str(model_path),
                "expected_sha256": expected,
                "actual_sha256": actual,
                "ok": actual == expected,
            }
        )

    all_valid = bool(results) and all(item["ok"] for item in results)
    return {"ok": all_valid, "models": results}
