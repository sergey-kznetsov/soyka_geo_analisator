"""Production geolocation and OSM contour."""

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
    "GeocodingCandidate",
    "GeolocationBatchResult",
    "GeolocationConfig",
    "GeolocationEngine",
    "GeolocationProviderError",
    "GeolocationStageHandler",
    "GeolocationStats",
    "GeolocationValidationCase",
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
    "haversine_distance_m",
    "is_missing",
    "metric_crs_for",
    "metric_distance_m",
    "project_point",
]
