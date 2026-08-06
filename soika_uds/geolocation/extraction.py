"""Primary and fallback address mention extraction."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from .model_manager import LazyModelManager
from .models import AddressMention, MentionSource
from .normalization import AddressNormalizer, is_missing


def _hex_digest(value: object, length: int, field_name: str) -> str:
    if not isinstance(value, str) or len(value) != length:
        raise ValueError(f"{field_name} must contain {length} hexadecimal characters")
    try:
        int(value, 16)
    except ValueError as error:
        raise ValueError(
            f"{field_name} must contain {length} hexadecimal characters"
        ) from error
    return value.lower()


class MentionExtractor(Protocol):
    @property
    def identity(self) -> Mapping[str, Any]: ...

    def extract(self, text: str) -> AddressMention | None: ...


class RuleBasedMentionExtractor:
    """Deterministic fallback for explicit address-like phrases."""

    _pattern = re.compile(
        r"(?:\b(?:ул(?:ица)?|пр(?:оспект)?|пер(?:еулок)?|наб(?:ережная)?|"
        r"бул(?:ьвар)?|ш(?:оссе)?|проезд|пл(?:ощадь)?|район|микрорайон)\.?\s+"
        r"[А-Яа-яЁёA-Za-z0-9\- ]{2,80}|\b(?:школа|поликлиника|парк|мост|"
        r"вокзал|станция метро)\s+[А-Яа-яЁёA-Za-z0-9\-\" ]{1,80})",
        re.I,
    )

    def __init__(self, normalizer: AddressNormalizer | None = None) -> None:
        self._normalizer = normalizer or AddressNormalizer()

    @property
    def identity(self) -> Mapping[str, Any]:
        return {"type": "rules", "version": "1"}

    def extract(self, text: str) -> AddressMention | None:
        if is_missing(text):
            return None
        match = self._pattern.search(text)
        if not match:
            return None
        return self._normalizer.normalize(
            match.group(0),
            confidence=0.58,
            source=MentionSource.RULES,
            span_start=match.start(),
            span_end=match.end(),
        )


class LocalFlairAddressExtractor:
    """Lazy local-only Flair NER adapter with mandatory artifact verification."""

    def __init__(
        self,
        model_path: Path,
        *,
        model_revision: str,
        weights_sha256: str,
        manager: LazyModelManager,
        artifact_verifier: Callable[[Path, str], None],
        normalizer: AddressNormalizer | None = None,
        loader: Callable[[str], Any] | None = None,
        min_score: float = 0.7,
    ) -> None:
        path = Path(model_path)
        if not path.is_absolute():
            raise ValueError("geolocation model path must be absolute")
        if not 0.0 <= min_score <= 1.0:
            raise ValueError("min_score must be in [0, 1]")
        self._path = path
        self._revision = _hex_digest(
            model_revision,
            40,
            "model_revision",
        )
        self._weights_sha256 = _hex_digest(
            weights_sha256,
            64,
            "weights_sha256",
        )
        self._manager = manager
        self._artifact_verifier = artifact_verifier
        self._normalizer = normalizer or AddressNormalizer.with_pymorphy3()
        self._loader = loader
        self._min_score = float(min_score)

    @property
    def identity(self) -> Mapping[str, Any]:
        return {
            "type": "flair_local",
            "path": str(self._path),
            "revision": self._revision,
            "weights_sha256": self._weights_sha256,
        }

    def _load(self) -> Any:
        self._artifact_verifier(self._path, self._weights_sha256)
        if self._loader is not None:
            return self._loader(str(self._path))
        from flair.models import SequenceTagger

        return SequenceTagger.load(str(self._path))

    def extract(self, text: str) -> AddressMention | None:
        if is_missing(text):
            return None
        from flair.data import Sentence

        model = self._manager.get(
            f"flair:{self._path}:{self._revision}",
            self._load,
        )
        sentence = Sentence(text)
        model.predict(sentence)
        labels = sentence.get_labels("ner")
        if not labels:
            return None
        best = max(labels, key=lambda item: float(item.score))
        score = float(best.score)
        if score < self._min_score:
            return None
        value = getattr(best, "data_point", None)
        mention = getattr(value, "text", None) or str(best.value)
        return self._normalizer.normalize(
            mention,
            confidence=score,
            source=MentionSource.PRIMARY_NER,
        )


class NatashaAddressExtractor:
    """Lazy Natasha LOC fallback without module-level model initialization."""

    def __init__(
        self,
        manager: LazyModelManager,
        *,
        excluded_names: Sequence[str] = (),
        normalizer: AddressNormalizer | None = None,
        component_loader: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        self._manager = manager
        self._excluded = frozenset(value.casefold() for value in excluded_names)
        self._normalizer = normalizer or AddressNormalizer.with_pymorphy3()
        self._component_loader = component_loader

    @property
    def identity(self) -> Mapping[str, Any]:
        return {"type": "natasha", "version": "1.6.0"}

    def _load(self) -> Mapping[str, Any]:
        if self._component_loader is not None:
            return self._component_loader()
        from natasha import (
            Doc,
            MorphVocab,
            NewsEmbedding,
            NewsMorphTagger,
            NewsNERTagger,
            NewsSyntaxParser,
            Segmenter,
        )

        embedding = NewsEmbedding()
        return {
            "Doc": Doc,
            "segmenter": Segmenter(),
            "morph_vocab": MorphVocab(),
            "morph_tagger": NewsMorphTagger(embedding),
            "syntax_parser": NewsSyntaxParser(embedding),
            "ner_tagger": NewsNERTagger(embedding),
        }

    def extract(self, text: str) -> AddressMention | None:
        if is_missing(text):
            return None
        components = self._manager.get("natasha:address", self._load)
        doc = components["Doc"](text)
        doc.segment(components["segmenter"])
        doc.tag_morph(components["morph_tagger"])
        doc.parse_syntax(components["syntax_parser"])
        doc.tag_ner(components["ner_tagger"])
        candidates: list[tuple[str, int | None, int | None]] = []
        for span in doc.spans:
            if getattr(span, "type", None) != "LOC":
                continue
            span.normalize(components["morph_vocab"])
            normalized = str(getattr(span, "normal", "") or "").casefold()
            if normalized and normalized in self._excluded:
                continue
            candidates.append(
                (
                    str(span.text),
                    getattr(span, "start", None),
                    getattr(span, "stop", None),
                )
            )
        if not candidates:
            return None
        text_value, start, end = candidates[0]
        return self._normalizer.normalize(
            text_value,
            confidence=0.62,
            source=MentionSource.NATASHA,
            span_start=start,
            span_end=end,
        )


class CompositeMentionExtractor:
    """Use primary NER, then Natasha, then deterministic rules."""

    def __init__(self, extractors: Sequence[MentionExtractor]) -> None:
        self._extractors = tuple(extractors)
        if not self._extractors:
            raise ValueError("at least one extractor is required")

    @property
    def identity(self) -> Mapping[str, Any]:
        return {
            "type": "composite",
            "extractors": [
                dict(extractor.identity) for extractor in self._extractors
            ],
        }

    def extract(self, text: str) -> AddressMention | None:
        for extractor in self._extractors:
            mention = extractor.extract(text)
            if mention is not None:
                return mention
        return None
