"""Address normalization and missing-value handling."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Callable
from typing import Any

from .models import AddressMention, LocationKind, MentionSource

_BRACKETS = re.compile(r"\[[^\]]*\]")
_SPACE = re.compile(r"\s+")
_HOUSE_NUMBER = r"(\d+[а-яa-z]?)"
_CORPUS_NUMBER = r"(?:\s*(?:к|корп(?:ус)?\.?)\s*(\d+[а-яa-z]?))?"
_EXPLICIT_HOUSE = re.compile(
    rf"(?<!\w)(?:д(?:ом)?\.?)\s*{_HOUSE_NUMBER}{_CORPUS_NUMBER}",
    re.I,
)
_TRAILING_HOUSE = re.compile(
    rf"(?:,\s*|\s+){_HOUSE_NUMBER}{_CORPUS_NUMBER}\s*$",
    re.I,
)
_INTERSECTION = re.compile(r"\s+(?:и|/|\\|пересечени[ея]|угол)\s+", re.I)
_STREET_PREFIX = re.compile(
    r"\b(?:ул(?:ица)?|пр(?:оспект)?|пер(?:еулок)?|наб(?:ережная)?|бул(?:ьвар)?|"
    r"ш(?:оссе)?|проезд|пл(?:ощадь)?|аллея|дорога)\.?\s+",
    re.I,
)
_DISTRICT = re.compile(r"\b(?:район|микрорайон|округ|квартал)\b", re.I)
_POI = re.compile(
    r"\b(?:школа|детский сад|поликлиника|больница|магазин|станция|остановка|"
    r"парк|сквер|мост|вокзал|метро|театр|музей|рынок|торговый центр)\b",
    re.I,
)


def is_missing(value: object) -> bool:
    """Return True for None, NaN-like scalars and blank strings."""

    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, float):
        return math.isnan(value)
    try:
        result = value != value
    except Exception:
        return False
    return bool(result) if isinstance(result, bool) else False


def clean_text(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("address text must be a string")
    normalized = unicodedata.normalize("NFKC", value)
    normalized = _BRACKETS.sub(" ", normalized)
    normalized = normalized.replace("ё", "е").replace("Ё", "Е")
    return _SPACE.sub(" ", normalized).strip(" ,.;:")


def _house_value(match: re.Match[str] | None) -> str | None:
    if match is None:
        return None
    value = match.group(1)
    if match.group(2):
        value += f"к{match.group(2)}"
    return value


def _without_match(value: str, match: re.Match[str]) -> str:
    return f"{value[: match.start()]} {value[match.end() :]}".strip(" ,")


class AddressNormalizer:
    """Normalize extracted mentions without global morphology state."""

    def __init__(self, morphology_factory: Callable[[], Any] | None = None) -> None:
        self._morphology_factory = morphology_factory
        self._morphology: Any | None = None

    @property
    def morphology(self) -> Any | None:
        if self._morphology_factory is None:
            return None
        if self._morphology is None:
            self._morphology = self._morphology_factory()
        return self._morphology

    @classmethod
    def with_pymorphy3(cls) -> AddressNormalizer:
        def factory() -> Any:
            import pymorphy3

            return pymorphy3.MorphAnalyzer()

        return cls(factory)

    def _lemma(self, value: str) -> str:
        morphology = self.morphology
        if morphology is None:
            return value.lower()
        words: list[str] = []
        for token in value.split():
            parsed = morphology.parse(token)
            words.append(parsed[0].normal_form if parsed else token.lower())
        return " ".join(words)

    def normalize(
        self,
        text: str,
        *,
        confidence: float,
        source: MentionSource,
        span_start: int | None = None,
        span_end: int | None = None,
    ) -> AddressMention:
        original = clean_text(text)
        if not original:
            raise ValueError("address mention is empty after normalization")
        intersection = _INTERSECTION.split(original, maxsplit=1)
        district = original if _DISTRICT.search(original) else None
        poi = original if _POI.search(original) else None
        house_match: re.Match[str] | None = None
        if len(intersection) != 2 and district is None and poi is None:
            house_match = _EXPLICIT_HOUSE.search(original)
            if house_match is None:
                house_match = _TRAILING_HOUSE.search(original)
        house_number = _house_value(house_match)

        if len(intersection) == 2:
            kind = LocationKind.INTERSECTION
            street = _STREET_PREFIX.sub("", intersection[0]).strip(" ,")
            secondary_street = _STREET_PREFIX.sub("", intersection[1]).strip(" ,")
        elif district:
            kind = LocationKind.DISTRICT
            street = None
            secondary_street = None
        elif poi:
            kind = (
                LocationKind.LANDMARK
                if re.search(r"\b(?:мост|парк|сквер)\b", original, re.I)
                else LocationKind.POI
            )
            street = None
            secondary_street = None
        elif house_match is not None:
            kind = LocationKind.HOUSE
            street_text = _without_match(original, house_match)
            street = _STREET_PREFIX.sub("", street_text).strip(" ,")
            secondary_street = None
        elif _STREET_PREFIX.search(original):
            kind = LocationKind.STREET
            street = _STREET_PREFIX.sub("", original).strip(" ,")
            secondary_street = None
        else:
            kind = LocationKind.UNKNOWN
            street = original
            secondary_street = None

        if kind is LocationKind.INTERSECTION:
            normalized_parts = [
                self._lemma(street),
                self._lemma(secondary_street),
            ]
        elif kind is LocationKind.HOUSE:
            normalized_parts = [self._lemma(street), house_number]
        elif kind is LocationKind.DISTRICT:
            normalized_parts = [self._lemma(district)]
        elif kind in {LocationKind.POI, LocationKind.LANDMARK}:
            normalized_parts = [self._lemma(poi)]
        else:
            normalized_parts = [self._lemma(street)]
        normalized = ", ".join(part for part in normalized_parts if part)
        return AddressMention(
            text=original,
            normalized=normalized,
            kind=kind,
            confidence=confidence,
            source=source,
            street=street,
            house_number=house_number,
            secondary_street=secondary_street,
            poi=poi,
            district=district,
            span_start=span_start,
            span_end=span_end,
        )
