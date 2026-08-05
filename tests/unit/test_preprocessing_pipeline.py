from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from soika_uds.preprocessing import (
    DuplicateKind,
    LanguageCode,
    PreprocessingConfig,
    PreprocessingError,
    PreprocessingPipeline,
    detect_language,
    normalize_timestamp,
    semantic_text,
    similarity,
    split_quotes,
)


@dataclass(frozen=True)
class Message:
    source: str
    external_id: str
    text: str
    published_at: datetime
    url: str | None = None
    author_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def message(
    external_id: str,
    text: str,
    *,
    minute: int = 0,
    source: str = "fixture",
) -> Message:
    return Message(
        source=source,
        external_id=external_id,
        text=text,
        published_at=datetime(2026, 8, 5, 12, minute, tzinfo=UTC),
        url=f"https://example.test/{external_id}",
        metadata={"kind": "comment"},
    )


def test_pipeline_preserves_raw_text_and_records_transformations() -> None:
    raw = (
        "<style>hidden</style><p>Во дворе\u00a0сломался фонарь.</p>\n"
        "> Ранее обещали ремонт\nРеклама: подпишитесь"
    )
    result = PreprocessingPipeline().run([message("1", raw)])
    processed = result.messages[0]

    assert processed.raw_text == raw
    assert processed.normalized_text == (
        "Во дворе сломался фонарь.\n> Ранее обещали ремонт"
    )
    assert processed.author_text == "Во дворе сломался фонарь."
    assert processed.quotes == ("Ранее обещали ремонт",)
    assert processed.language is LanguageCode.RU
    assert [step.name for step in processed.transformations] == [
        "unicode_nfkc",
        "html_cleanup",
        "whitespace",
        "service_text",
        "quote_extraction",
    ]
    assert any(step.changed for step in processed.transformations)


def test_unicode_normalization_is_reproducible() -> None:
    first = PreprocessingPipeline().run([message("1", "ул. Пушкина 10")])
    second = PreprocessingPipeline().run([message("1", "ул. Пушкина 10")])
    assert first.messages[0].to_dict() == second.messages[0].to_dict()


def test_exact_duplicates_ignore_case_punctuation_and_yo() -> None:
    result = PreprocessingPipeline().run(
        [
            message("first", "Всё ещё затоплен двор!"),
            message("second", "ВСЕ еще затоплен двор", minute=1),
        ]
    )
    assert result.unique_count == 1
    assert result.exact_duplicate_count == 1
    assert result.messages[1].duplicate_kind is DuplicateKind.EXACT
    assert result.messages[1].duplicate_of == "fixture:first"


def test_near_duplicates_are_detected_inside_repeat_window() -> None:
    result = PreprocessingPipeline(
        PreprocessingConfig(near_duplicate_threshold=0.75)
    ).run(
        [
            message("first", "У дома десять снова не работает уличный фонарь"),
            message(
                "second",
                "У дома 10 снова не работает фонарь уличного освещения",
                minute=5,
            ),
        ]
    )
    assert result.near_duplicate_count == 1
    assert result.messages[1].duplicate_kind is DuplicateKind.NEAR
    assert result.messages[1].similarity is not None


def test_repeated_appeal_outside_window_remains_unique() -> None:
    first = message("first", "На остановке разбито стекло")
    second = Message(
        source="fixture",
        external_id="second",
        text="На остановке разбито стекло",
        published_at=first.published_at + timedelta(days=3),
    )
    result = PreprocessingPipeline(
        PreprocessingConfig(repeat_window_seconds=3600)
    ).run([first, second])
    assert result.unique_count == 2
    assert all(item.duplicate_kind is DuplicateKind.UNIQUE for item in result.messages)


def test_cross_source_exact_duplicate_is_linked_to_first_observation() -> None:
    result = PreprocessingPipeline().run(
        [
            message("a", "Перекрыта дорога у школы", source="vk"),
            message("b", "Перекрыта дорога у школы", minute=1, source="ok"),
        ]
    )
    assert result.messages[1].duplicate_of == "vk:a"


def test_timestamp_is_normalized_to_utc() -> None:
    value = datetime(2026, 8, 5, 15, 0, tzinfo=timezone(timedelta(hours=3)))
    assert normalize_timestamp(value) == datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(PreprocessingError, match="UTC offset"):
        normalize_timestamp(datetime(2026, 8, 5, 12, 0))


def test_language_detection() -> None:
    assert detect_language("сломался фонарь") is LanguageCode.RU
    assert detect_language("street light failed") is LanguageCode.EN
    assert detect_language("ремонт road") is LanguageCode.MIXED
    assert detect_language("123") is LanguageCode.UNKNOWN


def test_quote_split_does_not_lose_author_text() -> None:
    author, quotes = split_quotes("Ответ автора\nЦитата: исходное сообщение")
    assert author == "Ответ автора"
    assert quotes == ("исходное сообщение",)


def test_semantic_text_normalizes_case_yo_and_punctuation() -> None:
    assert semantic_text("Ёлка, У ДОМА №10") == "елка у дома 10"


def test_similarity_is_bounded_and_symmetric() -> None:
    left = "ремонт дороги возле дома десять"
    right = "возле дома десять ремонтируют дорогу"
    assert 0.0 <= similarity(left, right) <= 1.0
    assert similarity(left, right) == similarity(right, left)
