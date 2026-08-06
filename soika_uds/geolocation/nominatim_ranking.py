"""Deterministic semantic ranking for Nominatim candidates."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from difflib import SequenceMatcher
from typing import Any

from .models import AddressMention, LocationKind

_TOKEN = re.compile(r"[0-9a-zа-я]+", re.I)
_HOUSE_MARKER = re.compile(r",?\s*д(?:ом)?\.?\s*", re.I)


def _normalized(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("ё", "е").replace("Ё", "Е").casefold()
    return " ".join(_TOKEN.findall(text))


def _tokens(value: object) -> frozenset[str]:
    return frozenset(_normalized(value).split())


def _numbers(value: object) -> frozenset[str]:
    return frozenset(token for token in _tokens(value) if token.isdigit())


def structured_query(mention: AddressMention, city: str | None) -> str:
    """Build a free-form query without the ambiguous Russian house marker."""

    if mention.kind is LocationKind.HOUSE and mention.house_number:
        base = _HOUSE_MARKER.sub(" ", mention.text).strip(" ,")
    else:
        base = mention.text
    parts = [base]
    if city and city.casefold() not in base.casefold():
        parts.append(city)
    return ", ".join(parts)


def _semantic_name(payload: Mapping[str, Any], label: str) -> str:
    address = payload.get("address")
    if isinstance(address, Mapping):
        for key in (
            "building",
            "amenity",
            "railway",
            "tourism",
            "shop",
            "park",
            "highway",
            "historic",
            "man_made",
            "neighbourhood",
            "road",
        ):
            value = address.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return label


def _name_similarity(mention: AddressMention, semantic_name: str) -> float:
    expected = mention.poi or mention.district or mention.street or mention.text
    expected_norm = _normalized(expected)
    actual_norm = _normalized(semantic_name)
    if not expected_norm or not actual_norm:
        return 0.0
    expected_tokens = _tokens(expected_norm)
    actual_tokens = _tokens(actual_norm)
    overlap = len(expected_tokens & actual_tokens)
    token_f1 = (
        2.0 * overlap / (len(expected_tokens) + len(actual_tokens))
        if expected_tokens and actual_tokens
        else 0.0
    )
    sequence = SequenceMatcher(None, expected_norm, actual_norm).ratio()
    return max(token_f1, sequence)


def _house_match(mention: AddressMention, payload: Mapping[str, Any], label: str) -> bool:
    if not mention.house_number:
        return False
    address = payload.get("address")
    address_number = address.get("house_number") if isinstance(address, Mapping) else None
    expected = _normalized(mention.house_number).replace(" ", "")
    actual = _normalized(address_number).replace(" ", "")
    if actual and (actual == expected or actual.startswith(expected)):
        return True
    return expected in _numbers(label)


def semantic_kind(
    payload: Mapping[str, Any],
    mention: AddressMention,
    label: str,
) -> LocationKind:
    """Return the SOIKA semantic level, not merely the OSM feature type."""

    if mention.kind is LocationKind.HOUSE:
        return (
            LocationKind.HOUSE
            if _house_match(mention, payload, label)
            else LocationKind.STREET
        )
    if mention.kind in {LocationKind.POI, LocationKind.LANDMARK}:
        semantic_name = _semantic_name(payload, label)
        if _name_similarity(mention, semantic_name) >= 0.5:
            return mention.kind
    value = str(payload.get("addresstype") or payload.get("type") or "").casefold()
    category = str(payload.get("category") or "").casefold()
    if value in {"house", "building"}:
        return LocationKind.HOUSE
    if value in {"road", "street", "pedestrian"}:
        return LocationKind.STREET
    if value in {"suburb", "district", "borough", "neighbourhood", "quarter"}:
        return LocationKind.DISTRICT
    if category in {"amenity", "shop", "tourism", "railway"}:
        return LocationKind.POI
    if category in {"historic", "natural", "man_made", "bridge"}:
        return LocationKind.LANDMARK
    return mention.kind


def semantic_confidence(
    payload: Mapping[str, Any],
    mention: AddressMention,
    label: str,
    rank: int,
    limit: int,
) -> tuple[float, tuple[str, ...]]:
    """Combine service rank with address-component and semantic-name evidence."""

    raw_importance = payload.get("importance", 0.0)
    importance = (
        max(0.0, min(1.0, float(raw_importance)))
        if not isinstance(raw_importance, bool)
        and isinstance(raw_importance, int | float)
        else 0.0
    )
    order = 1.0 - rank / max(1, limit)
    semantic_name = _semantic_name(payload, label)
    name_score = _name_similarity(mention, semantic_name)
    reasons = ["nominatim_rank", "semantic_name_similarity"]
    if mention.kind is LocationKind.HOUSE:
        number_match = _house_match(mention, payload, label)
        score = 0.15 * importance + 0.10 * order + 0.35 * name_score
        if number_match:
            score += 0.40
            reasons.append("house_number_match")
        else:
            score = min(score, 0.44)
            reasons.append("house_number_missing")
    else:
        expected_numbers = _numbers(mention.text)
        actual_numbers = _numbers(semantic_name)
        number_score = 1.0 if expected_numbers == actual_numbers else 0.0
        score = 0.15 * importance + 0.10 * order + 0.60 * name_score
        if expected_numbers:
            score += 0.15 * number_score
            reasons.append(
                "semantic_number_match" if number_score else "semantic_number_mismatch"
            )
    return round(max(0.05, min(0.99, score)), 6), tuple(reasons)


__all__ = ["semantic_confidence", "semantic_kind", "structured_query"]
