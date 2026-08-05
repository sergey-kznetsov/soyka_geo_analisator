"""Immutable contracts for deterministic text preprocessing and deduplication."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


def _required(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _required(value, field_name)


def _utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    return value.astimezone(UTC)


def _format_datetime(value: datetime) -> str:
    return _utc(value, "datetime").isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _json_value(value: object, field_name: str) -> Any:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field_name} keys must be strings")
            result[key] = _json_value(item, f"{field_name}.{key}")
        return result
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [
            _json_value(item, f"{field_name}[{index}]")
            for index, item in enumerate(value)
        ]
    raise ValueError(
        f"{field_name} contains unsupported value {type(value).__name__}"
    )


def _mapping(value: Mapping[str, Any] | None, field_name: str) -> Mapping[str, Any]:
    return MappingProxyType(_json_value(value or {}, field_name))


def _digest(value: object) -> str:
    encoded = json.dumps(
        _json_value(value, "digest"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class MessageDecision(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class DuplicateKind(str, Enum):
    UNIQUE = "unique"
    TECHNICAL_DUPLICATE = "technical_duplicate"
    CROSS_SOURCE_REPOST = "cross_source_repost"
    REPEATED_APPEAL = "repeated_appeal"


@dataclass(frozen=True, slots=True)
class PreprocessingConfig:
    """Versioned deterministic settings; no model or network dependency."""

    unicode_form: str = "NFKC"
    max_text_chars: int = 200_000
    min_model_text_chars: int = 2
    near_duplicate_similarity: float = 0.88
    near_duplicate_hamming_distance: int = 3
    repeated_appeal_min_seconds: int = 86_400
    drop_cross_source_reposts: bool = True
    technical_lines: tuple[str, ...] = (
        "ответить",
        "показать полностью",
        "читать далее",
        "развернуть",
        "свернуть",
    )
    recurrence_markers: tuple[str, ...] = (
        "снова",
        "опять",
        "повторно",
        "до сих пор",
        "уже неделю",
        "уже месяц",
        "не первый раз",
    )

    def __post_init__(self) -> None:
        if self.unicode_form not in {"NFC", "NFKC"}:
            raise ValueError("unicode_form must be NFC or NFKC")
        for field_name in (
            "max_text_chars",
            "min_model_text_chars",
            "repeated_appeal_min_seconds",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")
        if not 0.5 <= self.near_duplicate_similarity <= 1.0:
            raise ValueError("near_duplicate_similarity must be in [0.5, 1.0]")
        if not 0 <= self.near_duplicate_hamming_distance <= 16:
            raise ValueError("near_duplicate_hamming_distance must be in [0, 16]")
        if not isinstance(self.drop_cross_source_reposts, bool):
            raise TypeError("drop_cross_source_reposts must be a boolean")
        for field_name in ("technical_lines", "recurrence_markers"):
            values = tuple(
                _required(item, f"{field_name}[]").casefold()
                for item in getattr(self, field_name)
            )
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must not contain duplicates")
            object.__setattr__(self, field_name, values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "unicode_form": self.unicode_form,
            "max_text_chars": self.max_text_chars,
            "min_model_text_chars": self.min_model_text_chars,
            "near_duplicate_similarity": self.near_duplicate_similarity,
            "near_duplicate_hamming_distance": self.near_duplicate_hamming_distance,
            "repeated_appeal_min_seconds": self.repeated_appeal_min_seconds,
            "drop_cross_source_reposts": self.drop_cross_source_reposts,
            "technical_lines": list(self.technical_lines),
            "recurrence_markers": list(self.recurrence_markers),
        }

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())


@dataclass(frozen=True, slots=True)
class LanguageResult:
    code: str
    confidence: float
    cyrillic_letters: int
    latin_letters: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _required(self.code, "language.code"))
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("language.confidence must be in [0, 1]")
        for field_name in ("cyrillic_letters", "latin_letters"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ValueError(f"language.{field_name} must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "confidence": round(self.confidence, 6),
            "cyrillic_letters": self.cyrillic_letters,
            "latin_letters": self.latin_letters,
        }


@dataclass(frozen=True, slots=True)
class TransformationTrace:
    step: str
    version: str
    input_sha256: str
    output_sha256: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "step", _required(self.step, "trace.step"))
        object.__setattr__(self, "version", _required(self.version, "trace.version"))
        for field_name in ("input_sha256", "output_sha256"):
            value = _required(getattr(self, field_name), f"trace.{field_name}").lower()
            if _SHA256_RE.fullmatch(value) is None:
                raise ValueError(f"trace.{field_name} must be a SHA-256 digest")
            object.__setattr__(self, field_name, value)
        object.__setattr__(self, "details", _mapping(self.details, "trace.details"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "version": self.version,
            "input_sha256": self.input_sha256,
            "output_sha256": self.output_sha256,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class DuplicateDecision:
    kind: DuplicateKind = DuplicateKind.UNIQUE
    representative_key: str | None = None
    similarity: float = 0.0
    included_for_analysis: bool = True
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, DuplicateKind):
            object.__setattr__(self, "kind", DuplicateKind(self.kind))
        object.__setattr__(
            self,
            "representative_key",
            _optional(self.representative_key, "duplicate.representative_key"),
        )
        if not 0.0 <= self.similarity <= 1.0:
            raise ValueError("duplicate.similarity must be in [0, 1]")
        if not isinstance(self.included_for_analysis, bool):
            raise TypeError("duplicate.included_for_analysis must be a boolean")
        object.__setattr__(
            self,
            "reasons",
            tuple(_required(item, "duplicate.reasons[]") for item in self.reasons),
        )
        if self.kind is DuplicateKind.UNIQUE and self.representative_key is not None:
            raise ValueError("unique message must not reference a representative")
        if self.kind is not DuplicateKind.UNIQUE and self.representative_key is None:
            raise ValueError("duplicate decision requires representative_key")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "representative_key": self.representative_key,
            "similarity": round(self.similarity, 6),
            "included_for_analysis": self.included_for_analysis,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class PreprocessedMessage:
    source: str
    external_id: str
    original_text: str
    normalized_text: str
    model_text: str
    quote_texts: tuple[str, ...]
    original_published_at: str
    published_at_utc: datetime | None
    url: str | None
    author_id: str | None
    latitude: float | None
    longitude: float | None
    metadata: Mapping[str, Any]
    language: LanguageResult
    decision: MessageDecision
    rejection_reasons: tuple[str, ...]
    fingerprints: Mapping[str, Any]
    duplicate: DuplicateDecision
    transformations: tuple[TransformationTrace, ...]

    def __post_init__(self) -> None:
        for field_name in ("source", "external_id", "original_text"):
            object.__setattr__(
                self, field_name, _required(getattr(self, field_name), field_name)
            )
        object.__setattr__(self, "url", _optional(self.url, "url"))
        object.__setattr__(self, "author_id", _optional(self.author_id, "author_id"))
        object.__setattr__(
            self,
            "quote_texts",
            tuple(_required(item, "quote_texts[]") for item in self.quote_texts),
        )
        object.__setattr__(
            self,
            "original_published_at",
            _required(self.original_published_at, "original_published_at"),
        )
        if self.published_at_utc is not None:
            object.__setattr__(
                self,
                "published_at_utc",
                _utc(self.published_at_utc, "published_at_utc"),
            )
        object.__setattr__(self, "metadata", _mapping(self.metadata, "metadata"))
        object.__setattr__(
            self, "fingerprints", _mapping(self.fingerprints, "fingerprints")
        )
        if not isinstance(self.decision, MessageDecision):
            object.__setattr__(self, "decision", MessageDecision(self.decision))
        object.__setattr__(
            self,
            "rejection_reasons",
            tuple(_required(item, "rejection_reasons[]") for item in self.rejection_reasons),
        )
        object.__setattr__(self, "transformations", tuple(self.transformations))
        if self.decision is MessageDecision.ACCEPTED:
            if not self.model_text:
                raise ValueError("accepted message requires model_text")
            if self.rejection_reasons:
                raise ValueError("accepted message cannot contain rejection reasons")
        elif not self.rejection_reasons:
            raise ValueError("rejected message requires rejection reasons")

    @property
    def message_key(self) -> str:
        return f"{self.source}:{self.external_id}"

    def with_duplicate(self, duplicate: DuplicateDecision) -> PreprocessedMessage:
        return PreprocessedMessage(
            source=self.source,
            external_id=self.external_id,
            original_text=self.original_text,
            normalized_text=self.normalized_text,
            model_text=self.model_text,
            quote_texts=self.quote_texts,
            original_published_at=self.original_published_at,
            published_at_utc=self.published_at_utc,
            url=self.url,
            author_id=self.author_id,
            latitude=self.latitude,
            longitude=self.longitude,
            metadata=self.metadata,
            language=self.language,
            decision=self.decision,
            rejection_reasons=self.rejection_reasons,
            fingerprints=self.fingerprints,
            duplicate=duplicate,
            transformations=self.transformations,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "external_id": self.external_id,
            "message_key": self.message_key,
            "original_text": self.original_text,
            "normalized_text": self.normalized_text,
            "model_text": self.model_text,
            "quote_texts": list(self.quote_texts),
            "original_published_at": self.original_published_at,
            "published_at_utc": (
                _format_datetime(self.published_at_utc)
                if self.published_at_utc is not None
                else None
            ),
            "url": self.url,
            "author_id": self.author_id,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "metadata": dict(self.metadata),
            "language": self.language.to_dict(),
            "decision": self.decision.value,
            "rejection_reasons": list(self.rejection_reasons),
            "fingerprints": dict(self.fingerprints),
            "duplicate": self.duplicate.to_dict(),
            "transformations": [item.to_dict() for item in self.transformations],
        }


@dataclass(frozen=True, slots=True)
class PreprocessingStats:
    received: int
    accepted: int
    rejected: int
    included_for_analysis: int
    technical_duplicates: int
    cross_source_reposts: int
    repeated_appeals: int

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ValueError(f"stats.{field_name} must be non-negative")
        if self.accepted + self.rejected != self.received:
            raise ValueError("accepted plus rejected must equal received")
        if self.included_for_analysis > self.accepted:
            raise ValueError("included_for_analysis cannot exceed accepted")

    def to_dict(self) -> dict[str, int]:
        return {
            field_name: getattr(self, field_name)
            for field_name in self.__dataclass_fields__
        }


@dataclass(frozen=True, slots=True)
class PreprocessingResult:
    schema_version: str
    algorithm_version: str
    config_digest: str
    input_digest: str
    output_digest: str
    messages: tuple[PreprocessedMessage, ...]
    stats: PreprocessingStats

    def __post_init__(self) -> None:
        for field_name in (
            "schema_version",
            "algorithm_version",
            "config_digest",
            "input_digest",
            "output_digest",
        ):
            value = _required(getattr(self, field_name), field_name)
            if field_name.endswith("digest") and _SHA256_RE.fullmatch(value) is None:
                raise ValueError(f"{field_name} must be a SHA-256 digest")
            object.__setattr__(self, field_name, value)
        object.__setattr__(self, "messages", tuple(self.messages))

    @property
    def analysis_messages(self) -> tuple[PreprocessedMessage, ...]:
        return tuple(
            message
            for message in self.messages
            if message.decision is MessageDecision.ACCEPTED
            and message.duplicate.included_for_analysis
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "algorithm_version": self.algorithm_version,
            "config_digest": self.config_digest,
            "input_digest": self.input_digest,
            "output_digest": self.output_digest,
            "stats": self.stats.to_dict(),
            "messages": [message.to_dict() for message in self.messages],
            "analysis_message_keys": [
                message.message_key for message in self.analysis_messages
            ],
        }


__all__ = [
    "DuplicateDecision",
    "DuplicateKind",
    "LanguageResult",
    "MessageDecision",
    "PreprocessedMessage",
    "PreprocessingConfig",
    "PreprocessingResult",
    "PreprocessingStats",
    "TransformationTrace",
]
