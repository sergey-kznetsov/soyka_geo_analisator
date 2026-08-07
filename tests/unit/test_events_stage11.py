from __future__ import annotations

from dataclasses import dataclass

from soika_uds.events import (
    ClusterAssignment,
    CosineGraphClusterer,
    EmbeddingBatch,
    EventClusteringConfig,
    EventClusteringEngine,
    EventLevel,
    EventMessage,
    IdentityReductionBackend,
    ScopeStatus,
    UMAPReductionBackend,
)


@dataclass(frozen=True)
class ThemeEmbedder:
    def embed(self, texts):
        vectors = []
        for text in texts:
            folded = text.casefold()
            if "фонар" in folded or "свет" in folded:
                vectors.append((1.0, 0.0))
            elif "яма" in folded or "асфальт" in folded:
                vectors.append((0.0, 1.0))
            else:
                vectors.append((0.5, 0.5))
        return EmbeddingBatch(tuple(vectors), {"component": "fixture-theme"})


@dataclass(frozen=True)
class NoiseClusterer:
    def cluster(self, vectors, *, min_cluster_size, allow_single_cluster, seed):
        return ClusterAssignment(
            tuple(-1 for _ in vectors),
            {"component": "fixture-noise", "seed": seed},
        )


def _message(key: str, text: str) -> EventMessage:
    return EventMessage(
        message_key=key,
        model_text=text,
        published_at_utc=f"2026-08-0{1 + int(key[-1]) % 6}T10:00:00Z",
        category="ЖКХ",
        topic="освещение" if "фонар" in text else "дороги",
        point={"type": "Point", "coordinates": [49.1, 55.8]},
        scopes={
            "building": "osm:way:100",
            "road": "road:казань:тестовая",
            "global": "global",
        },
    )


def _engine(*, levels=(EventLevel.BUILDING,)) -> EventClusteringEngine:
    return EventClusteringEngine(
        embedder=ThemeEmbedder(),
        reducer=IdentityReductionBackend(),
        clusterer=CosineGraphClusterer(similarity_threshold=0.95),
        config=EventClusteringConfig(
            levels=levels,
            min_scope_messages=4,
            min_event_size=2,
            keyword_limit=4,
            representative_limit=2,
        ),
    )


def _messages() -> tuple[EventMessage, ...]:
    return (
        _message("m1", "Не работает фонарь во дворе"),
        _message("m2", "Фонарь погас у подъезда"),
        _message("m3", "Нет света возле дома"),
        _message("m4", "Большая яма на асфальте"),
        _message("m5", "Яма разрушает дорожное покрытие"),
        _message("m6", "Поврежден асфальт у дома"),
    )


def test_same_address_is_not_enough_to_merge_different_themes() -> None:
    result = _engine().cluster(_messages())

    building_events = [item for item in result.events if item.level is EventLevel.BUILDING]
    assert len(building_events) == 2
    assert {item.message_ids for item in building_events} == {
        ("m1", "m2", "m3"),
        ("m4", "m5", "m6"),
    }
    assert all(
        item.explanation["address_only_merge_prohibited"] is True
        for item in building_events
    )
    assert all(isinstance(item.to_dict()["message_ids"], list) for item in building_events)


def test_event_output_is_independent_of_input_order() -> None:
    messages = _messages()
    first = _engine(levels=(EventLevel.BUILDING, EventLevel.GLOBAL)).cluster(messages)
    second = _engine(levels=(EventLevel.BUILDING, EventLevel.GLOBAL)).cluster(
        tuple(reversed(messages))
    )

    assert first.input_digest == second.input_digest
    assert first.output_digest == second.output_digest
    assert [item.to_dict() for item in first.events] == [
        item.to_dict() for item in second.events
    ]


def test_single_cluster_is_valid_and_explained() -> None:
    messages = tuple(
        _message(f"m{index}", f"Не работает фонарь номер {index}")
        for index in range(1, 5)
    )
    result = _engine().cluster(messages)

    assert len(result.events) == 1
    assert result.events[0].size == 4
    diagnostic = next(item for item in result.diagnostics if item.object_id == "osm:way:100")
    assert diagnostic.status is ScopeStatus.CLUSTERED
    assert diagnostic.cluster_count == 1


def test_noise_is_diagnostic_only_even_when_include_noise_is_enabled() -> None:
    engine = EventClusteringEngine(
        embedder=ThemeEmbedder(),
        reducer=IdentityReductionBackend(),
        clusterer=NoiseClusterer(),
        config=EventClusteringConfig(
            levels=(EventLevel.BUILDING,),
            min_scope_messages=4,
            min_event_size=2,
            include_noise=True,
        ),
    )
    result = engine.cluster(_messages()[:4])

    assert result.events == ()
    assert result.stats.no_cluster_scopes == 1
    assert result.diagnostics[0].status is ScopeStatus.NO_CLUSTERS
    assert result.diagnostics[0].noise_count == 4
    assert result.component_provenance["noise_event_policy"] == "diagnostics_only"


def test_umap_small_scope_dimension_stays_below_spectral_limit() -> None:
    backend = UMAPReductionBackend(n_components=5)
    assert backend.effective_shape(3) == (2, 1)
    assert backend.effective_shape(5) == (4, 3)
    assert backend.effective_shape(6) == (5, 4)


def test_insufficient_scope_and_missing_link_are_explicit() -> None:
    engine = EventClusteringEngine(
        embedder=ThemeEmbedder(),
        reducer=IdentityReductionBackend(),
        clusterer=CosineGraphClusterer(similarity_threshold=0.95),
        config=EventClusteringConfig(
            levels=(EventLevel.BUILDING, EventLevel.LINK),
            min_scope_messages=5,
            min_event_size=2,
        ),
    )
    result = engine.cluster(_messages()[:3])

    statuses = {(item.level, item.status) for item in result.diagnostics}
    assert (EventLevel.BUILDING, ScopeStatus.INSUFFICIENT_DATA) in statuses
    assert (EventLevel.LINK, ScopeStatus.UNAVAILABLE) in statuses
    assert result.events == ()


def test_empty_input_is_supported_without_invoking_embedding_backend() -> None:
    result = _engine().cluster(())

    assert result.events == ()
    assert result.stats.received == 0
    assert result.component_provenance["embedding"]["component"] == "not_run"
