"""Verification of local geolocation model artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..model_registry import tree_digest


def verify_model_artifact(path: Path, expected_sha256: str) -> None:
    artifact = Path(path)
    if not artifact.exists():
        raise FileNotFoundError(f"geolocation model artifact is missing: {artifact}")
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise ValueError("expected model artifact digest must be SHA-256")
    try:
        int(expected_sha256, 16)
    except ValueError as error:
        raise ValueError("expected model artifact digest must be SHA-256") from error
    if artifact.is_dir():
        actual = tree_digest(artifact)
    elif artifact.is_file():
        digest = hashlib.sha256()
        with artifact.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        actual = digest.hexdigest()
    else:
        raise ValueError("geolocation model artifact must be a file or directory")
    if actual != expected_sha256.lower():
        raise ValueError(
            "geolocation model artifact digest mismatch: "
            f"expected {expected_sha256.lower()}, got {actual}"
        )
