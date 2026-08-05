import pytest

from factfinder import TextClassifier, TextClassifierTopics
from soika_uds.prediction import Prediction


class FakePipeline:
    def __init__(self, output):
        self.output = output
        self.calls = []

    def __call__(self, text, *, top_k):
        self.calls.append((text, top_k))
        return self.output


def factory_for(output, captured):
    def factory(task, **kwargs):
        captured["task"] = task
        captured["kwargs"] = kwargs
        pipeline = FakePipeline(output)
        captured["pipeline"] = pipeline
        return pipeline

    return factory


def test_text_classifier_has_structured_and_legacy_outputs():
    captured = {}
    classifier = TextClassifier(
        number_of_categories=2,
        model_revision="abc123",
        pipeline_factory=factory_for(
            [{"label": "ЖКХ", "score": 0.91}, {"label": "Дороги", "score": 0.06}],
            captured,
        ),
    )

    assert classifier.predict("Течёт труба") == [
        Prediction("ЖКХ", 0.91),
        Prediction("Дороги", 0.06),
    ]
    assert classifier.run("Течёт труба") == ["ЖКХ; Дороги", "0.91; 0.06"]
    assert captured["task"] == "text-classification"
    assert captured["kwargs"]["revision"] == "abc123"


def test_topic_classifier_uses_topic_model_by_default():
    captured = {}
    classifier = TextClassifierTopics(
        pipeline_factory=factory_for(
            [{"label": "Ливневая канализация", "score": 0.8}], captured
        )
    )

    assert classifier.REP_ID == "Sandrro/text_to_subfunction_v10"
    assert classifier.run("Затопило двор") == ["Ливневая канализация", "0.8"]


def test_classifier_rejects_invalid_input_before_model_call():
    captured = {}
    classifier = TextClassifier(
        pipeline_factory=factory_for([{"label": "ЖКХ", "score": 0.9}], captured)
    )

    with pytest.raises(TypeError):
        classifier.run(None)
    with pytest.raises(ValueError):
        classifier.run("   ")
