from __future__ import annotations

from datetime import UTC, datetime

import pytest

from soika_uds.discovery import (
    CandidateCollectionError,
    GeoScope,
    SourceCandidate,
    SourceKind,
    SourceReasonCode,
    SourceState,
    TelegramCollector,
    TelegramRecord,
    UnavailableTelegramGateway,
    parse_telegram_target,
    telegram_search_terms,
)


SCOPE = GeoScope(
    raw_address="Ижевск Пушкинская 277",
    city="Ижевск",
    region="Удмуртия",
    district="Октябрьский район",
    street="Пушкинская улица",
    house_number="277",
    longitude=53.2072056,
    latitude=56.8665403,
    precision="house",
    confidence=0.81,
    candidate_id="house-277",
    label="277, Пушкинская улица, Ижевск",
)

CANDIDATE = SourceCandidate(
    candidate_id="web:telegram-post",
    kind=SourceKind.TELEGRAM,
    url="https://t.me/izhevsk_news/123",
    domain="t.me",
    title="Ижевск новости",
    discovered_by="yandex-search-api-v2-ru",
    query='site:t.me Ижевск "Пушкинская 277"',
    geo_evidence=("city_text_match",),
)


class RecordingGateway:
    def __init__(self, records):
        self.records = tuple(records)
        self.calls = []

    def collect(
        self,
        target,
        *,
        search_terms,
        history_limit,
        comments_per_post,
    ):
        self.calls.append(
            (target, search_terms, history_limit, comments_per_post)
        )
        return self.records


def test_public_telegram_url_parses_channel_and_message() -> None:
    target = parse_telegram_target("https://t.me/s/Izhevsk_News/123?single=1")

    assert target.username == "izhevsk_news"
    assert target.message_id == 123
    assert target.channel_url == "https://t.me/izhevsk_news"


def test_private_invite_link_is_not_collected() -> None:
    with pytest.raises(CandidateCollectionError) as error:
        parse_telegram_target("https://t.me/+privateInvite")

    assert error.value.code is SourceReasonCode.AUTH_REQUIRED
    assert error.value.state is SourceState.AUTH_REQUIRED


def test_search_terms_are_address_first_and_geo_bound() -> None:
    terms = telegram_search_terms(SCOPE)

    assert terms[0] == "Пушкинская улица 277"
    assert "Ижевск Пушкинская улица 277" in terms
    assert "Ижевск Пушкинская улица" in terms
    assert "Ижевск Пушкинская 277" in terms


def test_telegram_collector_emits_posts_comments_and_explicit_coverage() -> None:
    gateway = RecordingGateway(
        (
            TelegramRecord(
                channel_username="izhevsk_news",
                message_id=123,
                text="Ижевск. Пушкинская улица 277: ремонт тротуара",
                published_at=datetime(2026, 8, 8, 10, 0, tzinfo=UTC),
            ),
            TelegramRecord(
                channel_username="izhevsk_news",
                message_id=9001,
                text="На Пушкинской улице работы идут второй день",
                published_at=datetime(2026, 8, 8, 10, 5, tzinfo=UTC),
                is_comment=True,
                parent_message_id=123,
            ),
        )
    )
    collector = TelegramCollector(
        gateway,
        history_limit=50,
        comments_per_post=25,
    )

    result = collector.collect(CANDIDATE, SCOPE)

    assert len(result.messages) == 2
    assert result.messages[0].source == "telegram"
    assert result.messages[0].url == "https://t.me/izhevsk_news/123"
    assert result.messages[0].author_id is None
    assert result.messages[0].metadata["kind"] == "telegram_post"
    assert result.messages[1].metadata["kind"] == "telegram_comment"
    assert result.messages[1].url == "https://t.me/izhevsk_news/123?comment=9001"
    assert result.outcome.state is SourceState.COLLECTED
    assert result.outcome.reason_code is SourceReasonCode.NONE
    assert result.outcome.messages_collected == 2
    # Only the exact normalized street/house match is counted as a pre-filter hint.
    assert result.outcome.relevant_messages == 1
    assert result.outcome.details["final_geo_filter_required"] is True
    call = gateway.calls[0]
    assert call[0].username == "izhevsk_news"
    assert call[0].message_id == 123
    assert call[2:] == (50, 25)


def test_telegram_collector_distinguishes_accessible_empty_channel() -> None:
    collector = TelegramCollector(RecordingGateway(()))

    result = collector.collect(CANDIDATE, SCOPE)

    assert result.messages == ()
    assert result.outcome.state is SourceState.NO_RELEVANT_RESULTS
    assert result.outcome.reason_code is SourceReasonCode.NO_RESULTS
    assert "accessible" in result.outcome.reason.lower()


def test_missing_mtproto_credentials_are_reported_not_silenced() -> None:
    collector = TelegramCollector(
        UnavailableTelegramGateway("Telegram service-account secret is missing")
    )

    with pytest.raises(CandidateCollectionError) as error:
        collector.collect(CANDIDATE, SCOPE)

    assert error.value.code is SourceReasonCode.API_CREDENTIALS_MISSING
    assert error.value.state is SourceState.CONFIGURATION_MISSING
    assert "secret is missing" in str(error.value)
