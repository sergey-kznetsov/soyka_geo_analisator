"""Production classification and topic-refinement public API."""

from .backend import TransformersPredictionBackend
from .evaluation import (
    ValidationLabel,
    evaluate_predictions,
    label_distribution,
    total_variation_drift,
)
from .models import (
    ClassificationBatchResult,
    ClassificationConfig,
    ClassificationStats,
    ConfidenceBand,
    ExecutionDevice,
    LabelPrediction,
    MessageClassificationResult,
    ModelDescriptor,
)
from .orchestration import ClassificationStageHandler
from .registry import ClassificationRegistry, load_classification_registry
from .runtime import ClassificationEngine, PredictionBackend

__all__ = [
    "ClassificationBatchResult",
    "ClassificationConfig",
    "ClassificationEngine",
    "ClassificationRegistry",
    "ClassificationStageHandler",
    "ClassificationStats",
    "ConfidenceBand",
    "ExecutionDevice",
    "LabelPrediction",
    "MessageClassificationResult",
    "ModelDescriptor",
    "PredictionBackend",
    "TransformersPredictionBackend",
    "ValidationLabel",
    "evaluate_predictions",
    "label_distribution",
    "load_classification_registry",
    "total_variation_drift",
]
