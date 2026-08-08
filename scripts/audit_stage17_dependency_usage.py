"""Report direct Poetry dependency imports across packaged production sources."""

from __future__ import annotations

import ast
import json
from collections import defaultdict
from pathlib import Path

ROOTS = (
    Path("soika_uds"),
    Path("geoanalyzer_storage"),
    Path("factfinder"),
    Path("pymorphy2"),
)

DEPENDENCY_IMPORTS = {
    "pandas": ("pandas",),
    "matplotlib": ("matplotlib",),
    "seaborn": ("seaborn",),
    "nltk": ("nltk",),
    "scikit-learn": ("sklearn",),
    "spacy": ("spacy",),
    "fuzzywuzzy": ("fuzzywuzzy",),
    "geopandas": ("geopandas",),
    "numpy": ("numpy",),
    "flair": ("flair",),
    "networkx": ("networkx",),
    "osm2geojson": ("osm2geojson",),
    "osmnx": ("osmnx",),
    "pymorphy3": ("pymorphy3",),
    "pymorphy3-dicts-ru": (),
    "torch": ("torch",),
    "tqdm": ("tqdm",),
    "geopy": ("geopy",),
    "shapely": ("shapely",),
    "transformers": ("transformers",),
    "huggingface-hub": ("huggingface_hub",),
    "bertopic": ("bertopic",),
    "hdbscan": ("hdbscan",),
    "umap-learn": ("umap",),
    "natasha": ("natasha",),
    "requests": ("requests",),
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.partition(".")[0])
    return roots


def main() -> int:
    usage: dict[str, list[str]] = defaultdict(list)
    for root in ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            imported = _imports(path)
            for dependency, module_roots in DEPENDENCY_IMPORTS.items():
                if imported.intersection(module_roots):
                    usage[dependency].append(path.as_posix())

    report = {
        dependency: {
            "import_roots": list(module_roots),
            "files": usage.get(dependency, []),
        }
        for dependency, module_roots in DEPENDENCY_IMPORTS.items()
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
