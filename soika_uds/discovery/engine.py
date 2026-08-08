"""Geo-first discovery engine: resolved territory first, source search second."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .classify import SourceClassifier, geo_evidence
from .models import (
    ACTIVE_SOURCE_KINDS,
    DiscoveryPlan,
    GeoScope,
    SourceCandidate,
    SourceKind,
    SourceOutcome,
    SourceReasonCode,
    SourceState,
)
from .places import PlaceEnricher
from .providers import SearchProvider, SearchProviderError
from .query import GeoQueryBuilder


def _provider_state(error: SearchProviderError) -> SourceState:
    if error.code in {
        SourceReasonCode.API_CREDENTIALS_MISSING,
        SourceReasonCode.SOURCE_CONFIGURATION_MISSING,
    }:
        return SourceState.CONFIGURATION_MISSING
    if error.code is SourceReasonCode.AUTH_REQUIRED:
        return SourceState.AUTH_REQUIRED
    return SourceState.UNAVAILABLE


def _enrich_scope(scope: GeoScope, enricher: PlaceEnricher) -> tuple[GeoScope, tuple[SourceOutcome, ...]]:
    result = enricher.enrich(scope)
    metadata = dict(scope.metadata)
    metadata["places"] = [item.to_dict() for item in result.places]
    metadata["place_names"] = list(dict.fromkeys(item.name for item in result.places))
    return replace(scope, metadata=metadata), result.outcomes


@dataclass(frozen=True, slots=True)
class DiscoveryEngine:
    provider: SearchProvider
    query_builder: GeoQueryBuilder = GeoQueryBuilder()
    classifier: SourceClassifier = SourceClassifier()
    place_enricher: PlaceEnricher | None = None
    results_per_query: int = 10
    max_candidates: int = 250

    def __post_init__(self) -> None:
        if not isinstance(self.results_per_query, int) or not (
            1 <= self.results_per_query <= 100
        ):
            raise ValueError("results_per_query must be in [1, 100]")
        if not isinstance(self.max_candidates, int) or not (
            10 <= self.max_candidates <= 2000
        ):
            raise ValueError("max_candidates must be in [10, 2000]")

    def plan(self, scope: GeoScope) -> DiscoveryPlan:
        outcomes: list[SourceOutcome] = []
        if self.place_enricher is not None:
            scope, enrichment_outcomes = _enrich_scope(scope, self.place_enricher)
            outcomes.extend(enrichment_outcomes)

        queries = self.query_builder.build(scope)
        candidates_by_url: dict[str, SourceCandidate] = {}
        provider_failed = False

        for query in queries:
            if len(candidates_by_url) >= self.max_candidates or provider_failed:
                break
            try:
                hits = self.provider.search(query.text, limit=self.results_per_query)
            except SearchProviderError as error:
                outcomes.append(
                    SourceOutcome(
                        source_id=f"search-provider:{self.provider.provider_id}",
                        kind=SourceKind.UNKNOWN,
                        state=_provider_state(error),
                        reason_code=error.code,
                        reason=str(error),
                        details={
                            "query": query.text,
                            "retryable": error.retryable,
                        },
                    )
                )
                if not error.retryable or error.code in {
                    SourceReasonCode.API_CREDENTIALS_MISSING,
                    SourceReasonCode.SOURCE_CONFIGURATION_MISSING,
                }:
                    provider_failed = True
                continue

            for hit in hits:
                if len(candidates_by_url) >= self.max_candidates:
                    break
                if hit.url in candidates_by_url:
                    continue
                kind = self.classifier.classify(hit)
                active = kind in ACTIVE_SOURCE_KINDS
                candidate = SourceCandidate.from_hit(
                    hit,
                    kind=kind,
                    active=active,
                    geo_evidence=geo_evidence(
                        hit,
                        city=scope.city,
                        region=scope.region,
                    ),
                )
                candidates_by_url[hit.url] = candidate
                if not active:
                    outcomes.append(
                        SourceOutcome(
                            source_id=candidate.candidate_id,
                            kind=kind,
                            state=SourceState.BLOCKED,
                            reason_code=SourceReasonCode.SOURCE_OUT_OF_SCOPE,
                            reason=(
                                f"{kind.value} is outside the active geo-first "
                                "collection perimeter"
                            ),
                            attempted_urls=(candidate.url,),
                            details={"query": candidate.query},
                        )
                    )

        return DiscoveryPlan(
            scope=scope,
            provider=self.provider.provider_id,
            queries=queries,
            candidates=tuple(candidates_by_url.values()),
            outcomes=tuple(outcomes),
        )


__all__ = ["DiscoveryEngine"]
