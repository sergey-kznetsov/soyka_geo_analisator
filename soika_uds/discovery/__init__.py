"""Geo-first source discovery for Russian urban-content collection."""

from .classify import SourceClassifier, geo_evidence
from .engine import DiscoveryEngine
from .models import (
    ACTIVE_SOURCE_KINDS,
    DiscoveryPlan,
    DiscoveryQuery,
    GeoScope,
    SearchHit,
    SourceCandidate,
    SourceKind,
    SourceOutcome,
    SourceReasonCode,
    SourceState,
    canonical_url,
)
from .orchestration import GeoDiscoveryPreparingHandler
from .providers import (
    JsonPostTransport,
    RequestsJsonPostTransport,
    SearchProvider,
    SearchProviderError,
    UnavailableSearchProvider,
    YandexSearchProvider,
    parse_yandex_xml,
)
from .query import GeoQueryBuilder
from .territory import (
    TerritoryGeolocationEngine,
    TerritoryResolutionError,
    TerritoryResolver,
)

__all__ = [
    "ACTIVE_SOURCE_KINDS",
    "DiscoveryEngine",
    "DiscoveryPlan",
    "DiscoveryQuery",
    "GeoDiscoveryPreparingHandler",
    "GeoQueryBuilder",
    "GeoScope",
    "JsonPostTransport",
    "RequestsJsonPostTransport",
    "SearchHit",
    "SearchProvider",
    "SearchProviderError",
    "SourceCandidate",
    "SourceClassifier",
    "SourceKind",
    "SourceOutcome",
    "SourceReasonCode",
    "SourceState",
    "TerritoryGeolocationEngine",
    "TerritoryResolutionError",
    "TerritoryResolver",
    "UnavailableSearchProvider",
    "YandexSearchProvider",
    "canonical_url",
    "geo_evidence",
    "parse_yandex_xml",
]
