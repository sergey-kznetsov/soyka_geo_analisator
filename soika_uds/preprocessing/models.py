"""Immutable contracts for deterministic message preprocessing."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any


class PreprocessingError(ValueError):
    """Raised when preprocessing input or configuration is invalid."""


class DuplicateKind(str, Enum):
    UNIQUE = "unique"
    EXACT = "exact"
    NEAR = "near"


class LanguageCode(str, Enum):
    RU = "ru"
    EN = "en"
    MIXED = "mixed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TransformationStep:
    name: str
    changed: bool
    before_sha256: str
    after_sha256: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise PreprocessingError("transformation step name must not be empty")
        for field_name in ("before_sha256", "after_sha256"):
            value = getattr(self, field_name)
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise PreprocessingError(f"{field_name} must be a lowercase SHA-256")
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))


@dataclass(frozen=True, slots=True)
class PreprocessedMessage:
    source: str
    external_id: str
    raw_text: str
    normalized_text: str
    author_text: str
    quotes: tuple[str, ...]
    language: LanguageCode
    published_at: datetime
    url: str | None
    author_id: str | None
    metadata: Mapping[str, Any]
    content_sha256: str
    semantic_fingerprint: str
    duplicate_kind: DuplicateKind = DuplicateKind.UNIQUE
    duplicate_of: str | None = None
    similarity: float | None = None
    transformations: tuple[TransformationStep, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("source", "external_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise PreprocessingError(f"{field_name} must not be empty")
        for field_name in ("raw_text", "normalized_text", "author_text"):
            if not isinstance(getattr(self, field_name), str):
                raise PreprocessingError(f"{field_name} must be a string")
        object.__setattr__(self, "quotes", tuple(self.quotes))
        if not isinstance(self.language, LanguageCode):
            object.__setattr__(self, "language", LanguageCode(self.language))
        if self.published_at.tzinfo is None or self.published_at.utcoffset() is None:
            raise PreprocessingError("published_at must include a UTC offset")
        object.__setattr__(self, "published_at", self.published_at.astimezone(UTC))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        for field_name in ("content_sha256", "semantic_fingerprint"):
            value = getattr(self, field_name)
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise PreprocessingError(f"{field_name} must be a lowercase SHA-256")
        if not isinstance(self.duplicate_kind, DuplicateKind):
            object.__setattr__(
                self, "duplicate_kind", DuplicateKind(self.duplicate_kind)
            )
        if self.duplicate_kind is DuplicateKind.UNIQUE and self.duplicate_of is not None:
            raise PreprocessingError("unique message cannot reference duplicate_of")
        if self.duplicate_kind is not DuplicateKind.UNIQUE and not self.duplicate_of:
            raise PreprocessingError("duplicate message requires duplicate_of")
        if self.similarity is not None:
            if not isinstance(self.similarity, int | float) or not math.isfinite(
                self.similarity
            ):
                raise PreprocessingError("similarity must be finite")
            if not 0.0 <= float(self.similarity) <= 1.0:
                raise PreprocessingError("similarity must be in [0, 1]")
        object.__setattr__(self, "transformations", tuple(self.transformations))

    @property
    def message_key(self) -> str:
        return f"{self.source}:{self.external_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "external_id": self.external_id,
            "raw_text": self.raw_text,
            "normalized_text": self.normalized_text,
            "author_text": self.author_text,
            "quotes": list(self.quotes),
            "language": self.language.value,
            "published_at": self.published_at.isoformat().replace("+00:00", "Z"),
            "url": self.url,
            "author_id": self.author_id,
            "metadata": dict(self.metadata),
            "content_sha256": self.content_sha256,
            "semantic_fingerprint": self.semantic_fingerprint,
            "duplicate_kind": self.duplicate_kind.value,
            "duplicate_of": self.duplicate_of,
            "similarity": self.similarity,
            "transformations": [
                {
                    "name": step.name,
                    "changed": step.changed,
                    "before_sha256": step.before_sha256,
                    "after_sha256": step.after_sha256,
                    "details": dict(step.details),
                }
                for step in self.transformations
            ],
        }


@dataclass(frozen=True, slots=True)
class PreprocessingBatchResult:
    messages: tuple[PreprocessedMessage, ...]
    unique_count: int
    exact_duplicate_count: int
    near_duplicate_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "messages", tuple(self.messages))
        counts = (
            self.unique_count,
            self.exact_duplicate_count,
            self.near_duplicate_count,
        )
        if any(type(value) is not int or value < 0 for value in counts):
            raise PreprocessingError("batch counts must be non-negative integers")
        if sum(counts) != len(self.messages):
            raise PreprocessingError("batch counts must match message count")


def immutable_metadata(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))


def string_tuple(value: Sequence[str]) -> tuple[str, ...]:
    return tuple(str(item) for item in value)
