"""Independent embedding, reduction and clustering components for events."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol

_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+", re.UNICODE)
_COMMIT_RE = re.compile(r"^[a-f0-9]{40}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


def _vectors(value: Sequence[Sequence[float]], name: str) -> tuple[tuple[float, ...], ...]:
    rows = tuple(tuple(float(number) for number in row) for row in value)
    if not rows:
        return ()
    width = len(rows[0])
    if width < 1 or any(len(row) != width for row in rows):
        raise ValueError(f"{name} vectors must have one non-zero width")
    if any(not math.isfinite(number) for row in rows for number in row):
        raise ValueError(f"{name} vectors must be finite")
    return rows


def _provenance(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    vectors: tuple[tuple[float, ...], ...]
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "vectors", _vectors(self.vectors, "embedding"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))


@dataclass(frozen=True, slots=True)
class ReductionBatch:
    vectors: tuple[tuple[float, ...], ...]
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "vectors", _vectors(self.vectors, "reduction"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))


@dataclass(frozen=True, slots=True)
class ClusterAssignment:
    labels: tuple[int, ...]
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        labels = tuple(self.labels)
        if not all(type(item) is int and item >= -1 for item in labels):
            raise ValueError("cluster labels must be integers >= -1")
        object.__setattr__(self, "labels", labels)
        object.__setattr__(self, "provenance", _provenance(self.provenance))


class EmbeddingBackend(Protocol):
    def embed(self, texts: Sequence[str]) -> EmbeddingBatch: ...


class ReductionBackend(Protocol):
    def reduce(self, vectors: Sequence[Sequence[float]], *, seed: int) -> ReductionBatch: ...


class ClusteringBackend(Protocol):
    def cluster(
        self,
        vectors: Sequence[Sequence[float]],
        *,
        min_cluster_size: int,
        allow_single_cluster: bool,
        seed: int,
    ) -> ClusterAssignment: ...


@dataclass(frozen=True, slots=True)
class HashingEmbeddingBackend:
    dimensions: int = 64

    def __post_init__(self) -> None:
        if type(self.dimensions) is not int or self.dimensions < 8:
            raise ValueError("dimensions must be an integer >= 8")

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        rows: list[tuple[float, ...]] = []
        for text in texts:
            if not isinstance(text, str) or not text.strip():
                raise ValueError("embedding texts must be non-empty strings")
            values = [0.0] * self.dimensions
            for token in _TOKEN_RE.findall(text.casefold()):
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "big") % self.dimensions
                values[index] += 1.0 if digest[4] & 1 else -1.0
            norm = math.sqrt(sum(value * value for value in values))
            rows.append(tuple(value / norm for value in values) if norm else tuple(values))
        return EmbeddingBatch(
            tuple(rows),
            {"component": "hashing_embedding", "version": "sha256-token-v1", "dimensions": self.dimensions},
        )


@dataclass(frozen=True, slots=True)
class IdentityReductionBackend:
    def reduce(self, vectors: Sequence[Sequence[float]], *, seed: int) -> ReductionBatch:
        return ReductionBatch(
            _vectors(vectors, "reduction input"),
            {"component": "identity_reduction", "seed": seed},
        )


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


@dataclass(frozen=True, slots=True)
class CosineGraphClusterer:
    similarity_threshold: float = 0.72

    def __post_init__(self) -> None:
        if not 0.0 <= self.similarity_threshold <= 1.0:
            raise ValueError("similarity_threshold must be in [0, 1]")

    def cluster(
        self,
        vectors: Sequence[Sequence[float]],
        *,
        min_cluster_size: int,
        allow_single_cluster: bool,
        seed: int,
    ) -> ClusterAssignment:
        rows = _vectors(vectors, "clustering input")
        if not rows:
            return ClusterAssignment((), {"component": "cosine_graph", "seed": seed})
        parents = list(range(len(rows)))

        def find(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        for left in range(len(rows)):
            for right in range(left + 1, len(rows)):
                if _cosine(rows[left], rows[right]) < self.similarity_threshold:
                    continue
                left_root, right_root = find(left), find(right)
                if left_root != right_root:
                    parents[max(left_root, right_root)] = min(left_root, right_root)
        groups: dict[int, list[int]] = {}
        for index in range(len(rows)):
            groups.setdefault(find(index), []).append(index)
        selected = [
            indexes for _, indexes in sorted(groups.items()) if len(indexes) >= min_cluster_size
        ]
        if len(selected) == 1 and not allow_single_cluster:
            selected = []
        labels = [-1] * len(rows)
        for label, indexes in enumerate(selected):
            for index in indexes:
                labels[index] = label
        return ClusterAssignment(
            tuple(labels),
            {
                "component": "cosine_graph",
                "version": "1",
                "similarity_threshold": self.similarity_threshold,
                "seed": seed,
            },
        )


@dataclass(frozen=True, slots=True)
class UMAPReductionBackend:
    n_neighbors: int = 15
    n_components: int = 5
    min_dist: float = 0.0
    metric: str = "cosine"

    def __post_init__(self) -> None:
        if type(self.n_neighbors) is not int or self.n_neighbors < 2:
            raise ValueError("n_neighbors must be an integer >= 2")
        if type(self.n_components) is not int or self.n_components < 1:
            raise ValueError("n_components must be a positive integer")
        if not isinstance(self.min_dist, int | float) or not math.isfinite(self.min_dist):
            raise ValueError("min_dist must be finite")
        if self.min_dist < 0:
            raise ValueError("min_dist must be non-negative")

    def reduce(self, vectors: Sequence[Sequence[float]], *, seed: int) -> ReductionBatch:
        rows = _vectors(vectors, "UMAP input")
        if len(rows) <= 2:
            return ReductionBatch(
                rows,
                {"component": "umap", "version": "0.5.3", "seed": seed, "bypassed": True},
            )
        import numpy as np
        from umap import UMAP

        neighbors = max(2, min(self.n_neighbors, len(rows) - 1))
        components = max(1, min(self.n_components, len(rows) - 2))
        model = UMAP(
            n_neighbors=neighbors,
            n_components=components,
            min_dist=float(self.min_dist),
            metric=self.metric,
            random_state=seed,
            init="random",
            low_memory=False,
        )
        reduced = model.fit_transform(np.asarray(rows, dtype=float))
        return ReductionBatch(
            tuple(tuple(float(value) for value in row) for row in reduced),
            {
                "component": "umap",
                "version": "0.5.3",
                "random_state": seed,
                "n_neighbors": neighbors,
                "n_components": components,
                "metric": self.metric,
                "init": "random",
            },
        )


@dataclass(frozen=True, slots=True)
class HDBSCANClusteringBackend:
    min_samples: int = 1
    metric: str = "euclidean"
    cluster_selection_method: str = "eom"

    def __post_init__(self) -> None:
        if type(self.min_samples) is not int or self.min_samples < 1:
            raise ValueError("min_samples must be a positive integer")
        if self.cluster_selection_method not in {"eom", "leaf"}:
            raise ValueError("cluster_selection_method must be eom or leaf")

    def cluster(
        self,
        vectors: Sequence[Sequence[float]],
        *,
        min_cluster_size: int,
        allow_single_cluster: bool,
        seed: int,
    ) -> ClusterAssignment:
        rows = _vectors(vectors, "HDBSCAN input")
        if not rows:
            return ClusterAssignment((), {"component": "hdbscan", "seed": seed})
        import numpy as np
        from hdbscan import HDBSCAN

        model = HDBSCAN(
            min_cluster_size=min_cluster_size,
            min_samples=self.min_samples,
            metric=self.metric,
            cluster_selection_method=self.cluster_selection_method,
            allow_single_cluster=allow_single_cluster,
            prediction_data=False,
            approx_min_span_tree=False,
            core_dist_n_jobs=1,
        )
        labels = model.fit_predict(np.asarray(rows, dtype=float))
        return ClusterAssignment(
            tuple(int(item) for item in labels),
            {
                "component": "hdbscan",
                "version": "0.8.33",
                "min_samples": self.min_samples,
                "metric": self.metric,
                "cluster_selection_method": self.cluster_selection_method,
                "allow_single_cluster": allow_single_cluster,
                "seed": seed,
            },
        )


@dataclass(frozen=True, slots=True)
class SentenceTransformerEmbeddingBackend:
    model: Any
    model_id: str
    revision: str
    weights_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise ValueError("model_id must be non-empty")
        if not isinstance(self.revision, str) or _COMMIT_RE.fullmatch(self.revision) is None:
            raise ValueError("revision must be an immutable 40-character commit SHA")
        if not isinstance(self.weights_sha256, str) or _SHA256_RE.fullmatch(self.weights_sha256) is None:
            raise ValueError("weights_sha256 must be a lowercase SHA-256 digest")
        if not hasattr(self.model, "encode"):
            raise TypeError("model must provide encode()")

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        values = self.model.encode(
            list(texts),
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return EmbeddingBatch(
            tuple(tuple(float(item) for item in row) for row in values),
            {
                "component": "sentence_transformer",
                "model_id": self.model_id,
                "revision": self.revision,
                "weights_sha256": self.weights_sha256,
            },
        )
