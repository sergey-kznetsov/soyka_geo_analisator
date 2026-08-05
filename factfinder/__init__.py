"""Backward-compatible, lazy public interface of the imported SOIKA package."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

__all__ = [
    "EventDetection",
    "Geocoder",
    "TextClassifier",
    "TextClassifierTopics",
]

_EXPORTS = {
    "EventDetection": ("factfinder.src.event_detection", "EventDetection"),
    "Geocoder": ("factfinder.src.geocoder", "Geocoder"),
    "TextClassifier": ("factfinder.src.text_classifier", "TextClassifier"),
    "TextClassifierTopics": (
        "factfinder.src.text_classifier_topics",
        "TextClassifierTopics",
    ),
}

if TYPE_CHECKING:
    from .src.event_detection import EventDetection
    from .src.geocoder import Geocoder
    from .src.text_classifier import TextClassifier
    from .src.text_classifier_topics import TextClassifierTopics


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
