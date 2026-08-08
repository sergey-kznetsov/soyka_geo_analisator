"""Geo-first source discovery for Russian urban-content collection."""

from .access import (
    DirectorySourcePolicyResolver,
    NetworkRobotsEvaluator,
    RobotsEvaluator,
    SourceAccessAuthorizer,
    SourcePolicyResolver,
    StaticSourcePolicyResolver,
)
from .browser import (
    BrowserRenderError,
    BrowserRenderer,
    PlaywrightBrowserRenderer,
    RenderedComment,
    RenderedPage,
    classify_browser_block,
)
from .classify import SourceClassifier, geo_evidence
from .collection import (
    CandidateCollectionError,
    CandidateCollectionResult,
    CandidateCollector,
    CollectorRouter,
    DiscoveryCollectionStageHandler,
    source_message_document,
)
from .engine import DiscoveryEngine
from .http import StaticHtmlFetcher
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
    SearchProvider,
    SearchProviderError,
    StdlibJsonPostTransport,
    UnavailableSearchProvider,
    YandexSearchProvider,
    parse_yandex_xml,
)
from .public_web import PublicWebCollector, geo_relevance_hint
from .query import GeoQueryBuilder
from .territory import (
    TerritoryGeolocationEngine,
    TerritoryResolutionError,
    TerritoryResolver,
)

__all__ = [
    "ACTIVE_SOURCE_KINDS",
    "BrowserRenderError",
    "BrowserRenderer",
    "CandidateCollectionError",
    "CandidateCollectionResult",
    "CandidateCollector",
    "CollectorRouter",
    "DirectorySourcePolicyResolver",
    "DiscoveryCollectionStageHandler",
    "DiscoveryEngine",
    "DiscoveryPlan",
    "DiscoveryQuery",
    "GeoDiscoveryPreparingHandler",
    "GeoQueryBuilder",
    "GeoScope",
    "JsonPostTransport",
    "NetworkRobotsEvaluator",
    "PlaywrightBrowserRenderer",
    "PublicWebCollector",
    "RenderedComment",
    "RenderedPage",
    "RobotsEvaluator",
    "SearchHit",
    "SearchProvider",
    "SearchProviderError",
    "SourceAccessAuthorizer",
    "SourceCandidate",
    "SourceClassifier",
    "SourceKind",
    "SourceOutcome",
    "SourcePolicyResolver",
    "SourceReasonCode",
    "SourceState",
    "StaticHtmlFetcher",
    "StaticSourcePolicyResolver",
    "StdlibJsonPostTransport",
    "TerritoryGeolocationEngine",
    "TerritoryResolutionError",
    "TerritoryResolver",
    "UnavailableSearchProvider",
    "YandexSearchProvider",
    "canonical_url",
    "classify_browser_block",
    "geo_evidence",
    "geo_relevance_hint",
    "parse_yandex_xml",
    "source_message_document",
]
