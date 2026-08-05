"""Runtime health and readiness checks for the server environment."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from platform import python_version
from sys import version_info
from typing import Final


@dataclass(frozen=True, slots=True)
class RuntimeCheck:
    """One deterministic environment check."""

    name: str
    ok: bool
    detail: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class CommandVersion:
    """Description of one required system executable and version prefix."""

    name: str
    command: tuple[str, ...]
    expected_prefix: str
    accepted_exit_codes: tuple[int, ...] = (0,)


SYSTEM_LIBRARIES: Final[tuple[CommandVersion, ...]] = (
    CommandVersion(
        "gdal",
        ("gdalinfo", "--version"),
        os.getenv("SOIKA_EXPECTED_GDAL", "3.6"),
    ),
    CommandVersion(
        "geos",
        ("dpkg-query", "-W", "-f=${Version}", "libgeos-c1v5"),
        os.getenv("SOIKA_EXPECTED_GEOS", "3.11"),
    ),
    CommandVersion(
        "proj",
        ("proj",),
        os.getenv("SOIKA_EXPECTED_PROJ", "9.1"),
        (0, 1),
    ),
)


def _run_version_command(spec: CommandVersion) -> RuntimeCheck:
    try:
        process = subprocess.run(
            spec.command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return RuntimeCheck(
            name=f"system:{spec.name}",
            ok=False,
            detail=f"command unavailable: {exc}",
        )

    output_parts = (process.stdout, process.stderr)
    output = "\n".join(part for part in output_parts if part).strip()
    version_match = re.search(r"\d+\.\d+(?:\.\d+)?", output)
    version = version_match.group(0) if version_match else "unknown"
    return RuntimeCheck(
        name=f"system:{spec.name}",
        ok=(
            process.returncode in spec.accepted_exit_codes
            and version.startswith(spec.expected_prefix)
        ),
        detail=(
            f"detected={version}; expected_prefix={spec.expected_prefix}; "
            f"exit_code={process.returncode}"
        ),
    )


def _directory_check(name: str, path: Path) -> RuntimeCheck:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".soika-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return RuntimeCheck(name=name, ok=False, detail=f"not writable: {path}: {exc}")
    return RuntimeCheck(name=name, ok=True, detail=f"writable: {path}")


def _cuda_check(required: bool) -> RuntimeCheck:
    if not required:
        return RuntimeCheck(
            name="cuda",
            ok=True,
            detail="CUDA is not required for this runtime profile",
            required=False,
        )

    try:
        import torch
    except ImportError as exc:
        return RuntimeCheck(name="cuda", ok=False, detail=f"torch unavailable: {exc}")

    available = bool(torch.cuda.is_available())
    detail = "available" if available else "torch.cuda.is_available() returned false"
    return RuntimeCheck(name="cuda", ok=available, detail=detail)


def liveness_payload() -> dict[str, object]:
    """Return a process-level liveness response without loading ML models."""

    return {
        "status": "alive",
        "service": "soika-uds-development",
        "python": python_version(),
    }


def readiness_checks(*, repository_root: Path | None = None) -> list[RuntimeCheck]:
    """Run checks needed before accepting server jobs."""

    checks: list[RuntimeCheck] = [
        RuntimeCheck(
            name="python",
            ok=version_info[:2] == (3, 11),
            detail=f"{python_version()} (required: >=3.11,<3.12)",
        )
    ]

    if repository_root is None:
        repository_root = Path(__file__).resolve().parents[1]

    checks.extend(
        (
            _directory_check(
                "storage",
                Path(os.getenv("SOIKA_DATA_DIR", "/var/lib/soika")),
            ),
            _directory_check(
                "model-cache",
                Path(os.getenv("SOIKA_MODEL_DIR", "/var/cache/soika/models")),
            ),
        )
    )

    checks.extend(_run_version_command(spec) for spec in SYSTEM_LIBRARIES)

    required_data = repository_root / "factfinder" / "src"
    for file_name in ("exceptions_countries.csv", "exсeptions_city.csv"):
        path = required_data / file_name
        checks.append(
            RuntimeCheck(
                name=f"data:{file_name}",
                ok=path.is_file(),
                detail=str(path),
            )
        )

    require_cuda = os.getenv("SOIKA_REQUIRE_CUDA", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    checks.append(_cuda_check(require_cuda))
    return checks


def readiness_payload(*, repository_root: Path | None = None) -> dict[str, object]:
    checks = readiness_checks(repository_root=repository_root)
    ready = all(check.ok for check in checks if check.required)
    return {
        "status": "ready" if ready else "not_ready",
        "service": "soika-uds-development",
        "checks": [asdict(check) for check in checks],
    }
