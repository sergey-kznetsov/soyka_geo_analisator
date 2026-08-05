"""Deterministic preprocessing and duplicate classification for SourceMessage."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..contracts import SourceMessage
from .models import (
    DuplicateDecision,
    DuplicateKind,
    LanguageResult,
    MessageDecision,
    PreprocessedMessage,
    PreprocessingConfig,
    PreprocessingResult,
    PreprocessingStats,
    TransformationTrace,
)

SCHEMA_VERSION = "1.0.0"
ALGORITHM_VERSION = "1.0.0"
_TRACE_VERSION = "1"
_HTML_TAG_RE = re.compile(r"<[A-Za-z!/][^>]*>")
_TOKEN_RE = re.compile(r"[\w-]+", flags=re.UNICODE)
_TRACKING_PARAMETERS = frozenset(
    {"fbclid", "gclid", "yclid", "_openstat", "mc_cid", "mc_eid"}
)
_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
)
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template"})


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _trace(
    step: str,
    before: str,
    after: str,
    **details: Any,
) -> TransformationTrace:
    return TransformationTrace(
        step=step,
        version=_TRACE_VERSION,
        input_sha256=_sha256_text(before),
        output_sha256=_sha256_text(after),
        details=details,
    )


class _StructuredHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.visible: list[str] = []
        self.quotes: list[list[str]] = []
        self._quote_depth = 0
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized = tag.casefold()
        if normalized in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if normalized == "blockquote":
            self._quote_depth += 1
            self.quotes.append([])
        if normalized in _BLOCK_TAGS:
            self._append("\n")

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in _SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if normalized in _BLOCK_TAGS:
            self._append("\n")
        if normalized == "blockquote" and self._quote_depth:
            self._quote_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not data:
            return
        self._append(data)

    def _append(self, value: str) -> None:
        if self._quote_depth and self.quotes:
            self.quotes[-1].append(value)
        else:
            self.visible.append(value)

    @property
    def visible_text(self) -> str:
        return "".join(self.visible)

    @property
    def quote_texts(self) -> tuple[str, ...]:
        return tuple("".join(parts) for parts in self.quotes)


@dataclass(frozen=True, slots=True)
class _NormalizedText:
    normalized_text: str
    model_text: str
    quote_texts: tuple[str, ...]
    transformations: tuple[TransformationTrace, ...]


def _html_to_text(value: str) -> tuple[str, tuple[str, ...]]:
    if _HTML_TAG_RE.search(value) is None:
        return unescape(value), ()
    parser = _StructuredHtmlParser()
    parser.feed(value)
    parser.close()
    return parser.visible_text, parser.quote_texts


def _remove_controls(value: str) -> str:
    result: list[str] = []
    for character in value:
        if character in {"\n", "\t"}:
            result.append(character)
            continue
        category = unicodedata.category(character)
        if category.startswith("C"):
            continue
        result.append(character)
    return "".join(result)


def _normalize_whitespace(value: str) -> str:
    normalized_lines: list[str] = []
    previous_blank = False
    for raw_line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = " ".join(raw_line.replace("\t", " ").split())
        if not line:
            if normalized_lines and not previous_blank:
                normalized_lines.append("")
            previous_blank = True
            continue
        normalized_lines.append(line)
        previous_blank = False
    while normalized_lines and not normalized_lines[-1]:
        normalized_lines.pop()
    return "\n".join(normalized_lines).strip()


def _remove_technical_lines(value: str, technical_lines: frozenset[str]) -> str:
    result = [
        line
        for line in value.splitlines()
        if line.strip().casefold() not in technical_lines
    ]
    return "\n".join(result)


def _split_plain_quotes(value: str) -> tuple[str, tuple[str, ...]]:
    quotes: list[str] = []
    author_lines: list[str] = []
    bracket_quote: list[str] | None = None
    for line in value.splitlines():
        stripped = line.strip()
        folded = stripped.casefold()
        if folded == "[quote]":
            bracket_quote = []
            continue
        if folded == "[/quote]" and bracket_quote is not None:
            quote = _normalize_whitespace("\n".join(bracket_quote))
            if quote:
                quotes.append(quote)
            bracket_quote = None
            continue
        if bracket_quote is not None:
            bracket_quote.append(line)
            continue
        if stripped.startswith(">"):
            quote = stripped.lstrip(">").strip()
            if quote:
                quotes.append(quote)
            continue
        author_lines.append(line)
    if bracket_quote is not None:
        quote = _normalize_whitespace("\n".join(bracket_quote))
        if quote:
            quotes.append(quote)
    return _normalize_whitespace("\n".join(author_lines)), tuple(quotes)


def _normalize_text(value: str, config: PreprocessingConfig) -> _NormalizedText:
    traces: list[TransformationTrace] = []
    visible, html_quotes = _html_to_text(value)
    traces.append(
        _trace("html_to_text", value, visible, extracted_quotes=len(html_quotes))
    )

    normalized = unicodedata.normalize(config.unicode_form, visible)
    traces.append(
        _trace(
            "unicode_normalization",
            visible,
            normalized,
            form=config.unicode_form,
        )
    )

    controls_removed = _remove_controls(normalized)
    traces.append(_trace("remove_controls", normalized, controls_removed))

    whitespace_normalized = _normalize_whitespace(controls_removed)
    traces.append(
        _trace("normalize_whitespace", controls_removed, whitespace_normalized)
    )

    technical_lines = frozenset(config.technical_lines)
    technical_removed = _remove_technical_lines(
        whitespace_normalized,
        technical_lines,
    )
    technical_removed = _normalize_whitespace(technical_removed)
    traces.append(
        _trace(
            "remove_technical_lines",
            whitespace_normalized,
            technical_removed,
            configured_lines=len(technical_lines),
        )
    )

    model_text, plain_quotes = _split_plain_quotes(technical_removed)
    normalized_quotes = tuple(
        quote
        for quote in (
            _normalize_whitespace(
                _remove_controls(unicodedata.normalize(config.unicode_form, item))
            )
            for item in (*html_quotes, *plain_quotes)
        )
        if quote
    )
    traces.append(
        _trace(
            "separate_quotes",
            technical_removed,
            model_text,
            quote_count=len(normalized_quotes),
        )
    )
    return _NormalizedText(
        normalized_text=technical_removed,
        model_text=model_text,
        quote_texts=normalized_quotes,
        transformations=tuple(traces),
    )


def detect_language(value: str) -> LanguageResult:
    cyrillic = sum("CYRILLIC" in unicodedata.name(character, "") for character in value)
    latin = sum("LATIN" in unicodedata.name(character, "") for character in value)
    total = cyrillic + latin
    if total < 3:
        return LanguageResult("unknown", 0.0, cyrillic, latin)
    cyrillic_ratio = cyrillic / total
    latin_ratio = latin / total
    if cyrillic_ratio >= 0.8:
        return LanguageResult("ru", cyrillic_ratio, cyrillic, latin)
    if latin_ratio >= 0.8:
        return LanguageResult("en", latin_ratio, cyrillic, latin)
    return LanguageResult(
        "mixed",
        max(cyrillic_ratio, latin_ratio),
        cyrillic,
        latin,
    )


def canonicalize_url(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    parsed = urlsplit(value.strip())
    if not parsed.scheme or not parsed.netloc:
        return value.strip()
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in _TRACKING_PARAMETERS
    ]
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            path,
            urlencode(sorted(query)),
            "",
        )
    )


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token.casefold()
        for token in _TOKEN_RE.findall(value)
        if token.strip("-_")
    )


def _simhash64(value: str) -> int:
    tokens = _tokens(value)
    features: Sequence[str]
    if len(tokens) >= 3:
        features = tuple(
            " ".join(tokens[index : index + 3])
            for index in range(len(tokens) - 2)
        )
    elif tokens:
        features = tokens
    else:
        compact = " ".join(value.casefold().split())
        features = tuple(
            compact[index : index + 3]
            for index in range(max(1, len(compact) - 2))
        )
    vector = [0] * 64
    for feature in features:
        hashed = int.from_bytes(
            hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest(),
            "big",
        )
        for bit in range(64):
            vector[bit] += 1 if hashed & (1 << bit) else -1
    result = 0
    for bit, weight in enumerate(vector):
        if weight >= 0:
            result |= 1 << bit
    return result


def _hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def _bands(simhash: int) -> tuple[tuple[int, int], ...]:
    mask = (1 << 16) - 1
    return tuple((index, (simhash >> (index * 16)) & mask) for index in range(4))


def _text_similarity(left: str, right: str) -> float:
    left_tokens = set(_tokens(left))
    right_tokens = set(_tokens(right))
    if left_tokens or right_tokens:
        union = left_tokens | right_tokens
        jaccard = len(left_tokens & right_tokens) / len(union)
    else:
        jaccard = 0.0
    sequence = SequenceMatcher(None, left.casefold(), right.casefold()).ratio()
    return max(jaccard, sequence)


def _message_payload(message: SourceMessage) -> dict[str, Any]:
    return {
        "source": message.source,
        "external_id": message.external_id,
        "text": message.text,
        "published_at": message.published_at.isoformat(),
        "url": message.url,
        "author_id": message.author_id,
        "latitude": message.latitude,
        "longitude": message.longitude,
        "metadata": dict(message.metadata),
    }


def source_message_to_dict(message: SourceMessage) -> dict[str, Any]:
    return _message_payload(message)


def source_message_from_dict(payload: Mapping[str, Any]) -> SourceMessage:
    published = payload.get("published_at")
    if not isinstance(published, str):
        raise ValueError("source message published_at must be an ISO 8601 string")
    candidate = published.strip()
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        published_at = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise ValueError("source message published_at is invalid") from error
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise ValueError("source message metadata must be an object")
    return SourceMessage(
        source=payload["source"],
        external_id=payload["external_id"],
        text=payload["text"],
        published_at=published_at,
        url=payload.get("url"),
        author_id=payload.get("author_id"),
        latitude=payload.get("latitude"),
        longitude=payload.get("longitude"),
        metadata=metadata,
    )


def _preprocess_one(
    message: SourceMessage,
    config: PreprocessingConfig,
) -> PreprocessedMessage:
    normalized = _normalize_text(message.text, config)
    rejection_reasons: list[str] = []
    if len(message.text) > config.max_text_chars:
        rejection_reasons.append("TEXT_TOO_LONG")
    if len(normalized.model_text) < config.min_model_text_chars:
        rejection_reasons.append("MODEL_TEXT_TOO_SHORT")
    published_at_utc: datetime | None
    if message.published_at.tzinfo is None or message.published_at.utcoffset() is None:
        published_at_utc = None
        rejection_reasons.append("PUBLISHED_AT_MISSING_TIMEZONE")
    else:
        published_at_utc = message.published_at.astimezone(UTC)

    canonical_url = canonicalize_url(message.url)
    simhash = _simhash64(normalized.model_text)
    fingerprints: dict[str, Any] = {
        "identity_sha256": _sha256_text(
            f"{message.source.casefold()}\0{message.external_id}"
        ),
        "normalized_text_sha256": _sha256_text(normalized.normalized_text.casefold()),
        "model_text_sha256": _sha256_text(normalized.model_text.casefold()),
        "simhash64": f"{simhash:016x}",
    }
    if canonical_url is not None:
        fingerprints["canonical_url"] = canonical_url
        fingerprints["canonical_url_sha256"] = _sha256_text(canonical_url)

    decision = (
        MessageDecision.REJECTED if rejection_reasons else MessageDecision.ACCEPTED
    )
    return PreprocessedMessage(
        source=message.source,
        external_id=message.external_id,
        original_text=message.text,
        normalized_text=normalized.normalized_text,
        model_text=normalized.model_text,
        quote_texts=normalized.quote_texts,
        original_published_at=message.published_at.isoformat(),
        published_at_utc=published_at_utc,
        url=message.url,
        author_id=message.author_id,
        latitude=message.latitude,
        longitude=message.longitude,
        metadata=message.metadata,
        language=detect_language(normalized.model_text),
        decision=decision,
        rejection_reasons=tuple(rejection_reasons),
        fingerprints=fingerprints,
        duplicate=DuplicateDecision(
            included_for_analysis=decision is MessageDecision.ACCEPTED
        ),
        transformations=normalized.transformations,
    )


def _sort_key(message: PreprocessedMessage) -> tuple[datetime, str, str, str]:
    timestamp = message.published_at_utc or datetime.max.replace(tzinfo=UTC)
    return (
        timestamp,
        message.source.casefold(),
        message.external_id,
        str(message.fingerprints["model_text_sha256"]),
    )


def _has_recurrence_marker(
    message: PreprocessedMessage,
    config: PreprocessingConfig,
) -> bool:
    folded = message.model_text.casefold()
    return any(marker in folded for marker in config.recurrence_markers)


def _is_repeated_appeal(
    current: PreprocessedMessage,
    representative: PreprocessedMessage,
    config: PreprocessingConfig,
) -> bool:
    if current.source != representative.source:
        return False
    if (
        current.fingerprints["identity_sha256"]
        == representative.fingerprints["identity_sha256"]
    ):
        return False
    if _has_recurrence_marker(current, config):
        return True
    if current.published_at_utc is None or representative.published_at_utc is None:
        return False
    gap = (current.published_at_utc - representative.published_at_utc).total_seconds()
    return gap >= config.repeated_appeal_min_seconds


def _duplicate_decision(
    current: PreprocessedMessage,
    representative: PreprocessedMessage,
    config: PreprocessingConfig,
    *,
    similarity: float,
    reason: str,
) -> DuplicateDecision:
    repeated = _is_repeated_appeal(current, representative, config)
    if repeated:
        return DuplicateDecision(
            kind=DuplicateKind.REPEATED_APPEAL,
            representative_key=representative.message_key,
            similarity=similarity,
            included_for_analysis=True,
            reasons=(reason, "recurrence_or_time_gap"),
        )
    if current.source != representative.source:
        return DuplicateDecision(
            kind=DuplicateKind.CROSS_SOURCE_REPOST,
            representative_key=representative.message_key,
            similarity=similarity,
            included_for_analysis=not config.drop_cross_source_reposts,
            reasons=(reason, "different_source"),
        )
    return DuplicateDecision(
        kind=DuplicateKind.TECHNICAL_DUPLICATE,
        representative_key=representative.message_key,
        similarity=similarity,
        included_for_analysis=False,
        reasons=(reason,),
    )


def _classify_duplicates(
    messages: Sequence[PreprocessedMessage],
    config: PreprocessingConfig,
) -> tuple[PreprocessedMessage, ...]:
    identity_index: dict[str, int] = {}
    url_index: dict[str, int] = {}
    text_index: dict[str, list[int]] = defaultdict(list)
    band_index: dict[tuple[int, int], set[int]] = defaultdict(set)
    result: list[PreprocessedMessage] = list(messages)

    for index, current in enumerate(result):
        if current.decision is MessageDecision.REJECTED:
            continue
        representative_index: int | None = None
        similarity = 0.0
        reason = ""
        identity = str(current.fingerprints["identity_sha256"])
        url_digest = current.fingerprints.get("canonical_url_sha256")
        text_digest = str(current.fingerprints["model_text_sha256"])
        if identity in identity_index:
            representative_index = identity_index[identity]
            similarity = 1.0
            reason = "same_source_external_id"
        elif isinstance(url_digest, str) and url_digest in url_index:
            representative_index = url_index[url_digest]
            similarity = 1.0
            reason = "same_canonical_url"
        elif text_index[text_digest]:
            representative_index = text_index[text_digest][0]
            similarity = 1.0
            reason = "same_model_text"
        else:
            simhash = int(str(current.fingerprints["simhash64"]), 16)
            candidates: set[int] = set()
            for band in _bands(simhash):
                candidates.update(band_index.get(band, set()))
            best: tuple[float, int] | None = None
            for candidate_index in sorted(candidates):
                candidate = result[candidate_index]
                candidate_hash = int(
                    str(candidate.fingerprints["simhash64"]),
                    16,
                )
                if (
                    _hamming_distance(simhash, candidate_hash)
                    > config.near_duplicate_hamming_distance
                ):
                    continue
                candidate_similarity = _text_similarity(
                    current.model_text,
                    candidate.model_text,
                )
                if candidate_similarity < config.near_duplicate_similarity:
                    continue
                if best is None or candidate_similarity > best[0]:
                    best = (candidate_similarity, candidate_index)
            if best is not None:
                similarity, representative_index = best
                reason = "near_duplicate_text"

        if representative_index is not None:
            representative = result[representative_index]
            result[index] = current.with_duplicate(
                _duplicate_decision(
                    current,
                    representative,
                    config,
                    similarity=similarity,
                    reason=reason,
                )
            )
            current = result[index]

        if current.duplicate.included_for_analysis:
            identity_index.setdefault(identity, index)
            if isinstance(url_digest, str):
                url_index.setdefault(url_digest, index)
            text_index[text_digest].append(index)
            simhash = int(str(current.fingerprints["simhash64"]), 16)
            for band in _bands(simhash):
                band_index[band].add(index)

    return tuple(result)


def preprocess_messages(
    messages: Iterable[SourceMessage],
    config: PreprocessingConfig | None = None,
) -> PreprocessingResult:
    resolved_config = config or PreprocessingConfig()
    source_messages = tuple(messages)
    normalized_input = sorted(
        (_message_payload(message) for message in source_messages),
        key=lambda item: (
            item["published_at"],
            item["source"].casefold(),
            item["external_id"],
            _sha256_text(item["text"]),
        ),
    )
    input_digest = _stable_digest(normalized_input)
    preprocessed = sorted(
        (_preprocess_one(message, resolved_config) for message in source_messages),
        key=_sort_key,
    )
    classified = _classify_duplicates(preprocessed, resolved_config)
    stats = PreprocessingStats(
        received=len(classified),
        accepted=sum(
            message.decision is MessageDecision.ACCEPTED for message in classified
        ),
        rejected=sum(
            message.decision is MessageDecision.REJECTED for message in classified
        ),
        included_for_analysis=sum(
            message.decision is MessageDecision.ACCEPTED
            and message.duplicate.included_for_analysis
            for message in classified
        ),
        technical_duplicates=sum(
            message.duplicate.kind is DuplicateKind.TECHNICAL_DUPLICATE
            for message in classified
        ),
        cross_source_reposts=sum(
            message.duplicate.kind is DuplicateKind.CROSS_SOURCE_REPOST
            for message in classified
        ),
        repeated_appeals=sum(
            message.duplicate.kind is DuplicateKind.REPEATED_APPEAL
            for message in classified
        ),
    )
    output_payload = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "config_digest": resolved_config.digest,
        "input_digest": input_digest,
        "stats": stats.to_dict(),
        "messages": [message.to_dict() for message in classified],
    }
    return PreprocessingResult(
        schema_version=SCHEMA_VERSION,
        algorithm_version=ALGORITHM_VERSION,
        config_digest=resolved_config.digest,
        input_digest=input_digest,
        output_digest=_stable_digest(output_payload),
        messages=classified,
        stats=stats,
    )


__all__ = [
    "ALGORITHM_VERSION",
    "SCHEMA_VERSION",
    "canonicalize_url",
    "detect_language",
    "preprocess_messages",
    "source_message_from_dict",
    "source_message_to_dict",
]
