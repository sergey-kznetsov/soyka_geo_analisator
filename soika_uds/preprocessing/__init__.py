"""Deterministic preprocessing and deduplication public API."""

from .models import (
    DuplicateDecision,
    DuplicateKind,
    LanguageResult,
    MessageDecision,
    PreprocessedMessage,
    PreprocessingConfig,
    PreprocessingResult,
    PreprocessingStats,
    TransformationTrace,
)
from .orchestration import PreprocessingStageHandler
from .pipeline import (
    ALGORITHM_VERSION,
    SCHEMA_VERSION,
    canonicalize_url,
    detect_language,
    preprocess_messages,
    source_message_from_dict,
    source_message_to_dict,
)

__all__ = [
    "ALGORITHM_VERSION",
    "SCHEMA_VERSION",
    "DuplicateDecision",
    "DuplicateKind",
    "LanguageResult",
    "MessageDecision",
    "PreprocessedMessage",
    "PreprocessingConfig",
    "PreprocessingResult",
    "PreprocessingStageHandler",
    "PreprocessingStats",
    "TransformationTrace",
    "canonicalize_url",
    "detect_language",
    "preprocess_messages",
    "source_message_from_dict",
    "source_message_to_dict",
]
