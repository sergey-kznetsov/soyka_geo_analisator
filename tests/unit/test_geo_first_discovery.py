from __future__ import annotations

import base64
from datetime import UTC, datetime

import pytest

from soika_uds.contracts import TerritoryContext
from soika_uds.discovery import (
    ACTIVE_SOURCE_KINDS,
    DiscoveryEngine,
    GeoDiscoveryPreparingHandler,
    GeoQueryBuilder,
    SearchHit,
    SourceClassifier,
    SourceKind,
    SourceOutcome,
    SourceReasonCode,
    SourceState,
    TerritoryResolutionError,
    TerritoryResolver,
    YandexSearchProvider,
)
from soika_uds.geolocation import (
    AddressMention,
    CandidateSource,
    GeocodingCandidate,
    GeolocationBatchResult,
    GeolocationStats,
    GeoPoint,
    LocationKind,
    MentionSource,
    MessageGeolocationResult,
)
from soika_uds.integration import AnalysisRequestV1
from soika_uds.orchestration import PermanentStageError, PipelineStage, StageContext


class FixedTerritoryEngine:
    def __init__(self, *, city: str = "Ижевск", included: bool = True) -> None:
        self.city = city
        self.included = included
        self.calls = []

    def geolocate(self, messages, *, city=None):
        self.calls.append((messages, city))
        mention = AddressMention(
            text="Ижевск Пушкинская 277",
            normalized="ижевск пушкинская 277",
            kind=LocationKind.HOUSE,
            confidence=0.9,
            source=MentionSource.RULES,
            street="Пушкинская улица",
            house_number="277",
        )
        candidate = GeocodingCandidate(
            candidate_id="house-277",
            label="277, Пушкинская улица, Ижевск, Удмуртия, Россия",
            kind=LocationKind.HOUSE,
            point=GeoPoint(longitude=53.2072056, latitude=56.8665403),
            confidence=0.9,
            source=CandidateSource.FIXTURE,
            osm_type="way",
            osm_id=337532196,
            address={
                "city": self.city,
                "state": "Удмуртия",
                "city_district": "Октябрьский район",
                "road": "Пушкинская улица",
                "house_number": "277",
            },
        )
        item = MessageGeolocationResult(
            message_key="territory:test",
            mention=mention,
            candidates=(candidate,),
            selected_candidate_id=candidate.candidate_id,
            confidence=0.81,
            included_for_analysis=self.included,
            reasons=() if self.included else ("geolocation_below_threshold",),
            metric_crs="EPSG:32639",
            provenance={"fixture": True},
        )
        stats = GeolocationStats(
            received=1,
            processed=1,
            resolved=1 if self.included else 0,
            low_confidence=0 if self.included else 1,
            unresolved=0,
            skipped=0,
        )
        return GeolocationBatchResult(
            results=(item,),
            stats=stats,
            input_digest="input",
            output_digest="output",
            config_digest="config",
        )


class RecordingSearchProvider:
    provider_id = "fixture-yandex"

    def __init__(self) -> None:
        self.queries = []

    def search(self, query: str, *, limit: int = 10):
        self.queries.append((query, limit))
        return (
            SearchHit(
                query=query,
                title="Новости Ижевска — городское СМИ",
                url="https://izhevsk-news.example/articles/1",
                snippet="Ижевск Пушкинская улица",
                rank=0,
                provider=self.provider_id,
            ),
            SearchHit(
                query=query,
                title="Ижевск | Telegram",
                url="https://t.me/izhevsk_local/42",
                snippet="Новости Ижевска",
                rank=1,
                provider=self.provider_id,
            ),
            SearchHit(
                query=query,
                title="RUTUBE",
                url="https://rutube.ru/video/example/",
                snippet="Ижевск",
                rank=2,
                provider=self.provider_id,
            ),
        )


def _territory(city: str = "Ижевск") -> TerritoryContext:
    return TerritoryContext(
        analysis_id="analysis-discovery",
        city=city,
        address="Ижевск Пушкинская 277",
    )


def test_territory_is_resolved_from_address_before_discovery() -> None:
    engine = FixedTerritoryEngine()
    scope = TerritoryResolver(engine).resolve(_territory(city="Москва"))

    assert engine.calls[0][0][0]["model_text"] == "Ижевск Пушкинская 277"
    assert engine.calls[0][1] is None
    assert scope.city == "Ижевск"
    assert scope.region == "Удмуртия"
    assert scope.district == "Октябрьский район"
    assert scope.street == "Пушкинская улица"
    assert scope.house_number == "277"
    assert scope.osm_id == 337532196
    assert scope.metadata["requested_city_hint"] == "Москва"
    assert scope.metadata["city_hint_matches_resolved_city"] is False


def test_unresolved_target_fails_closed_before_search() -> None:
    resolver = TerritoryResolver(FixedTerritoryEngine(included=False))

    with pytest.raises(TerritoryResolutionError) as error:
        resolver.resolve(_territory())

    assert error.value.code is SourceReasonCode.TERRITORY_UNRESOLVED


def test_query_builder_is_ru_geo_bound_and_has_no_rutube_vk_ok() -> None:
    scope = TerritoryResolver(FixedTerritoryEngine()).resolve(_territory())
    queries = GeoQueryBuilder().build(scope)
    texts = [item.text for item in queries]

    assert all("Ижевск" in text or "Удмуртия" in text for text in texts)
    assert any("site:t.me" in text for text in texts)
    assert any("site:pikabu.ru" in text for text in texts)
    assert any("site:dzen.ru" in text for text in texts)
    assert any("site:yandex.ru/maps" in text for text in texts)
    assert any("site:2gis.ru" in text for text in texts)
    assert not any("rutube" in text.casefold() for text in texts)
    assert not any("vk.com" in text.casefold() for text in texts)
    assert not any("ok.ru" in text.casefold() for text in texts)


def test_active_perimeter_excludes_legacy_and_video_platforms() -> None:
    assert SourceKind.TELEGRAM in ACTIVE_SOURCE_KINDS
    assert SourceKind.LOCAL_MEDIA in ACTIVE_SOURCE_KINDS
    assert SourceKind.LOCAL_FORUM in ACTIVE_SOURCE_KINDS
    assert SourceKind.YANDEX_MAPS in ACTIVE_SOURCE_KINDS
    assert SourceKind.TWO_GIS in ACTIVE_SOURCE_KINDS
    assert SourceKind.RUTUBE not in ACTIVE_SOURCE_KINDS
    assert SourceKind.VK not in ACTIVE_SOURCE_KINDS
    assert SourceKind.OK not in ACTIVE_SOURCE_KINDS
    assert SourceKind.MAX not in ACTIVE_SOURCE_KINDS


def test_classifier_marks_known_platforms_and_local_forums() -> None:
    classifier = SourceClassifier()

    assert classifier.classify(
        SearchHit("q", "Ижевск", "https://t.me/izh_news", provider="test")
    ) is SourceKind.TELEGRAM
    assert classifier.classify(
        SearchHit("q", "Отзывы", "https://2gis.ru/izhevsk/firm/1", provider="test")
    ) is SourceKind.TWO_GIS
    assert classifier.classify(
        SearchHit("q", "Форум жителей Ижевска", "https://forum.example.ru/", provider="test")
    ) is SourceKind.LOCAL_FORUM
    assert classifier.classify(
        SearchHit("q", "Видео", "https://rutube.ru/video/1/", provider="test")
    ) is SourceKind.RUTUBE


def test_discovery_keeps_local_sources_and_explicitly_excludes_rutube() -> None:
    scope = TerritoryResolver(FixedTerritoryEngine()).resolve(_territory())
    provider = RecordingSearchProvider()
    plan = DiscoveryEngine(provider, results_per_query=3).plan(scope)

    by_kind = {item.kind: item for item in plan.candidates}
    assert by_kind[SourceKind.LOCAL_MEDIA].active is True
    assert by_kind[SourceKind.TELEGRAM].active is True
    assert by_kind[SourceKind.RUTUBE].active is False
    excluded = [item for item in plan.outcomes if item.kind is SourceKind.RUTUBE]
    assert excluded[0].state is SourceState.BLOCKED
    assert excluded[0].reason_code is SourceReasonCode.SOURCE_OUT_OF_SCOPE


def test_source_outcome_always_explains_unavailability() -> None:
    outcome = SourceOutcome(
        source_id="forum.example",
        kind=SourceKind.LOCAL_FORUM,
        state=SourceState.UNAVAILABLE,
        reason_code=SourceReasonCode.HTTP_403,
        reason="Источник вернул HTTP 403 Forbidden",
        attempted_urls=("https://forum.example/",),
    )

    payload = outcome.to_dict()
    assert payload["state"] == "unavailable"
    assert payload["reason_code"] == "HTTP_403"
    assert payload["reason"] == "Источник вернул HTTP 403 Forbidden"


class FakeYandexTransport:
    def __init__(self) -> None:
        self.calls = []

    def post_json(self, url, *, headers, payload, timeout_seconds):
        self.calls.append((url, headers, payload, timeout_seconds))
        xml = b"""<?xml version='1.0' encoding='utf-8'?>
        <yandexsearch><response><results><grouping><group><doc>
        <url>https://example.ru/news/277</url>
        <title>Новости Ижевска</title>
        <passages><passage>Пушкинская 277, Ижевск</passage></passages>
        </doc></group></grouping></results></response></yandexsearch>"""
        return 200, {"rawData": base64.b64encode(xml).decode("ascii")}


def test_yandex_provider_uses_ru_search_api_v2_and_parses_xml() -> None:
    transport = FakeYandexTransport()
    provider = YandexSearchProvider(
        folder_id="folder-test",
        api_key="secret-test",
        transport=transport,
    )

    hits = provider.search('"Пушкинская 277" Ижевск', limit=5)

    _, headers, payload, _ = transport.calls[0]
    assert headers["Authorization"] == "Api-Key secret-test"
    assert payload["query"]["searchType"] == "SEARCH_TYPE_RU"
    assert payload["responseFormat"] == "FORMAT_XML"
    assert hits[0].url == "https://example.ru/news/277"
    assert hits[0].snippet == "Пушкинская 277, Ижевск"


def test_preparing_handler_resolves_geo_then_builds_discovery_plan() -> None:
    geo = FixedTerritoryEngine()
    provider = RecordingSearchProvider()
    handler = GeoDiscoveryPreparingHandler(
        resolver=TerritoryResolver(geo),
        discovery=DiscoveryEngine(provider, results_per_query=3),
    )
    request = AnalysisRequestV1(
        analysis_id="analysis-discovery",
        requested_at=datetime(2026, 8, 8, tzinfo=UTC),
        territory=_territory(),
    )
    context = StageContext(
        request=request,
        stage=PipelineStage.PREPARING,
        attempt=1,
        worker_id="test-worker",
        previous_outputs={},
    )

    result = handler.run(context)

    assert result.output["territory_context"]["city"] == "Ижевск"
    assert result.output["discovery_plan"]["provider"] == "fixture-yandex"
    assert result.output["discovery_plan"]["stats"]["active_candidates"] == 2
    assert provider.queries


def test_preparing_handler_rejects_unresolved_address() -> None:
    handler = GeoDiscoveryPreparingHandler(
        resolver=TerritoryResolver(FixedTerritoryEngine(included=False)),
        discovery=DiscoveryEngine(RecordingSearchProvider()),
    )
    request = AnalysisRequestV1(
        analysis_id="analysis-discovery",
        requested_at=datetime(2026, 8, 8, tzinfo=UTC),
        territory=_territory(),
    )
    context = StageContext(
        request=request,
        stage=PipelineStage.PREPARING,
        attempt=1,
        worker_id="test-worker",
        previous_outputs={},
    )

    with pytest.raises(PermanentStageError) as error:
        handler.run(context)

    assert error.value.code == "TERRITORY_UNRESOLVED"
