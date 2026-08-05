"""Static diagnostics that do not download models or call external services."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib.util import find_spec
from pathlib import Path
from platform import python_version
from sys import version_info
from typing import Iterable


@dataclass(frozen=True, slots=True)
class DiagnosticCheck:
    name: str
    ok: bool
    detail: str
    required: bool = True


DEPENDENCY_GROUPS: dict[str, tuple[str, ...]] = {
    "classification": ("torch", "transformers"),
    "geolocation": (
        "flair",
        "natasha",
        "geopandas",
        "geopy",
        "osmnx",
        "osm2geojson",
        "shapely",
    ),
    "events": ("bertopic", "hdbscan", "umap", "networkx"),
}


def _module_checks(modules: Iterable[str], *, group: str) -> list[DiagnosticCheck]:
    return [
        DiagnosticCheck(
            name=f"dependency:{group}:{module}",
            ok=find_spec(module) is not None,
            detail="installed" if find_spec(module) is not None else "not installed",
        )
        for module in modules
    ]


def run_diagnostics(repository_root: Path | None = None) -> list[DiagnosticCheck]:
    checks: list[DiagnosticCheck] = []
    supported_python = (3, 10, 6) <= version_info[:3] < (3, 11)
    checks.append(
        DiagnosticCheck(
            name="python",
            ok=supported_python,
            detail=(
                f"{python_version()} (normalized legacy baseline is >=3.10.6,<3.11)"
            ),
        )
    )

    for group, modules in DEPENDENCY_GROUPS.items():
        checks.extend(_module_checks(modules, group=group))

    if repository_root is None:
        repository_root = Path(__file__).resolve().parents[1]

    data_dir = repository_root / "factfinder" / "src"
    for file_name in ("exceptions_countries.csv", "exсeptions_city.csv"):
        path = data_dir / file_name
        checks.append(
            DiagnosticCheck(
                name=f"data:{file_name}",
                ok=path.is_file(),
                detail=str(path),
            )
        )

    return checks


def diagnostics_payload(repository_root: Path | None = None) -> dict[str, object]:
    checks = run_diagnostics(repository_root)
    return {
        "ok": all(check.ok for check in checks if check.required),
        "checks": [asdict(check) for check in checks],
    }
