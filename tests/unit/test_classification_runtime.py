from __future__ import annotations

from collections.abc import Sequence

import pytest

from soika_uds.classification import (
    ClassificationConfig,
    ClassificationEngine,
    ClassificationRegistry,
    ConfidenceBand,
    ExecutionDevice,
    LabelPrediction,
    ModelDescriptor,
    classification_model_registry_digest,
)

MODEL_COMMIT = "a" * 40
TOKENIZER_COMMIT = "b" * 40
CATEGORY_SHA = "c" * 64
TOPIC_SHA = "d" * 64
REPORT_SHA = "e" * 64


class FakeBackend:
    def __init__(
        self,
        category_rows: Sequence[Sequence[LabelPrediction]],
        topic_rows: Sequence[Sequence[LabelPrediction]],
    ) -> None:
        self.category_rows = category_rows
        self.topic_rows = topic_rows
        self.calls: list[tuple[str, tuple[str, ...], int, str]] = []

    def predict_batch(
        self,
        texts: Sequence[str],
        *,
        model: ModelDescriptor,
        batch_size: int,
        device: str,
    ) -> Sequence[Sequence[LabelPrediction]]:
        self.calls.append((model.task, tuple(texts), batch_size, device))
        return self.category_rows if model.task == "category" else self.topic_rows


def descriptor(name: str, task: str) -> ModelDescriptor:
    labels = (
        ("roads", "lighting")
        if task == "category"
        else ("pothole", "road_surface", "lamp_outage", "dark_yard")
    )
    return ModelDescriptor(
        name=name,
        repo_id=f"example/{name}",
        revision=MODEL_COMMIT,
        license="MIT",
        task=task,
        tokenizer_id="example/tokenizer",
        tokenizer_revision=TOKENIZER_COMMIT,
        weights_sha256=CATEGORY_SHA if task == "category" else TOPIC_SHA,
        approved_for_production=True,
        training_data_review="approved test fixture",
        label_space=labels,
        label_map={"LABEL_0": labels[0]},
    )


def hierarchy() -> dict[str, tuple[str, ...]]:
    return {
        "roads": ("pothole", "road_surface"),
        "lighting": ("lamp_outage", "dark_yard"),
    }


def registry() -> ClassificationRegistry:
    models = {
        "category": descriptor("category", "category"),
        "topic": descriptor("topic", "topic"),
    }
    return ClassificationRegistry(
        models,
        topic_hierarchy=hierarchy(),
        qualification_report_digest=REPORT_SHA,
        qualified_model_registry_digest=classification_model_registry_digest(
            models,
            hierarchy(),
        ),
    )


def message(
    key: str,
    text: str,
    *,
    accepted: bool = True,
    included: bool = True,
) -> dict[str, object]:
    return {
        "message_key": key,
        "model_text": text,
        "decision": "accepted" if accepted else "rejected",
        "duplicate": {"included_for_analysis": included},
    }


def test_batch_classification_maps_labels_and_preserves_order() -> None:
    backend = FakeBackend(
        category_rows=(
            (LabelPrediction("LABEL_0", 0.91),),
            (LabelPrediction("roads", 0.80),),
        ),
        topic_rows=(
            (LabelPrediction("pothole", 0.88),),
            (LabelPrediction("road_surface", 0.79),),
        ),
    )
    engine = ClassificationEngine(
        registry(),
        backend,
        ClassificationConfig(batch_size=2, device=ExecutionDevice.CPU),
    )

    result = engine.classify(
        [
            message("vk:2", "На дороге яма"),
            message("vk:1", "Повреждено дорожное покрытие"),
        ]
    )

    assert [item.message_key for item in result.results] == ["vk:1", "vk:2"]
    assert result.results[0].category.label == "roads"
    assert result.results[0].confidence_band is ConfidenceBand.HIGH
    assert result.stats.classified == 2
    assert backend.calls[0][2:] == (2, "cpu")


def test_topic_is_refined_inside_predicted_category() -> None:
    backend = FakeBackend(
        category_rows=((LabelPrediction("roads", 0.95),),),
        topic_rows=(
            (
                LabelPrediction("lamp_outage", 0.99),
                LabelPrediction("pothole", 0.80),
            ),
        ),
    )
    result = ClassificationEngine(registry(), backend).classify(
        [message("vk:1", "На дороге яма")]
    )
    assert result.results[0].topic.label == "pothole"
    assert result.results[0].model_provenance["allowed_topics"] == (
        "pothole",
        "road_surface",
    )


def test_low_confidence_is_explicit_and_excluded() -> None:
    backend = FakeBackend(
        category_rows=((LabelPrediction("roads", 0.95),),),
        topic_rows=((LabelPrediction("pothole", 0.40),),),
    )
    result = ClassificationEngine(registry(), backend).classify(
        [message("vk:1", "На дороге яма")]
    )

    item = result.results[0]
    assert item.low_confidence is True
    assert item.included_for_analysis is False
    assert item.confidence_band is ConfidenceBand.LOW
    assert item.reasons == ("topic_below_threshold",)
    assert result.stats.low_confidence == 1


def test_rejected_and_duplicate_messages_are_skipped_by_default() -> None:
    backend = FakeBackend(category_rows=(), topic_rows=())
    result = ClassificationEngine(registry(), backend).classify(
        [
            message("vk:1", "bad", accepted=False),
            message("ok:1", "duplicate", included=False),
        ]
    )
    assert result.stats.received == 2
    assert result.stats.classified == 0
    assert result.stats.skipped == 2
    assert backend.calls == [
        ("category", (), 32, "cpu"),
        ("topic", (), 32, "cpu"),
    ]


def test_output_is_deterministic() -> None:
    backend_one = FakeBackend(
        category_rows=((LabelPrediction("roads", 0.90),),),
        topic_rows=((LabelPrediction("pothole", 0.90),),),
    )
    backend_two = FakeBackend(
        category_rows=((LabelPrediction("roads", 0.90),),),
        topic_rows=((LabelPrediction("pothole", 0.90),),),
    )
    first = ClassificationEngine(registry(), backend_one).classify(
        [message("vk:1", "На дороге яма")]
    )
    second = ClassificationEngine(registry(), backend_two).classify(
        [message("vk:1", "На дороге яма")]
    )
    assert first.to_dict() == second.to_dict()


def test_nested_provenance_is_immutable() -> None:
    backend = FakeBackend(
        category_rows=((LabelPrediction("roads", 0.90),),),
        topic_rows=((LabelPrediction("pothole", 0.90),),),
    )
    item = ClassificationEngine(registry(), backend).classify(
        [message("vk:1", "На дороге яма")]
    ).results[0]
    with pytest.raises(TypeError):
        item.model_provenance["category_model"]["name"] = "changed"


def test_registry_rejects_mutable_revision() -> None:
    with pytest.raises(ValueError, match="40-character"):
        ModelDescriptor(
            name="category",
            repo_id="example/category",
            revision="release-v1",
            license="MIT",
            task="category",
            tokenizer_id="example/tokenizer",
            tokenizer_revision=TOKENIZER_COMMIT,
            weights_sha256=CATEGORY_SHA,
            approved_for_production=True,
            training_data_review="approved",
            label_space=("roads",),
        )


def test_registry_rejects_unapproved_model() -> None:
    unapproved = ModelDescriptor(
        name="category",
        repo_id="example/category",
        revision=MODEL_COMMIT,
        license="MIT",
        task="category",
        tokenizer_id="example/tokenizer",
        tokenizer_revision=TOKENIZER_COMMIT,
        weights_sha256=CATEGORY_SHA,
        approved_for_production=False,
        training_data_review="pending",
        label_space=("roads", "lighting"),
    )
    models = {
        "category": unapproved,
        "topic": descriptor("topic", "topic"),
    }
    with pytest.raises(ValueError, match="approved"):
        ClassificationRegistry(
            models,
            topic_hierarchy=hierarchy(),
            qualification_report_digest=REPORT_SHA,
            qualified_model_registry_digest=classification_model_registry_digest(
                models,
                hierarchy(),
            ),
        )


def test_registry_rejects_descriptor_assigned_to_wrong_role() -> None:
    models = {
        "category": descriptor("wrong", "topic"),
        "topic": descriptor("topic", "topic"),
    }
    with pytest.raises(ValueError, match="does not match role"):
        ClassificationRegistry(
            models,
            topic_hierarchy=hierarchy(),
            qualification_report_digest=REPORT_SHA,
            qualified_model_registry_digest=CATEGORY_SHA,
        )


def test_registry_rejects_incomplete_topic_hierarchy() -> None:
    models = {
        "category": descriptor("category", "category"),
        "topic": descriptor("topic", "topic"),
    }
    incomplete = {"roads": ("pothole", "road_surface")}
    with pytest.raises(ValueError, match="category mismatch"):
        ClassificationRegistry(
            models,
            topic_hierarchy=incomplete,
            qualification_report_digest=REPORT_SHA,
            qualified_model_registry_digest=classification_model_registry_digest(
                models,
                incomplete,
            ),
        )
