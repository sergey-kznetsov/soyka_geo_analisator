"""Public package of SOIKA UDS Development."""

from .contracts import (
    AnalysisResult,
    CoverageSummary,
    JobStatus,
    MessageClassification,
    ModelResult,
    PrecisionLevel,
    SourceMessage,
    TerritoryContext,
)
from .engine import SoikaEngine
from .prediction import Prediction, PredictionFormatError

__version__ = "0.2.0"

__all__ = [
    "AnalysisResult",
    "CoverageSummary",
    "JobStatus",
    "MessageClassification",
    "ModelResult",
    "PrecisionLevel",
    "Prediction",
    "PredictionFormatError",
    "SoikaEngine",
    "SourceMessage",
    "TerritoryContext",
    "__version__",
]
