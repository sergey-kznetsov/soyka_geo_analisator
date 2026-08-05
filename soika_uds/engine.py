"""Product-facing orchestration shell around normalized SOIKA classifiers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from .contracts import MessageClassification, ModelResult, SourceMessage
from .prediction import Prediction


class ClassifierProtocol(Protocol):
    REP_ID: str
    MODEL_REVISION: str | None

    def predict(self, text: str) -> list[Prediction]:
        ...


class SoikaEngine:
    """Lazy product facade for the normalized SOIKA processing pipeline."""

    def __init__(
        self,
        category_classifier: ClassifierProtocol | None = None,
        topic_classifier: ClassifierProtocol | None = None,
        *,
        category_classifier_options: dict[str, Any] | None = None,
        topic_classifier_options: dict[str, Any] | None = None,
    ) -> None:
        self._category_classifier = category_classifier
        self._topic_classifier = topic_classifier
        self._category_classifier_options = dict(category_classifier_options or {})
        self._topic_classifier_options = dict(topic_classifier_options or {})

    @property
    def category_classifier(self) -> ClassifierProtocol:
        if self._category_classifier is None:
            from factfinder import TextClassifier

            self._category_classifier = TextClassifier(
                **self._category_classifier_options
            )
        return self._category_classifier

    @property
    def topic_classifier(self) -> ClassifierProtocol:
        if self._topic_classifier is None:
            from factfinder import TextClassifierTopics

            self._topic_classifier = TextClassifierTopics(
                **self._topic_classifier_options
            )
        return self._topic_classifier

    @staticmethod
    def _to_model_result(classifier: ClassifierProtocol, text: str) -> ModelResult:
        predictions = classifier.predict(text)
        return ModelResult(
            primary=predictions[0],
            alternatives=tuple(predictions[1:]),
            model_id=classifier.REP_ID,
            model_revision=classifier.MODEL_REVISION,
        )

    def classify_message(self, message: SourceMessage) -> MessageClassification:
        return MessageClassification(
            message=message,
            category=self._to_model_result(self.category_classifier, message.text),
            topic=self._to_model_result(self.topic_classifier, message.text),
        )

    def classify_messages(
        self, messages: Iterable[SourceMessage]
    ) -> list[MessageClassification]:
        return [self.classify_message(message) for message in messages]
