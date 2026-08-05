from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from soika_uds.contracts import SourceMessage, TerritoryContext
from soika_uds.integration import AnalysisRequestV1
from soika_uds.orchestration import PipelineStage, StageContext
from soika_uds.preprocessing import (
    DuplicateKind,
    MessageDecision,
    PreprocessingConfig,
    PreprocessingStageHandler,
    canonicalize_url,
    detect_language,
    preprocess_messages,
    source_message_to_dict,
)

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def message(
    external_id: str,
    text: str,
    *,
    source: str = "fixture",
    published_at: datetime = NOW,
    url: str | None = None,
) -> SourceMessage:
    return SourceMessage(
        source=source,
        external_id=external_id,
        text=text,
        published_at=published_at,
        url=url,
        metadata={"kind": "comment"},
    )


def request() -> AnalysisRequestV1:
    return AnalysisRequestV1(
        analysis_id="stage-7",
        requested_at=NOW,
        territory=TerritoryContext(
            analysis_id="stage-7",
            city="Казань",
            address="улица Баумана, 1",
        ),
        sources=("fixture",),
    )


def test_html_unicode_quotes_and_technical_text_are_processed_with_trace() -> None:
    original = (
        "<p>  У дома № 5&nbsp;сломался <b>фонарь</b>.</p>"
        "<blockquote>Старый ответ</blockquote>"
        "<script>secret()</script>\n> Цитата\nПоказать полностью"
    )
    result = preprocess_messages((message("1", original),))
    processed = result.messages[0]

    assert processed.original_text == original
    assert processed.model_text == "У дома No 5 сломался фонарь."
    assert processed.quote_texts == ("Старый ответ", "Цитата")
    assert "secret" not in processed.normalized_text
    assert "Показать полностью" not in processed.normalized_text
    assert processed.language.code == "ru"
    assert [trace.step for trace in processed.transformations] == [
        "html_to_text",
        "unicode_normalization",
        "remove_controls",
        "normalize_whitespace",
        "remove_technical_lines",
        "separate_quotes",
    ]
    assert all(
        left.output_sha256 == right.input_sha256
        for left, right in zip(
            processed.transformations[:-1],
            processed.transformations[1:],
            strict=True,
        )
    )


def test_nfkc_and_control_character_removal_are_reproducible() -> None:
    result = preprocess_messages((message("1", "Ａ\u200b Б\x00"),))
    processed = result.messages[0]

    assert processed.model_text == "A Б"
    assert processed.original_text == "Ａ\u200b Б\x00"
    assert result.config_digest
    assert result.input_digest
    assert result.output_digest


def test_language_detection_reports_script_evidence() -> None:
    assert detect_language("На улице ремонтируют дорогу").code == "ru"
    assert detect_language("Road works started today").code == "en"
    assert detect_language("Ремонт road").code == "mixed"
    assert detect_language("12").code == "unknown"


def test_url_canonicalization_removes_fragment_and_tracking_parameters() -> None:
    assert canonicalize_url(
        "HTTPS://Example.RU/news/?utm_source=test&id=2#comments"
    ) == "https://example.ru/news?id=2"


def test_same_source_external_id_is_a_technical_duplicate() -> None:
    result = preprocess_messages(
        (
            message("same", "Не работает фонарь"),
            message(
                "same",
                "Не работает фонарь",
                published_at=NOW + timedelta(minutes=5),
            ),
        )
    )

    assert result.messages[0].duplicate.kind is DuplicateKind.UNIQUE
    assert result.messages[1].duplicate.kind is DuplicateKind.TECHNICAL_DUPLICATE
    assert not result.messages[1].duplicate.included_for_analysis
    assert result.stats.included_for_analysis == 1


def test_same_identity_remains_technical_after_recurrence_interval() -> None:
    result = preprocess_messages(
        (
            message("same", "Снова не работает фонарь"),
            message(
                "same",
                "Снова не работает фонарь",
                published_at=NOW + timedelta(days=2),
            ),
        )
    )

    duplicate = result.messages[1]
    assert duplicate.duplicate.kind is DuplicateKind.TECHNICAL_DUPLICATE
    assert duplicate.duplicate.reasons == ("same_source_external_id",)
    assert not duplicate.duplicate.included_for_analysis


def test_cross_source_repost_is_preserved_but_excluded_from_analysis() -> None:
    result = preprocess_messages(
        (
            message("vk-1", "На улице ремонтируют освещение", source="vk"),
            message(
                "ok-1",
                "На улице ремонтируют освещение",
                source="ok",
                published_at=NOW + timedelta(minutes=1),
            ),
        )
    )

    repost = result.messages[1]
    assert repost.duplicate.kind is DuplicateKind.CROSS_SOURCE_REPOST
    assert repost.original_text == "На улице ремонтируют освещение"
    assert not repost.duplicate.included_for_analysis
    assert result.stats.cross_source_reposts == 1


def test_cross_source_marker_does_not_turn_repost_into_repeated_appeal() -> None:
    result = preprocess_messages(
        (
            message("vk-1", "Снова не работает освещение", source="vk"),
            message(
                "ok-1",
                "Снова не работает освещение",
                source="ok",
                published_at=NOW + timedelta(days=2),
            ),
        )
    )

    repost = result.messages[1]
    assert repost.duplicate.kind is DuplicateKind.CROSS_SOURCE_REPOST
    assert repost.duplicate.reasons == ("same_model_text", "different_source")
    assert not repost.duplicate.included_for_analysis


def test_repeated_appeal_remains_an_independent_observation() -> None:
    result = preprocess_messages(
        (
            message("1", "Во дворе не работает освещение"),
            message(
                "2",
                "Во дворе не работает освещение",
                published_at=NOW + timedelta(days=2),
            ),
        )
    )

    repeated = result.messages[1]
    assert repeated.duplicate.kind is DuplicateKind.REPEATED_APPEAL
    assert repeated.duplicate.included_for_analysis
    assert result.stats.included_for_analysis == 2
    assert result.stats.repeated_appeals == 1


def test_near_duplicate_detection_uses_text_similarity_and_simhash() -> None:
    result = preprocess_messages(
        (
            message("1", "На улице Ленина не работает освещение во дворе."),
            message(
                "2",
                "На улице Ленина не работает освещение во дворе!",
                published_at=NOW + timedelta(minutes=10),
            ),
        )
    )

    duplicate = result.messages[1]
    assert duplicate.duplicate.kind is DuplicateKind.TECHNICAL_DUPLICATE
    assert duplicate.duplicate.reasons == ("near_duplicate_text",)
    assert duplicate.duplicate.similarity > 0.95


def test_input_order_does_not_change_result_or_representative() -> None:
    first = message("b", "Не убирают снег", published_at=NOW + timedelta(minutes=1))
    second = message("a", "Не убирают снег", published_at=NOW)

    forward = preprocess_messages((first, second))
    reverse = preprocess_messages((second, first))

    assert forward.output_digest == reverse.output_digest
    assert [item.to_dict() for item in forward.messages] == [
        item.to_dict() for item in reverse.messages
    ]
    assert forward.messages[1].duplicate.representative_key == "fixture:a"


def test_invalid_timestamp_and_empty_text_preserve_original() -> None:
    naive = datetime(2026, 8, 5, 12, 0)
    result = preprocess_messages(
        (message("1", "Показать полностью", published_at=naive),)
    )
    processed = result.messages[0]

    assert processed.decision is MessageDecision.REJECTED
    assert set(processed.rejection_reasons) == {
        "MODEL_TEXT_TOO_SHORT",
        "PUBLISHED_AT_MISSING_TIMEZONE",
    }
    assert processed.original_text == "Показать полностью"
    assert not processed.duplicate.included_for_analysis


def test_config_can_keep_cross_source_reposts_for_analysis() -> None:
    config = PreprocessingConfig(drop_cross_source_reposts=False)
    result = preprocess_messages(
        (
            message("1", "Ремонт дороги", source="vk"),
            message("2", "Ремонт дороги", source="ok"),
        ),
        config,
    )

    assert result.stats.included_for_analysis == 2
    assert result.messages[1].duplicate.kind is DuplicateKind.CROSS_SOURCE_REPOST
    assert result.messages[1].duplicate.included_for_analysis


def test_orchestration_handler_reads_collection_and_returns_json_result() -> None:
    collected = message("1", "<p>Во дворе яма</p>")
    context = StageContext(
        request=request(),
        stage=PipelineStage.PREPROCESSING,
        attempt=1,
        worker_id="worker-test",
        previous_outputs={
            "collection": {"messages": [source_message_to_dict(collected)]}
        },
    )

    stage_result = PreprocessingStageHandler().run(context)

    assert stage_result.processed_items == 1
    assert stage_result.total_items == 1
    assert stage_result.output["preprocessing"]["stats"]["received"] == 1
    assert stage_result.output["preprocessing"]["analysis_message_keys"] == [
        "fixture:1"
    ]


def test_orchestration_handler_rejects_missing_collection_output() -> None:
    context = StageContext(
        request=request(),
        stage=PipelineStage.PREPROCESSING,
        attempt=1,
        worker_id="worker-test",
        previous_outputs={},
    )

    with pytest.raises(Exception, match="collection"):
        PreprocessingStageHandler().run(context)
