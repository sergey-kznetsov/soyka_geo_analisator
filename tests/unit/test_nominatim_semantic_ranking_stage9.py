from __future__ import annotations

from soika_uds.geolocation import AddressNormalizer, MentionSource
from soika_uds.geolocation.nominatim_ranking import (
    semantic_confidence,
    semantic_kind,
    structured_query,
)


def _mention(text: str):
    return AddressNormalizer().normalize(
        text,
        confidence=0.8,
        source=MentionSource.RULES,
    )


def test_house_query_removes_ambiguous_russian_marker() -> None:
    mention = _mention("ул. Тверская, д. 13")
    assert structured_query(mention, "Москва") == "ул. Тверская 13, Москва"


def test_exact_house_number_outranks_street_fallback() -> None:
    mention = _mention("ул. Тверская, д. 13")
    exact = {
        "importance": 0.2,
        "address": {"road": "Тверская улица", "house_number": "13"},
    }
    street = {
        "importance": 0.8,
        "address": {"road": "Тверская улица"},
    }
    exact_score, exact_reasons = semantic_confidence(
        exact,
        mention,
        "Тверская улица, 13, Москва",
        1,
        5,
    )
    street_score, _ = semantic_confidence(
        street,
        mention,
        "Тверская улица, Москва",
        0,
        5,
    )
    assert exact_score > street_score
    assert "house_number_match" in exact_reasons
    assert semantic_kind(exact, mention, "Тверская улица, 13") == mention.kind


def test_semantic_number_selects_kazan_passenger_station() -> None:
    mention = _mention("вокзал Казань-1")
    river = {
        "importance": 0.6,
        "address": {"amenity": "Речной вокзал Казань", "house_number": "1"},
    }
    passenger = {
        "importance": 0.3,
        "address": {"building": "Казань-1-Пассажирская"},
    }
    river_score, _ = semantic_confidence(
        river,
        mention,
        "Речной вокзал Казань, 1",
        0,
        5,
    )
    passenger_score, passenger_reasons = semantic_confidence(
        passenger,
        mention,
        "Казань-1-Пассажирская",
        1,
        5,
    )
    assert passenger_score > river_score
    assert "semantic_number_match" in passenger_reasons
    assert semantic_kind(passenger, mention, "Казань-1-Пассажирская") == mention.kind


def test_semantic_name_prefers_alexandrovsky_park_over_garden() -> None:
    mention = _mention("парк Александровский")
    garden = {
        "importance": 0.8,
        "address": {"park": "Александровский сад"},
    }
    park = {
        "importance": 0.4,
        "address": {"neighbourhood": "Александровский парк"},
    }
    garden_score, _ = semantic_confidence(
        garden,
        mention,
        "Александровский сад",
        0,
        5,
    )
    park_score, _ = semantic_confidence(
        park,
        mention,
        "Александровский парк",
        1,
        5,
    )
    assert park_score > garden_score
    assert semantic_kind(park, mention, "Александровский парк") == mention.kind
