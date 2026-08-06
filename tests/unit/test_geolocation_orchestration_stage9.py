from __future__ import annotations

from datetime import UTC, datetime

from soika_uds.contracts import TerritoryContext
from soika_uds.geolocation import (
    AddressNormalizer,
    CandidateSource,
    GeocodingCandidate,
    GeolocationConfig,
    GeolocationEngine,
    GeolocationStageHandler,
    GeoPoint,
    LocationKind,
    MentionSource,
)
from soika_uds.integration import AnalysisRequestV1
from soika_uds.orchestration import PipelineStage, StageContext


class FixedExtractor:
    identity = {"type": "fixture"}

    def extract(self, text: str):
        return AddressNormalizer().normalize(
            "ул. Ленина 10",
            confidence=0.9,
            source=MentionSource.RULES,
        )


class RecordingProvider:
    identity = {"type": "fixture"}

    def __init__(self) -> None:
        self.cities = []

    def search(self, mention, *, city, country_codes, language, limit):
        self.cities.append(city)
        return (
            GeocodingCandidate(
                candidate_id="candidate-1",
                label=f"Ленина 10, {city}",
                kind=LocationKind.HOUSE,
                point=GeoPoint(49.1221, 55.7887),
                confidence=0.9,
                source=CandidateSource.FIXTURE,
            ),
        )


def test_engine_city_override_is_bound_to_digest_and_provider() -> None:
    provider = RecordingProvider()
    engine = GeolocationEngine(
        FixedExtractor(),
        provider,
        config=GeolocationConfig(default_city="Москва"),
    )
    message = {
        "message_key": "a",
        "model_text": "На улице яма",
        "included_for_analysis": True,
    }
    kazan = engine.geolocate((message,), city="Казань")
    moscow = engine.geolocate((message,), city="Москва")

    assert provider.cities == ["Казань", "Москва"]
    assert kazan.config_digest != moscow.config_digest
    assert kazan.input_digest != moscow.input_digest
    assert kazan.results[0].provenance["effective_city"] == "Казань"


def test_stage_handler_passes_request_territory_city() -> None:
    provider = RecordingProvider()
    handler = GeolocationStageHandler(
        GeolocationEngine(FixedExtractor(), provider)
    )
    territory = TerritoryContext(
        analysis_id="analysis-stage9",
        city="Казань",
        address="Кремль",
    )
    request = AnalysisRequestV1(
        analysis_id="analysis-stage9",
        requested_at=datetime(2026, 8, 6, tzinfo=UTC),
        territory=territory,
    )
    context = StageContext(
        request=request,
        stage=PipelineStage.GEOLOCATION,
        attempt=1,
        worker_id="test-worker",
        previous_outputs={
            PipelineStage.PREPROCESSING.value: {
                "preprocessing": {
                    "messages": [
                        {
                            "message_key": "a",
                            "model_text": "На улице яма",
                        }
                    ]
                }
            },
            PipelineStage.NLP.value: {
                "classification": {
                    "results": [
                        {
                            "message_key": "a",
                            "included_for_analysis": True,
                        }
                    ]
                }
            },
        },
    )
    result = handler.run(context)

    assert provider.cities == ["Казань"]
    assert result.output["geolocation"]["results"][0]["provenance"][
        "effective_city"
    ] == "Казань"
