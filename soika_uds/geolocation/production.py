"""Activation of an approved geolocation registry in production runtime."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .cache import SQLiteResponseCache
from .extraction import MentionExtractor
from .models import (
    GeolocationBatchResult,
    GeolocationConfig,
    GeolocationStats,
    LocationKind,
    MessageGeolocationResult,
    digest_json,
)
from .qualification_api import load_qualified_registry
from .runtime import GeolocationEngine
from .semantic_provider import SemanticNominatimClient
from .transport import HttpRetryPolicy, RateLimiter, RequestsJsonTransport

_PUBLIC_NOMINATIM = "https://nominatim.openstreetmap.org"


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be an object")
    return value


class QualifiedGeolocationEngine:
    """Apply approved-level scope and registry provenance to every result."""

    def __init__(self, engine: GeolocationEngine, registry: Mapping[str, Any]) -> None:
        if registry.get("approved_for_production") is not True:
            raise ValueError("geolocation registry is not approved for production")
        validation = _mapping(registry.get("validation"), "registry.validation")
        levels = validation.get("approved_levels")
        if isinstance(levels, str | bytes | bytearray) or not isinstance(
            levels,
            Sequence,
        ):
            raise TypeError("registry approved_levels must be an array")
        self._approved_levels = frozenset(LocationKind(item) for item in levels)
        if not self._approved_levels:
            raise ValueError("registry approved_levels must not be empty")
        self._engine = engine
        self._registry_digest = str(registry["registry_digest"])
        self._report_digest = str(registry["qualification_report_digest"])

    @property
    def approved_levels(self) -> frozenset[LocationKind]:
        return self._approved_levels

    @property
    def registry_digest(self) -> str:
        return self._registry_digest

    def geolocate(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        city: str | None = None,
    ) -> GeolocationBatchResult:
        base = self._engine.geolocate(messages, city=city)
        results: list[MessageGeolocationResult] = []
        for item in base.results:
            qualified_level = (
                item.mention is None or item.mention.kind in self._approved_levels
            )
            included = item.included_for_analysis and qualified_level
            reasons = item.reasons
            if item.mention is not None and not qualified_level:
                reasons = (*reasons, "geolocation_level_not_qualified")
            provenance = dict(item.provenance)
            provenance["qualification"] = {
                "registry_digest": self._registry_digest,
                "report_digest": self._report_digest,
                "approved_levels": sorted(level.value for level in self._approved_levels),
            }
            results.append(
                MessageGeolocationResult(
                    message_key=item.message_key,
                    mention=item.mention,
                    candidates=item.candidates,
                    selected_candidate_id=item.selected_candidate_id,
                    confidence=item.confidence,
                    included_for_analysis=included,
                    reasons=reasons,
                    metric_crs=item.metric_crs,
                    provenance=provenance,
                )
            )
        resolved = sum(item.included_for_analysis for item in results)
        unresolved = sum(item.selected is None for item in results)
        low_confidence = sum(
            item.selected is not None and not item.included_for_analysis
            for item in results
        )
        stats = GeolocationStats(
            received=base.stats.received,
            processed=base.stats.processed,
            resolved=resolved,
            low_confidence=low_confidence,
            unresolved=unresolved,
            skipped=base.stats.skipped,
        )
        config_digest = digest_json(
            {
                "base_config_digest": base.config_digest,
                "qualification_registry_digest": self._registry_digest,
            }
        )
        output_core = {
            "algorithm_version": base.algorithm_version,
            "results": [item.to_dict() for item in results],
            "stats": stats.to_dict(),
            "input_digest": base.input_digest,
            "config_digest": config_digest,
        }
        return GeolocationBatchResult(
            results=tuple(results),
            stats=stats,
            input_digest=base.input_digest,
            output_digest=digest_json(output_core),
            config_digest=config_digest,
            algorithm_version=base.algorithm_version,
        )


def production_geolocation_engine(
    *,
    registry_path: Path,
    extractor: MentionExtractor,
    cache: SQLiteResponseCache,
    base_url: str,
    user_agent: str,
    retry_policy: HttpRetryPolicy | None = None,
) -> QualifiedGeolocationEngine:
    """Build the tested profile against a dedicated HTTPS Nominatim endpoint."""

    registry = load_qualified_registry(registry_path)
    provider_policy = _mapping(registry.get("provider_policy"), "registry.provider_policy")
    normalized_url = base_url.rstrip("/")
    if not normalized_url.startswith("https://"):
        raise ValueError("production Nominatim endpoint must use HTTPS")
    if (
        provider_policy.get("production_public_endpoint_allowed") is False
        and normalized_url == _PUBLIC_NOMINATIM
    ):
        raise ValueError("public Nominatim endpoint is forbidden for production")
    interval = float(provider_policy.get("minimum_interval_seconds", 1.0))
    transport = RequestsJsonTransport(
        user_agent=user_agent,
        policy=retry_policy,
        rate_limiter=RateLimiter(interval),
    )
    provider = SemanticNominatimClient(
        transport,
        cache,
        base_url=normalized_url,
    )
    runtime = _mapping(registry.get("runtime_config"), "registry.runtime_config")
    config = GeolocationConfig(
        min_confidence=float(runtime["min_confidence"]),
        max_candidates=int(runtime["max_candidates"]),
        country_codes=tuple(runtime["country_codes"]),
        language=str(runtime["language"]),
    )
    return QualifiedGeolocationEngine(
        GeolocationEngine(extractor, provider, config=config),
        registry,
    )


__all__ = ["QualifiedGeolocationEngine", "production_geolocation_engine"]
