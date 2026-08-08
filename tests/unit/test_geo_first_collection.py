from __future__ import annotations

from datetime import UTC, datetime

from soika_uds.contracts import SourceMessage, TerritoryContext
from soika_uds.discovery import (
    CandidateCollectionResult,
    CollectorRouter,
    DiscoveryCollectionStageHandler,
    GeoScope,
    SourceCandidate,
    SourceKind,
    SourceOutcome,
    SourceReasonCode,
    SourceState,
)
from soika_uds.integration import AnalysisRequestV1
from soika_uds.orchestration import PipelineStage, StageContext


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
    candidate_id="web:media",
    kind=SourceKind.LOCAL_MEDIA,
    url="https://media.example.ru/news/277",
    domain="media.example.ru",
    title="Новости Ижевска",
    discovered_by="fixture-yandex",
    query='"Пушкинская 277" Ижевск',
    geo_evidence=("city_text_match",),
)


class FixedMediaCollector:
    source_kind = SourceKind.LOCAL_MEDIA

    def collect(self, candidate, scope):
        assert scope.city == "Ижевск"
        message = SourceMessage(
            source="local-media",
            external_id="article-277",
            text="На Пушкинской у дома 277 обсуждают ремонт тротуара",
            published_at=datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
            url=candidate.url,
            metadata={"kind": "news_article"},
        )
        return CandidateCollectionResult(
            messages=(message,),
            outcome=SourceOutcome(
                source_id=candidate.candidate_id,
                kind=candidate.kind,
                state=SourceState.COLLECTED,
                reason_code=SourceReasonCode.NONE,
                reason="source collected successfully",
                attempted_urls=(candidate.url,),
                messages_collected=1,
                relevant_messages=1,
            ),
        )


def _context() -> StageContext:
    territory = TerritoryContext(
        analysis_id="analysis-collection",
        city="Ижевск",
        address="Ижевск Пушкинская 277",
    )
    request = AnalysisRequestV1(
        analysis_id="analysis-collection",
        requested_at=datetime(2026, 8, 8, tzinfo=UTC),
        territory=territory,
    )
    return StageContext(
        request=request,
        stage=PipelineStage.COLLECTION,
        attempt=1,
        worker_id="worker-test",
        previous_outputs={
            PipelineStage.PREPARING.value: {
                "territory_context": SCOPE.to_dict(),
                "discovery_plan": {
                    "candidates": [CANDIDATE.to_dict()],
                },
            }
        },
    )


def test_missing_collector_is_reported_with_reason() -> None:
    handler = DiscoveryCollectionStageHandler(CollectorRouter())

    result = handler.run(_context())

    assert result.output["messages"] == []
    assert result.output["coverage"]["sources_unavailable"] == 1
    status = result.output["source_coverage"][0]
    assert status["state"] == "configuration_missing"
    assert status["reason_code"] == "SOURCE_CONFIGURATION_MISSING"
    assert status["reason"]


def test_configured_collector_emits_real_source_message_shape() -> None:
    router = CollectorRouter({SourceKind.LOCAL_MEDIA: FixedMediaCollector()})
    handler = DiscoveryCollectionStageHandler(router)

    result = handler.run(_context())

    assert result.output["coverage"] == {
        "sources_discovered": 1,
        "sources_collected": 1,
        "sources_unavailable": 0,
        "sources_no_relevant_results": 0,
        "messages_collected": 1,
        "messages_relevant": 1,
    }
    assert result.output["messages"][0]["external_id"] == "article-277"
    assert result.output["messages"][0]["url"] == "https://media.example.ru/news/277"
    assert result.output["source_coverage"][0]["state"] == "collected"
