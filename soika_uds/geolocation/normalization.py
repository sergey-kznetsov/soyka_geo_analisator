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
_HOUSE = re.compile(
    r"(?<!\w)(?:д(?:ом)?\.?\s*)?(\d+[а-яa-z]?)"
    r"(?:\s*(?:к|корп(?:ус)?\.?)\s*(\d+[а-яa-z]?))?",
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
        house_match = _HOUSE.search(original)
        house_number = None
        if house_match:
            house_number = house_match.group(1)
            if house_match.group(2):
                house_number += f"к{house_match.group(2)}"
        district = original if _DISTRICT.search(original) else None
        poi = original if _POI.search(original) else None
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
        elif house_number:
            kind = LocationKind.HOUSE
            street = _HOUSE.sub("", original).strip(" ,")
            secondary_street = None
        elif _STREET_PREFIX.search(original):
            kind = LocationKind.STREET
            street = _STREET_PREFIX.sub("", original).strip(" ,")
            secondary_street = None
        else:
            kind = LocationKind.UNKNOWN
            street = original
            secondary_street = None
        normalized_parts = [
            part
            for part in (
                self._lemma(street) if street else None,
                house_number,
                self._lemma(secondary_street) if secondary_street else None,
                self._lemma(poi) if poi else None,
                self._lemma(district) if district else None,
            )
            if part
        ]
        normalized = ", ".join(normalized_parts) or self._lemma(original)
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
