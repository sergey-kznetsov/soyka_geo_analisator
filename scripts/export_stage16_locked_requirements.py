from __future__ import annotations

import argparse
import tomllib
from pathlib import Path


def _locked_main_requirements(lock_path: Path) -> tuple[str, ...]:
    payload = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    packages = payload.get("package")
    if not isinstance(packages, list):
        raise ValueError("poetry.lock does not contain a package array")

    requirements: list[str] = []
    seen: set[str] = set()
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
        requirement = f"{name.strip()}=={version.strip()}"
        normalized = name.strip().lower().replace("_", "-")
        if normalized in seen:
            raise ValueError(f"duplicate main lock entry for {name!r}")
        seen.add(normalized)
        requirements.append(requirement)

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
