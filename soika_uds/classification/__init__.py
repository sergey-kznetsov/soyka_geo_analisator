"""Production classification and topic-refinement public API."""

from .backend import TransformersPredictionBackend
from .calibration import (
    IdentityCalibrator,
    PiecewiseLinearCalibrator,
    ScoreCalibrator,
    calibrate_scores,
)
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
    classification_model_registry_digest,
)
from .orchestration import ClassificationStageHandler
from .qualification import (
    BenchmarkEvidence,
    GateResult,
    GateState,
    ModelAuditRecord,
    QualificationInput,
    QualificationPolicy,
    QualificationReport,
    QualityEvidence,
    ValidationSetEvidence,
    audited_model_registry_digest,
    qualify_release,
)
from .qualification_loader import (
    load_qualification_input,
    qualification_input_from_dict,
)
from .registry import ClassificationRegistry, load_classification_registry
from .runtime import ClassificationEngine, PredictionBackend

__all__ = [
    "BenchmarkEvidence",
    "ClassificationBatchResult",
    "ClassificationConfig",
    "ClassificationEngine",
    "ClassificationRegistry",
    "ClassificationStageHandler",
    "ClassificationStats",
    "ConfidenceBand",
    "ExecutionDevice",
    "GateResult",
    "GateState",
    "IdentityCalibrator",
    "LabelPrediction",
    "MessageClassificationResult",
    "ModelAuditRecord",
    "ModelDescriptor",
    "PiecewiseLinearCalibrator",
    "PredictionBackend",
    "QualificationInput",
    "QualificationPolicy",
    "QualificationReport",
    "QualityEvidence",
    "ScoreCalibrator",
    "TransformersPredictionBackend",
    "ValidationLabel",
    "ValidationSetEvidence",
    "audited_model_registry_digest",
    "calibrate_scores",
    "classification_model_registry_digest",
    "evaluate_predictions",
    "label_distribution",
    "load_classification_registry",
    "load_qualification_input",
    "qualification_input_from_dict",
    "qualify_release",
    "total_variation_drift",
]
