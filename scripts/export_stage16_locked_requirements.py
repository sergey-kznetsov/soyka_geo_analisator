from __future__ import annotations

import argparse
import tomllib
from collections import defaultdict
from pathlib import Path


def _locked_main_requirements(lock_path: Path) -> tuple[str, ...]:
    payload = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    packages = payload.get("package")
    if not isinstance(packages, list):
        raise ValueError("poetry.lock does not contain a package array")

    requirements: set[str] = set()
    entries_by_name: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for package in packages:
        if not isinstance(package, dict):
            raise ValueError("poetry.lock package entries must be tables")
        groups = package.get("groups", [])
        if not isinstance(groups, list) or "main" not in groups:
            continue
        name = package.get("name")
        version = package.get("version")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("poetry.lock package name is invalid")
        if not isinstance(version, str) or not version.strip():
            raise ValueError(f"poetry.lock version is invalid for {name!r}")

        marker_value = package.get("markers", "")
        if marker_value is None:
            marker_value = ""
        if not isinstance(marker_value, str):
            raise ValueError(f"poetry.lock markers are invalid for {name!r}")
        marker = marker_value.strip()
        if marker == "<empty>":
            # Poetry uses this sentinel for a solver branch that cannot match
            # any environment. It must not become a PEP 508 requirement.
            continue

        clean_name = name.strip()
        clean_version = version.strip()
        normalized = clean_name.lower().replace("_", "-")
        previous_entries = entries_by_name[normalized]
        if any(
            existing_version != clean_version
            and (not existing_marker or not marker)
            for existing_version, existing_marker in previous_entries
        ):
            raise ValueError(
                f"ambiguous unmarked main lock entries for {clean_name!r}"
            )
        previous_entries.append((clean_version, marker))

        requirement = f"{clean_name}=={clean_version}"
        if marker:
            requirement = f"{requirement} ; {marker}"
        requirements.add(requirement)

    if not requirements:
        raise ValueError("poetry.lock contains no main dependency entries")
    return tuple(sorted(requirements, key=str.lower))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export exact Poetry main-group versions for security auditing."
    )
    parser.add_argument("--lock", default="poetry.lock")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    requirements = _locked_main_requirements(Path(args.lock))
    output = Path(args.output)
    output.write_text("\n".join(requirements) + "\n", encoding="utf-8")
    print(f"exported {len(requirements)} locked main dependencies to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
