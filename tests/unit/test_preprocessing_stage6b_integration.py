from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from soika_uds.preprocessing import DuplicateKind, PreprocessingPipeline


@dataclass(frozen=True)
class ParserMessageFixture:
    source: str
    external_id: str
    text: str
    published_at: datetime
    url: str | None = None
    author_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def test_stage6b_sources_pass_through_preprocessing_without_data_loss() -> None:
    started = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    fixtures = [
        ParserMessageFixture(
            "vk",
            "post:-1:10",
            "На улице Центральной ремонтируют освещение",
            started,
        ),
        ParserMessageFixture(
            "ok",
            "comment:GROUP_TOPIC:1:ok-1",
            "Остановка у школы требует ремонта",
            started + timedelta(minutes=1),
        ),
        ParserMessageFixture(
            "local-media",
            "feed:1",
            "<p>Начался ремонт дороги возле жилого квартала</p>",
            started + timedelta(minutes=2),
        ),
        ParserMessageFixture(
            "municipal-public",
            "document:1",
            "Муниципалитет сообщил о ремонте тротуара",
            started + timedelta(minutes=3),
        ),
        ParserMessageFixture(
            "dzen",
            "document:2",
            "Жители обсудили благоустройство сквера",
            started + timedelta(minutes=4),
        ),
        ParserMessageFixture(
            "pikabu",
            "document:3",
            "Пользователи сообщили о яме на дороге",
            started + timedelta(minutes=5),
        ),
        ParserMessageFixture(
            "rutube",
            "document:4",
            "Видео о ремонте городской набережной",
            started + timedelta(minutes=6),
        ),
    ]

    result = PreprocessingPipeline().run(fixtures)

    assert len(result.messages) == 7
    assert result.unique_count == 7
    assert result.exact_duplicate_count == 0
    assert result.near_duplicate_count == 0
    assert {item.source for item in result.messages} == {
        "vk",
        "ok",
        "local-media",
        "municipal-public",
        "dzen",
        "pikabu",
        "rutube",
    }
    assert all(item.raw_text for item in result.messages)
    assert all(item.normalized_text for item in result.messages)
    assert all(item.transformations for item in result.messages)
    assert all(item.duplicate_kind is DuplicateKind.UNIQUE for item in result.messages)
    local_media = next(item for item in result.messages if item.source == "local-media")
    assert local_media.raw_text.startswith("<p>")
    assert local_media.normalized_text == (
        "Начался ремонт дороги возле жилого квартала"
    )
