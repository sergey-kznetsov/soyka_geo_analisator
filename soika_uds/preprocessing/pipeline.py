"""Deterministic text normalization and duplicate classification."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any, Protocol

from .models import (
    DuplicateKind,
    LanguageCode,
    PreprocessedMessage,
    PreprocessingBatchResult,
    PreprocessingError,
    TransformationStep,
)

_TAG_RE = re.compile(r"<[^>]*>")
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
_SPACE_RE = re.compile(r"[\s\u00a0]+")
_SERVICE_LINE_RE = re.compile(
    r"^(?:реклама|подписывайтесь|источник|читать далее|комментарий удал[её]н)\b.*$",
    re.IGNORECASE,
)
_QUOTE_LINE_RE = re.compile(r"^\s*(?:>|»|цитата:|quote:)\s*(.+)$", re.IGNORECASE)
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+", re.UNICODE)


class SourceMessageLike(Protocol):
    source: str
    external_id: str
    text: str
    published_at: datetime
    url: str | None
    author_id: str | None
    metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PreprocessingConfig:
    near_duplicate_threshold: float = 0.88
    min_near_duplicate_tokens: int = 5
    repeat_window_seconds: int = 86_400
    remove_service_lines: bool = True

    def __post_init__(self) -> None:
        if not 0.0 <= self.near_duplicate_threshold <= 1.0:
            raise PreprocessingError("near_duplicate_threshold must be in [0, 1]")
        if type(self.min_near_duplicate_tokens) is not int or (
            self.min_near_duplicate_tokens < 1
        ):
            raise PreprocessingError("min_near_duplicate_tokens must be positive")
        if type(self.repeat_window_seconds) is not int or self.repeat_window_seconds < 0:
            raise PreprocessingError("repeat_window_seconds must be non-negative")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _step(name: str, before: str, after: str, **details: Any) -> TransformationStep:
    return TransformationStep(
        name=name,
        changed=before != after,
        before_sha256=_digest(before),
        after_sha256=_digest(after),
        details=details,
    )


def normalize_unicode(value: str) -> str:
    return unicodedata.normalize("NFKC", value).replace("\u200b", "")


def strip_html(value: str) -> str:
    without_active = _SCRIPT_STYLE_RE.sub(" ", value)
    return html.unescape(_TAG_RE.sub(" ", without_active))


def normalize_whitespace(value: str) -> str:
    lines = [_SPACE_RE.sub(" ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def remove_service_text(value: str) -> str:
    return "\n".join(
        line for line in value.splitlines() if not _SERVICE_LINE_RE.match(line)
    )


def split_quotes(value: str) -> tuple[str, tuple[str, ...]]:
    author_lines: list[str] = []
    quotes: list[str] = []
    for line in value.splitlines():
        match = _QUOTE_LINE_RE.match(line)
        if match:
            quote = match.group(1).strip()
            if quote:
                quotes.append(quote)
        else:
            author_lines.append(line)
    return "\n".join(author_lines).strip(), tuple(quotes)


def detect_language(value: str) -> LanguageCode:
    cyrillic = len(_CYRILLIC_RE.findall(value))
    latin = len(_LATIN_RE.findall(value))
    if not cyrillic and not latin:
        return LanguageCode.UNKNOWN
    total = cyrillic + latin
    if cyrillic / total >= 0.8:
        return LanguageCode.RU
    if latin / total >= 0.8:
        return LanguageCode.EN
    return LanguageCode.MIXED


def semantic_text(value: str) -> str:
    normalized = normalize_unicode(value).casefold().replace("ё", "е")
    return " ".join(_TOKEN_RE.findall(normalized))


def token_signature(value: str) -> tuple[str, ...]:
    return tuple(semantic_text(value).split())


def similarity(left: str, right: str) -> float:
    left_tokens = token_signature(left)
    right_tokens = token_signature(right)
    if not left_tokens or not right_tokens:
        return 0.0
    sequence_score = SequenceMatcher(None, left_tokens, right_tokens).ratio()
    left_set = set(left_tokens)
    right_set = set(right_tokens)
    jaccard = len(left_set & right_set) / len(left_set | right_set)
    return round((sequence_score + jaccard) / 2.0, 6)


def normalize_timestamp(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise PreprocessingError("published_at must be datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise PreprocessingError("published_at must include a UTC offset")
    return value.astimezone(UTC)


class MessagePreprocessor:
    def __init__(self, config: PreprocessingConfig | None = None) -> None:
        self._config = config or PreprocessingConfig()

    def preprocess(self, message: SourceMessageLike) -> PreprocessedMessage:
        raw_text = message.text
        if not isinstance(raw_text, str):
            raise PreprocessingError("message text must be a string")
        steps: list[TransformationStep] = []

        current = normalize_unicode(raw_text)
        steps.append(_step("unicode_nfkc", raw_text, current, form="NFKC"))

        cleaned = strip_html(current)
        steps.append(_step("html_cleanup", current, cleaned))
        current = cleaned

        cleaned = normalize_whitespace(current)
        steps.append(_step("whitespace", current, cleaned))
        current = cleaned

        if self._config.remove_service_lines:
            cleaned = remove_service_text(current)
            steps.append(_step("service_text", current, cleaned))
            current = cleaned

        author_text, quotes = split_quotes(current)
        steps.append(
            _step("quote_extraction", current, author_text, quotes_extracted=len(quotes))
        )
        normalized_text = current.strip()
        fingerprint_text = semantic_text(author_text or normalized_text)
        content_sha256 = _digest(normalized_text)
        semantic_fingerprint = _digest(fingerprint_text)

        return PreprocessedMessage(
            source=message.source,
            external_id=message.external_id,
            raw_text=raw_text,
            normalized_text=normalized_text,
            author_text=author_text,
            quotes=quotes,
            language=detect_language(author_text or normalized_text),
            published_at=normalize_timestamp(message.published_at),
            url=getattr(message, "url", None),
            author_id=getattr(message, "author_id", None),
            metadata=dict(getattr(message, "metadata", {})),
            content_sha256=content_sha256,
            semantic_fingerprint=semantic_fingerprint,
            transformations=tuple(steps),
        )


class DuplicateDetector:
    def __init__(self, config: PreprocessingConfig | None = None) -> None:
        self._config = config or PreprocessingConfig()

    def classify(
        self, messages: Iterable[PreprocessedMessage]
    ) -> PreprocessingBatchResult:
        ordered = sorted(
            tuple(messages),
            key=lambda item: (item.published_at, item.source, item.external_id),
        )
        accepted: list[PreprocessedMessage] = []
        exact_index: dict[str, PreprocessedMessage] = {}
        exact_count = 0
        near_count = 0

        for message in ordered:
            exact = exact_index.get(message.semantic_fingerprint)
            if exact is not None:
                accepted.append(
                    replace(
                        message,
                        duplicate_kind=DuplicateKind.EXACT,
                        duplicate_of=exact.message_key,
                        similarity=1.0,
                    )
                )
                exact_count += 1
                continue

            near_match: PreprocessedMessage | None = None
            near_score = 0.0
            token_count = len(token_signature(message.author_text))
            if token_count >= self._config.min_near_duplicate_tokens:
                for candidate in accepted:
                    if candidate.duplicate_kind is not DuplicateKind.UNIQUE:
                        continue
                    delta = abs(
                        (message.published_at - candidate.published_at).total_seconds()
                    )
                    if delta > self._config.repeat_window_seconds:
                        continue
                    score = similarity(message.author_text, candidate.author_text)
                    if score > near_score:
                        near_match = candidate
                        near_score = score

            if near_match is not None and (
                near_score >= self._config.near_duplicate_threshold
            ):
                accepted.append(
                    replace(
                        message,
                        duplicate_kind=DuplicateKind.NEAR,
                        duplicate_of=near_match.message_key,
                        similarity=near_score,
                    )
                )
                near_count += 1
                continue

            accepted.append(message)
            exact_index[message.semantic_fingerprint] = message

        return PreprocessingBatchResult(
            messages=tuple(accepted),
            unique_count=len(accepted) - exact_count - near_count,
            exact_duplicate_count=exact_count,
            near_duplicate_count=near_count,
        )


class PreprocessingPipeline:
    def __init__(self, config: PreprocessingConfig | None = None) -> None:
        self._preprocessor = MessagePreprocessor(config)
        self._detector = DuplicateDetector(config)

    def run(self, messages: Iterable[SourceMessageLike]) -> PreprocessingBatchResult:
        return self._detector.classify(
            self._preprocessor.preprocess(message) for message in messages
        )
