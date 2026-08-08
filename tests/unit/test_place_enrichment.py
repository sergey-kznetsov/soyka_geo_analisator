from __future__ import annotations

# ruff: noqa: I001

from soika_uds.discovery import (
    DiscoveryEngine,
    GeoQueryBuilder,
    GeoScope,
    MapReviewUnavailableCollector,
    OsmPoiEnricher,
    PlaceEnrichmentResult,
    PlaceRecord,
    SourceCandidate,
    SourceKind,
    SourceOutcome,
    SourceReasonCode,
    SourceState,
    TwoGisPlacesEnricher,
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


class TwoGisFixtureTransport:
    def __init__(self):
        self.params = None

    def fetch(self, params, *, timeout_seconds):
        self.params = dict(params)
        assert timeout_seconds == 15.0
        return 200, {
            "meta": {"code": 200},
            "result": {
                "items": [
                    {
                        "id": "70000001081809114",
                        "name": "Sushi Art",
                        "point": {"lon": 53.2073, "lat": 56.8666},
                        "full_address_name": "Ижевск, Пушкинская улица, 277",
                        "rubrics": [{"name": "Суши-бары"}],
                        "reviews": {
                            "general_rating": "4.7",
                            "review_count": "21",
                        },
                    }
                ]
            },
        }


class OverpassFixtureTransport:
    def __init__(self):
        self.query_text = ""
        self.user_agent = ""

    def query(self, query, *, user_agent, timeout_seconds):
        self.query_text = query
        self.user_agent = user_agent
        assert timeout_seconds == 15.0
        return 200, {
            "elements": [
                {
                    "type": "way",
                    "id": 337532196,
                    "center": {"lon": 53.2072056, "lat": 56.8665403},
                    "tags": {
                        "name": "Parus Plaza",
                        "shop": "mall",
                        "addr:street": "Пушкинская улица",
                        "addr:housenumber": "277",
                    },
                }
            ]
        }


class FixturePlaceEnricher:
    def enrich(self, scope):
        assert scope.city == "Ижевск"
        return PlaceEnrichmentResult(
            places=(
                PlaceRecord(
                    provider="openstreetmap",
                    external_id="way/337532196",
                    name="Parus Plaza",
                    longitude=53.2072056,
                    latitude=56.8665403,
                    address="Пушкинская улица 277",
                ),
            ),
            outcomes=(
                SourceOutcome(
                    source_id="places:fixture",
                    kind=SourceKind.OSM_ENTITY,
                    state=SourceState.PARTIAL,
                    reason_code=SourceReasonCode.NONE,
                    reason="fixture place enrichment",
                ),
            ),
        )


class EmptySearchProvider:
    provider_id = "fixture-search"

    def __init__(self):
        self.queries = []

    def search(self, query, *, limit=10):
        self.queries.append((query, limit))
        return ()


def test_2gis_missing_key_is_explicitly_unavailable() -> None:
    result = TwoGisPlacesEnricher(None).enrich(SCOPE)

    assert result.places == ()
    assert result.outcomes[0].state is SourceState.CONFIGURATION_MISSING
    assert result.outcomes[0].reason_code is SourceReasonCode.API_CREDENTIALS_MISSING


def test_2gis_collects_poi_and_review_statistics_not_review_texts() -> None:
    transport = TwoGisFixtureTransport()
    result = TwoGisPlacesEnricher("secret", transport=transport).enrich(SCOPE)

    assert len(result.places) == 1
    place = result.places[0]
    assert place.name == "Sushi Art"
    assert place.rating == 4.7
    assert place.review_count == 21
    assert place.address == "Ижевск, Пушкинская улица, 277"
    assert result.outcomes[0].state is SourceState.PARTIAL
    assert result.outcomes[0].details["review_texts_available"] is False
    assert transport.params["point"] == "53.2072056,56.8665403"
    assert transport.params["radius"] == "300"


def test_osm_overpass_is_bounded_and_emits_nearby_named_poi() -> None:
    transport = OverpassFixtureTransport()
    result = OsmPoiEnricher(transport=transport).enrich(SCOPE)

    assert len(result.places) == 1
    assert result.places[0].name == "Parus Plaza"
    assert result.places[0].source_url.endswith("/way/337532196")
    assert "around:300,56.8665403,53.2072056" in transport.query_text
    assert "timeout:10" in transport.query_text
    assert transport.user_agent.startswith("SOIKA-UDS/")
    assert result.outcomes[0].details["license"] == "ODbL"


def test_place_names_expand_yandex_discovery_after_geo_resolution() -> None:
    provider = EmptySearchProvider()
    engine = DiscoveryEngine(provider, place_enricher=FixturePlaceEnricher())

    plan = engine.plan(SCOPE)

    assert plan.scope.metadata["place_names"] == ["Parus Plaza"]
    queries = [item.text for item in plan.queries]
    assert '"Parus Plaza" Ижевск' in queries
    assert '"Parus Plaza" Ижевск отзывы' in queries
    assert any(item.source_id == "places:fixture" for item in plan.outcomes)
    assert provider.queries


def test_query_builder_remains_bounded_when_many_places_are_supplied() -> None:
    scope = GeoScope(
        raw_address=SCOPE.raw_address,
        city=SCOPE.city,
        region=SCOPE.region,
        district=SCOPE.district,
        street=SCOPE.street,
        house_number=SCOPE.house_number,
        longitude=SCOPE.longitude,
        latitude=SCOPE.latitude,
        precision=SCOPE.precision,
        confidence=SCOPE.confidence,
        candidate_id=SCOPE.candidate_id,
        label=SCOPE.label,
        metadata={"place_names": [f"Организация {index}" for index in range(100)]},
    )

    queries = GeoQueryBuilder(max_queries=24).build(scope)

    assert len(queries) <= 24


def test_map_review_text_unavailability_is_not_reported_as_success() -> None:
    candidate = SourceCandidate(
        candidate_id="map:2gis",
        kind=SourceKind.TWO_GIS,
        url="https://2gis.ru/izhevsk/firm/70000001081809114/tab/reviews",
        domain="2gis.ru",
        title="Sushi Art",
        discovered_by="fixture-search",
        query='"Пушкинская 277" Ижевск отзывы',
    )
    collector = MapReviewUnavailableCollector(SourceKind.TWO_GIS)

    result = collector.collect(candidate, SCOPE)

    assert result.messages == ()
    assert result.outcome.state is SourceState.UNAVAILABLE
    assert result.outcome.reason_code is SourceReasonCode.UNSUPPORTED_PAGE
    assert result.outcome.details["review_texts_collected"] is False
