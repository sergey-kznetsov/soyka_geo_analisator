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
from .integration import (
    CURRENT_CONTRACT_VERSION,
    SUPPORTED_CONTRACT_VERSIONS,
    AnalysisRequestV1,
    AnalysisResultV1,
    ContractIssue,
    ContractValidationError,
    ContractVersion,
    IdempotencyConflictError,
    JobStatusV1,
    MessageType,
    ResultProvenance,
    assert_idempotent_request,
    contract_info,
    export_schema_bundle,
    parse_contract_document,
    schema_bundle_digest,
)
from .prediction import Prediction, PredictionFormatError

__version__ = "0.4.0"

__all__ = [
    "CURRENT_CONTRACT_VERSION",
    "SUPPORTED_CONTRACT_VERSIONS",
    "AnalysisRequestV1",
    "AnalysisResult",
    "AnalysisResultV1",
    "ContractIssue",
    "ContractValidationError",
    "ContractVersion",
    "CoverageSummary",
    "IdempotencyConflictError",
    "JobStatus",
    "JobStatusV1",
    "MessageClassification",
    "MessageType",
    "ModelResult",
    "PrecisionLevel",
    "Prediction",
    "PredictionFormatError",
    "ResultProvenance",
    "SoikaEngine",
    "SourceMessage",
    "TerritoryContext",
    "assert_idempotent_request",
    "contract_info",
    "export_schema_bundle",
    "parse_contract_document",
    "schema_bundle_digest",
    "__version__",
]
