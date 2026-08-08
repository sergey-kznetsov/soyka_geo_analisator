from __future__ import annotations

# ruff: noqa: I001

from soika_uds.discovery import (
    ACTIVE_SOURCE_KINDS,
    DiscoveryEngine,
    GeoScope,
    SearchHit,
    SourceKind,
    SourceReasonCode,
    SourceState,
)


SCOPE = GeoScope(
    raw_address="Ижевск Пушкинская 277",
    city="Ижевск",
    region="Удмуртская Республика",
    district="Октябрьский район",
    street="Пушкинская улица",
    house_number="277",
    longitude=53.2072056,
    latitude=56.8665403,
    precision="house",
    confidence=1.0,
)


class TelegramHitProvider:
    provider_id = "fixture-yandex"

    def search(self, query, *, limit=10):
        del limit
        if "site:t.me" not in query:
            return ()
        return (
            SearchHit(
                provider=self.provider_id,
                url="https://t.me/izhevsk_news/123",
                title="Новости Ижевска",
                snippet="Пушкинская улица, 277",
                query=query,
                rank=1,
            ),
        )


def test_telegram_is_not_in_production_active_source_set() -> None:
    assert SourceKind.TELEGRAM not in ACTIVE_SOURCE_KINDS


def test_discovered_telegram_candidate_reports_terms_restriction() -> None:
    plan = DiscoveryEngine(TelegramHitProvider()).plan(SCOPE)

    telegram_candidates = [
        candidate for candidate in plan.candidates if candidate.kind is SourceKind.TELEGRAM
    ]
    assert telegram_candidates
    assert all(candidate.active is False for candidate in telegram_candidates)

    outcomes = [
        outcome for outcome in plan.outcomes if outcome.kind is SourceKind.TELEGRAM
    ]
    assert outcomes
    assert all(outcome.state is SourceState.BLOCKED for outcome in outcomes)
    assert all(
        outcome.reason_code is SourceReasonCode.TERMS_RESTRICTED
        for outcome in outcomes
    )
    assert all(outcome.details["production_enabled"] is False for outcome in outcomes)
