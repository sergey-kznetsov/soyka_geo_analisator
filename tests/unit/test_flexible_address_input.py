from __future__ import annotations

import pytest

from soika_uds.geolocation import (
    CandidateSource,
    CompositeMentionExtractor,
    GeocodingCandidate,
    GeolocationConfig,
    GeolocationEngine,
    GeoPoint,
    LocationKind,
    MentionSource,
    RuleBasedMentionExtractor,
)
from soika_uds.geolocation.models import AddressMention


@pytest.mark.parametrize(
    "text",
    (
        "Ижевск Пушкинская 277",
        "Ижевск, Пушкинская, 277",
        "Пушкинская 277",
        "пушкинская 277",
        "Ижевск Пушкинская, д. 277",
        "Ижевск, Пушкинская, 277 к 2",
    ),
)
def test_rule_extractor_accepts_common_free_form_house_variants(text: str) -> None:
    mention = RuleBasedMentionExtractor().extract(text)

    assert mention is not None
    assert mention.kind is LocationKind.HOUSE
    assert mention.house_number is not None
    assert mention.house_number.startswith("277")
    assert mention.source is MentionSource.RULES


@pytest.mark.parametrize(
    "text",
    (
        "В очереди было 277 человек",
        "сломался лифт 2",
        "подъезд 2",
        "квартира 277",
        "сегодня придут 10 человек",
    ),
)
def test_free_form_fallback_rejects_short_non_address_phrases(text: str) -> None:
    assert RuleBasedMentionExtractor().extract(text) is None


class UnknownExtractor:
    identity = {"type": "unknown-fixture"}

    def extract(self, text: str) -> AddressMention:
        return AddressMention(
            text="Ижевск Пушкинская",
            normalized="ижевск пушкинская",
            kind=LocationKind.UNKNOWN,
            confidence=0.9,
            source=MentionSource.NATASHA,
            street="Ижевск Пушкинская",
        )


def test_composite_keeps_complete_house_components_over_less_specific_ner() -> None:
    extractor = CompositeMentionExtractor(
        (UnknownExtractor(), RuleBasedMentionExtractor())
    )

    mention = extractor.extract("Ижевск Пушкинская 277")

    assert mention is not None
    assert mention.kind is LocationKind.HOUSE
    assert mention.house_number == "277"


class CapturingProvider:
    identity = {"type": "capture-fixture"}

    def __init__(self, candidates: tuple[GeocodingCandidate, ...]) -> None:
        self.candidates = candidates
        self.mentions: list[AddressMention] = []

    def search(self, mention, *, city, country_codes, language, limit):
        self.mentions.append(mention)
        return self.candidates


def _candidate(
    candidate_id: str,
    *,
    kind: LocationKind,
    confidence: float,
) -> GeocodingCandidate:
    return GeocodingCandidate(
        candidate_id=candidate_id,
        label="Пушкинская улица, 277, Ижевск",
        kind=kind,
        point=GeoPoint(53.2072056, 56.8665403),
        confidence=confidence,
        source=CandidateSource.FIXTURE,
        address={
            "city": "Ижевск",
            "road": "Пушкинская улица",
            **({"house_number": "277"} if kind is LocationKind.HOUSE else {}),
        },
    )


def _engine(provider: CapturingProvider) -> GeolocationEngine:
    return GeolocationEngine(
        RuleBasedMentionExtractor(),
        provider,
        config=GeolocationConfig(min_confidence=0.25),
    )


def test_engine_uses_city_as_context_not_as_part_of_street_name() -> None:
    provider = CapturingProvider((_candidate("house", kind=LocationKind.HOUSE, confidence=0.8),))

    result = _engine(provider).geolocate(
        (
            {
                "message_key": "a",
                "model_text": "Ижевск Пушкинская 277",
                "included_for_analysis": True,
            },
        ),
        city="Ижевск",
    )

    assert provider.mentions[0].street == "Пушкинская"
    assert provider.mentions[0].house_number == "277"
    assert result.results[0].included_for_analysis is True
    assert result.results[0].selected is not None
    assert result.results[0].selected.kind is LocationKind.HOUSE


def test_house_request_prefers_exact_house_candidate_over_street_candidate() -> None:
    provider = CapturingProvider(
        (
            _candidate("street", kind=LocationKind.STREET, confidence=0.95),
            _candidate("house", kind=LocationKind.HOUSE, confidence=0.75),
        )
    )

    result = _engine(provider).geolocate(
        (
            {
                "message_key": "a",
                "model_text": "Ижевск Пушкинская 277",
                "included_for_analysis": True,
            },
        ),
        city="Ижевск",
    )

    assert result.results[0].selected_candidate_id == "house"
    assert result.results[0].included_for_analysis is True


def test_house_request_fails_closed_when_only_street_precision_is_available() -> None:
    provider = CapturingProvider(
        (_candidate("street", kind=LocationKind.STREET, confidence=0.95),)
    )

    result = _engine(provider).geolocate(
        (
            {
                "message_key": "a",
                "model_text": "Ижевск Пушкинская 277",
                "included_for_analysis": True,
            },
        ),
        city="Ижевск",
    )

    item = result.results[0]
    assert item.selected_candidate_id == "street"
    assert item.included_for_analysis is False
    assert item.reasons == ("house_candidate_not_resolved",)
    assert result.stats.low_confidence == 1
