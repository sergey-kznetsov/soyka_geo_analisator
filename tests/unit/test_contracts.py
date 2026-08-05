from datetime import date, datetime, timezone

import pytest

from soika_uds.contracts import CoverageSummary, SourceMessage, TerritoryContext


def test_territory_accepts_geo_analyzer_address_and_coordinates():
    context = TerritoryContext(
        analysis_id="analysis-1",
        city="Ижевск",
        address="Ижевск, Пушкинская, 277",
        latitude=56.87,
        longitude=53.21,
        radius_meters=1500,
        period_from=date(2026, 1, 1),
        period_to=date(2026, 8, 5),
        sources=("source-a",),
    )

    assert context.analysis_id == "analysis-1"
    assert context.sources == ("source-a",)


def test_territory_rejects_partial_coordinates():
    with pytest.raises(ValueError, match="supplied together"):
        TerritoryContext(
            analysis_id="analysis-1",
            city="Ижевск",
            latitude=56.87,
        )


def test_source_message_is_source_independent():
    message = SourceMessage(
        source="portal",
        external_id="42",
        text="На улице яма",
        published_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )

    assert message.source == "portal"
    assert message.text == "На улице яма"


def test_coverage_invariants_are_enforced():
    with pytest.raises(ValueError, match="messages_relevant"):
        CoverageSummary(messages_collected=2, messages_relevant=3)
