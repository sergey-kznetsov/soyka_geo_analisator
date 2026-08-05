from datetime import datetime, timezone

from soika_uds import Prediction, SoikaEngine, SourceMessage


class FakeClassifier:
    def __init__(self, model_id, predictions):
        self.REP_ID = model_id
        self.MODEL_REVISION = "revision-1"
        self._predictions = predictions

    def predict(self, text):
        assert text
        return self._predictions


def test_engine_returns_structured_category_and_topic():
    engine = SoikaEngine(
        category_classifier=FakeClassifier(
            "category-model", [Prediction("ЖКХ", 0.9), Prediction("Дороги", 0.1)]
        ),
        topic_classifier=FakeClassifier(
            "topic-model", [Prediction("Водоотведение", 0.8)]
        ),
    )
    message = SourceMessage(
        source="portal",
        external_id="1",
        text="Затопило двор",
        published_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )

    result = engine.classify_message(message)

    assert result.category.primary.label == "ЖКХ"
    assert result.category.alternatives[0].label == "Дороги"
    assert result.topic.primary.label == "Водоотведение"
