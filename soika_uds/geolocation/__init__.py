"""Production geolocation and OSM contour."""

from .artifacts import verify_model_artifact
from .cache import SQLiteResponseCache
from .crs import (
    haversine_distance_m,
    metric_crs_for,
    metric_distance_m,
    project_point,
)
from .evaluation import GeolocationValidationCase, evaluate_geolocation
from .extraction import (
    CompositeMentionExtractor,
    LocalFlairAddressExtractor,
    MentionExtractor,
    NatashaAddressExtractor,
    RuleBasedMentionExtractor,
)
from .factory import public_nominatim_client
from .model_manager import LazyModelManager
from .models import (
    ALGORITHM_VERSION,
    AddressMention,
    CandidateSource,
    GeocodingCandidate,
    GeolocationBatchResult,
    GeolocationConfig,
    GeolocationStats,
    GeoPoint,
    LocationKind,
    MentionSource,
    MessageGeolocationResult,
)
from .normalization import AddressNormalizer, clean_text, is_missing
from .orchestration import GeolocationStageHandler
from .providers import CandidateProvider, NominatimClient, OverpassClient
from .qualification_api import (
    GateResult,
    GateState,
    GeolocationModelAudit,
    GeolocationQualificationReport,
    GeolocationThresholds,
    GeolocationValidationCase as GeolocationQualificationCase,
    GeolocationValidationManifest,
    extraction_exact_rate,
    load_model_audit,
    load_qualified_registry,
    load_validation_manifest,
    low_confidence_rate,
    qualify_geolocation,
)
from .runtime import GeolocationEngine, GeolocationProviderError
from .transport import (
    HttpRetryPolicy,
    JsonTransport,
    RateLimiter,
    RequestsJsonTransport,
    TransportError,
)

__all__ = [
    "ALGORITHM_VERSION",
    "AddressMention",
    "AddressNormalizer",
    "CandidateProvider",
    "CandidateSource",
    "CompositeMentionExtractor",
    "GateResult",
    "GateState",
    "GeocodingCandidate",
    "GeolocationBatchResult",
    "GeolocationConfig",
    "GeolocationEngine",
    "GeolocationModelAudit",
    "GeolocationProviderError",
    "GeolocationQualificationCase",
    "GeolocationQualificationReport",
    "GeolocationStageHandler",
    "GeolocationStats",
    "GeolocationThresholds",
    "GeolocationValidationCase",
    "GeolocationValidationManifest",
    "GeoPoint",
    "HttpRetryPolicy",
    "JsonTransport",
    "LazyModelManager",
    "LocalFlairAddressExtractor",
    "LocationKind",
    "MentionExtractor",
    "MentionSource",
    "MessageGeolocationResult",
    "NatashaAddressExtractor",
    "NominatimClient",
    "OverpassClient",
    "RateLimiter",
    "RequestsJsonTransport",
    "RuleBasedMentionExtractor",
    "SQLiteResponseCache",
    "TransportError",
    "clean_text",
    "evaluate_geolocation",
    "extraction_exact_rate",
    "haversine_distance_m",
    "is_missing",
    "load_model_audit",
    "load_qualified_registry",
    "load_validation_manifest",
    "low_confidence_rate",
    "metric_crs_for",
    "metric_distance_m",
    "project_point",
    "public_nominatim_client",
    "qualify_geolocation",
    "verify_model_artifact",
]
