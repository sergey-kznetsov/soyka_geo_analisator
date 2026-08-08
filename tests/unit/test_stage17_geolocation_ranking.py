from __future__ import annotations

from soika_uds.geolocation.models import AddressMention, LocationKind, MentionSource
from soika_uds.geolocation.nominatim_ranking import semantic_confidence


def _mention() -> AddressMention:
    return AddressMention(
        text="ул. Тверская, д. 13",
        normalized="тверская, 13",
        kind=LocationKind.HOUSE,
        confidence=0.58,
        source=MentionSource.RULES,
        street="Тверская",
        house_number="13",
    )


def test_house_ranking_prefers_structured_exact_road_over_amenity_name() -> None:
    mention = _mention()
    exact_payload = {
        "importance": 0.4,
        "address": {
            "amenity": "Правительство Москвы",
            "house_number": "13",
            "road": "Тверская улица",
        },
    }
    fuzzy_payload = {
        "importance": 0.4,
        "address": {
            "house_number": "13",
            "road": "4-я Тверская-Ямская улица",
        },
    }

    exact_score, _ = semantic_confidence(
        exact_payload,
        mention,
        "Правительство Москвы, 13, Тверская улица, Москва",
        rank=2,
        limit=5,
    )
    fuzzy_score, _ = semantic_confidence(
        fuzzy_payload,
        mention,
        "13, 4-я Тверская-Ямская улица, Москва",
        rank=0,
        limit=5,
    )

    assert exact_score > fuzzy_score
