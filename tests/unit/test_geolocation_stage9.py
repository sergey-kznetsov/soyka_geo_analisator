from __future__ import annotations

from pathlib import Path

import pytest

from soika_uds.geolocation import (
    AddressNormalizer,
    CandidateSource,
    GeocodingCandidate,
    GeolocationConfig,
    GeolocationEngine,
    GeolocationValidationCase,
    GeoPoint,
    HttpRetryPolicy,
    LazyModelManager,
    LocationKind,
    MentionSource,
    NominatimClient,
    RateLimiter,
    RequestsJsonTransport,
    RuleBasedMentionExtractor,
    SQLiteResponseCache,
    evaluate_geolocation,
    is_missing,
    metric_crs_for,
)


class FakeExtractor:
    identity = {"type": "fixture"}

    def extract(self, text: str):
        if "нет адреса" in text:
            return None
        return AddressNormalizer().normalize(
            "ул. Ленина 10",
            confidence=0.9,
            source=MentionSource.RULES,
        )


class FakeProvider:
    identity = {"type": "fixture"}

    def search(self, mention, *, city, country_codes, language, limit):
        return (
            GeocodingCandidate(
                candidate_id="candidate-1",
                label="Ленина, 10",
                kind=LocationKind.HOUSE,
                point=GeoPoint(37.6176, 55.7558),
                confidence=0.9,
                source=CandidateSource.FIXTURE,
            ),
        )


def test_missing_handles_nan_and_blank_values() -> None:
    assert is_missing(None)
    assert is_missing(float("nan"))
    assert is_missing("   ")
    assert not is_missing(0)


def test_normalizer_supports_house_intersection_poi_and_district() -> None:
    normalizer = AddressNormalizer()
    house = normalizer.normalize(
        "ул. Ленина, д. 10 к 2",
        confidence=0.9,
        source=MentionSource.RULES,
    )
    intersection = normalizer.normalize(
        "ул. Ленина и пр. Мира",
        confidence=0.8,
        source=MentionSource.RULES,
    )
    poi = normalizer.normalize(
        "школа 42",
        confidence=0.8,
        source=MentionSource.RULES,
    )
    district = normalizer.normalize(
        "Приморский район",
        confidence=0.8,
        source=MentionSource.RULES,
    )
    assert house.kind is LocationKind.HOUSE
    assert house.house_number == "10к2"
    assert intersection.kind is LocationKind.INTERSECTION
    assert intersection.secondary_street == "Мира"
    assert poi.kind is LocationKind.POI
    assert district.kind is LocationKind.DISTRICT


def test_lazy_model_manager_loads_once() -> None:
    manager = LazyModelManager()
    calls = []
    first = manager.get("x", lambda: calls.append("load") or object())
    second = manager.get("x", lambda: calls.append("again") or object())
    assert first is second
    assert calls == ["load"]


def test_rule_extractor_is_deterministic() -> None:
    extractor = RuleBasedMentionExtractor()
    first = extractor.extract("Яма на ул. Ленина 10 около дома")
    second = extractor.extract("Яма на ул. Ленина 10 около дома")
    assert first == second
    assert first is not None
    assert first.kind is LocationKind.HOUSE


def test_persistent_cache_round_trip(tmp_path: Path) -> None:
    cache = SQLiteResponseCache(tmp_path / "cache.sqlite3", namespace="test")
    key = cache.key("search", {"q": "Ленина"})
    cache.set(key, {"ok": True}, ttl_seconds=60)
    assert cache.get(key) == {"ok": True}


def test_rate_limiter_waits_for_interval() -> None:
    times = iter([0.0, 0.0, 0.25])
    sleeps = []
    limiter = RateLimiter(1.0, clock=lambda: next(times), sleeper=sleeps.append)
    limiter.wait()
    limiter.wait()
    assert sleeps == [1.0]


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.responses.pop(0)


def test_transport_retries_retryable_status() -> None:
    session = FakeSession(
        [FakeResponse(503, {}), FakeResponse(200, {"ok": True})]
    )
    sleeps = []
    transport = RequestsJsonTransport(
        user_agent="SOIKA UDS test@example.test",
        policy=HttpRetryPolicy(attempts=2, initial_backoff_seconds=0.1),
        session=session,
        sleeper=sleeps.append,
    )
    assert transport.request_json("GET", "https://example.test") == {"ok": True}
    assert sleeps == [0.1]


def test_transport_rejects_http_endpoint() -> None:
    transport = RequestsJsonTransport(
        user_agent="SOIKA UDS test@example.test",
        session=FakeSession([]),
    )
    with pytest.raises(ValueError, match="HTTPS"):
        transport.request_json("GET", "http://example.test")


class FakeTransport:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def request_json(self, method, url, *, params=None, data=None):
        self.calls.append((method, url, params, data))
        return self.payload


def test_nominatim_client_maps_candidates_and_uses_cache(tmp_path: Path) -> None:
    payload = [
        {
            "lat": "55.7558",
            "lon": "37.6176",
            "display_name": "Ленина, 10",
            "importance": 0.8,
            "osm_type": "way",
            "osm_id": 123,
            "addresstype": "house",
            "address": {"road": "Ленина", "house_number": "10"},
        }
    ]
    transport = FakeTransport(payload)
    cache = SQLiteResponseCache(
        tmp_path / "nominatim.sqlite3",
        namespace="nominatim",
    )
    client = NominatimClient(transport, cache)
    mention = AddressNormalizer().normalize(
        "ул. Ленина 10",
        confidence=0.9,
        source=MentionSource.RULES,
    )
    first = client.search(
        mention,
        city="Москва",
        country_codes=("ru",),
        language="ru",
        limit=5,
    )
    second = client.search(
        mention,
        city="Москва",
        country_codes=("ru",),
        language="ru",
        limit=5,
    )
    assert first == second
    assert first[0].kind is LocationKind.HOUSE
    assert len(transport.calls) == 1


def test_engine_preserves_candidates_and_is_deterministic() -> None:
    engine = GeolocationEngine(
        FakeExtractor(),
        FakeProvider(),
        config=GeolocationConfig(
            min_confidence=0.7,
            default_city="Москва",
        ),
    )
    messages = (
        {
            "message_key": "b",
            "model_text": "ул. Ленина",
            "included_for_analysis": True,
        },
        {
            "message_key": "a",
            "model_text": "нет адреса",
            "included_for_analysis": True,
        },
        {
            "message_key": "c",
            "model_text": "ул. Ленина",
            "included_for_analysis": False,
        },
    )
    first = engine.geolocate(messages)
    second = engine.geolocate(tuple(reversed(messages)))
    assert first.to_dict() == second.to_dict()
    assert first.stats.received == 3
    assert first.stats.processed == 2
    assert first.stats.resolved == 1
    assert first.stats.unresolved == 1
    assert first.stats.skipped == 1
    assert first.results[1].metric_crs == "EPSG:32637"


def test_metric_crs_uses_northern_and_southern_utm() -> None:
    assert metric_crs_for(GeoPoint(37.6, 55.7)) == "EPSG:32637"
    assert metric_crs_for(GeoPoint(151.2, -33.8)) == "EPSG:32756"


def test_evaluation_uses_metric_distance() -> None:
    candidate = GeocodingCandidate(
        candidate_id="candidate-1",
        label="fixture",
        kind=LocationKind.HOUSE,
        point=GeoPoint(37.6176, 55.7558),
        confidence=0.9,
        source=CandidateSource.FIXTURE,
    )
    from soika_uds.geolocation import MessageGeolocationResult

    prediction = MessageGeolocationResult(
        message_key="a",
        mention=None,
        candidates=(candidate,),
        selected_candidate_id="candidate-1",
        confidence=0.9,
        included_for_analysis=True,
        reasons=(),
        metric_crs="EPSG:32637",
        provenance={},
    )
    report = evaluate_geolocation(
        (
            GeolocationValidationCase(
                message_key="a",
                city="Москва",
                expected_point=GeoPoint(37.6177, 55.7559),
                expected_kind=LocationKind.HOUSE,
                tolerance_m=30.0,
            ),
        ),
        (prediction,),
    )
    assert report["resolution_rate"] == 1.0
    assert report["within_tolerance_rate"] == 1.0
    assert report["kind_accuracy"] == 1.0
    assert len(report["report_digest"]) == 64
