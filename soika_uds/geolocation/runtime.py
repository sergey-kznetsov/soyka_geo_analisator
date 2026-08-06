"""Deterministic geolocation runtime."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .crs import metric_crs_for
from .extraction import MentionExtractor
from .models import (
    ALGORITHM_VERSION,
    GeocodingCandidate,
    GeolocationBatchResult,
    GeolocationConfig,
    GeolocationStats,
    MessageGeolocationResult,
    digest_json,
)
from .providers import CandidateProvider, OverpassClient
from .transport import TransportError


class GeolocationProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class GeolocationEngine:
    def __init__(
        self,
        extractor: MentionExtractor,
        provider: CandidateProvider,
        *,
        overpass: OverpassClient | None = None,
        config: GeolocationConfig | None = None,
    ) -> None:
        self._extractor = extractor
        self._provider = provider
        self._overpass = overpass
        self._config = config or GeolocationConfig()

    @staticmethod
    def _message_key(message: Mapping[str, Any]) -> str:
        value = message.get("message_key")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("message_key must be a non-empty string")
        return value.strip()

    @staticmethod
    def _model_text(message: Mapping[str, Any]) -> str:
        value = message.get("model_text")
        if not isinstance(value, str):
            raise ValueError("model_text must be a string")
        return value

    @staticmethod
    def _eligible(
        message: Mapping[str, Any],
        include_unclassified: bool,
    ) -> bool:
        if include_unclassified:
            return True
        return message.get("included_for_analysis", True) is True

    def _search(self, mention) -> tuple[GeocodingCandidate, ...]:
        try:
            primary = tuple(
                self._provider.search(
                    mention,
                    city=self._config.default_city,
                    country_codes=self._config.country_codes,
                    language=self._config.language,
                    limit=self._config.max_candidates,
                )
            )
            candidates = list(primary)
            if (
                self._overpass is not None
                and primary
                and mention.kind.value in {"poi", "landmark", "district"}
            ):
                candidates.extend(
                    self._overpass.nearby(
                        mention,
                        center=primary[0].point,
                        limit=self._config.max_candidates,
                    )
                )
        except TransportError as error:
            raise GeolocationProviderError(
                str(error),
                retryable=error.retryable,
            ) from error
        except (TypeError, ValueError) as error:
            raise GeolocationProviderError(
                str(error),
                retryable=False,
            ) from error
        unique: dict[str, GeocodingCandidate] = {}
        for candidate in candidates:
            previous = unique.get(candidate.candidate_id)
            if previous is None or candidate.confidence > previous.confidence:
                unique[candidate.candidate_id] = candidate
        ordered = sorted(
            unique.values(),
            key=lambda item: (-item.confidence, item.candidate_id),
        )
        return tuple(ordered[: self._config.max_candidates])

    def geolocate(
        self,
        messages: Sequence[Mapping[str, Any]],
    ) -> GeolocationBatchResult:
        normalized_messages = tuple(sorted(messages, key=self._message_key))
        input_payload = [dict(message) for message in normalized_messages]
        input_digest = digest_json(input_payload)
        results: list[MessageGeolocationResult] = []
        skipped = 0
        resolved = 0
        low_confidence = 0
        unresolved = 0
        provenance = {
            "algorithm_version": ALGORITHM_VERSION,
            "extractor": dict(self._extractor.identity),
            "provider": dict(self._provider.identity),
            "overpass": (
                dict(self._overpass.identity) if self._overpass else None
            ),
            "config_digest": self._config.digest,
        }
        for message in normalized_messages:
            key = self._message_key(message)
            if not self._eligible(
                message,
                self._config.include_unclassified,
            ):
                skipped += 1
                continue
            text = self._model_text(message)
            mention = self._extractor.extract(text)
            if mention is None:
                unresolved += 1
                results.append(
                    MessageGeolocationResult(
                        message_key=key,
                        mention=None,
                        candidates=(),
                        selected_candidate_id=None,
                        confidence=0.0,
                        included_for_analysis=False,
                        reasons=("location_mention_not_found",),
                        metric_crs=None,
                        provenance=provenance,
                    )
                )
                continue
            candidates = self._search(mention)
            if not candidates:
                unresolved += 1
                results.append(
                    MessageGeolocationResult(
                        message_key=key,
                        mention=mention,
                        candidates=(),
                        selected_candidate_id=None,
                        confidence=0.0,
                        included_for_analysis=False,
                        reasons=("geocoding_candidate_not_found",),
                        metric_crs=None,
                        provenance=provenance,
                    )
                )
                continue
            selected = candidates[0]
            confidence = round(
                mention.confidence * selected.confidence,
                6,
            )
            included = confidence >= self._config.min_confidence
            reasons: tuple[str, ...]
            if included:
                resolved += 1
                reasons = ()
            else:
                low_confidence += 1
                reasons = ("geolocation_below_threshold",)
            results.append(
                MessageGeolocationResult(
                    message_key=key,
                    mention=mention,
                    candidates=candidates,
                    selected_candidate_id=selected.candidate_id,
                    confidence=confidence,
                    included_for_analysis=included,
                    reasons=reasons,
                    metric_crs=metric_crs_for(selected.point),
                    provenance=provenance,
                )
            )
        processed = len(normalized_messages) - skipped
        stats = GeolocationStats(
            received=len(normalized_messages),
            processed=processed,
            resolved=resolved,
            low_confidence=low_confidence,
            unresolved=unresolved,
            skipped=skipped,
        )
        output_core = {
            "algorithm_version": ALGORITHM_VERSION,
            "results": [result.to_dict() for result in results],
            "stats": stats.to_dict(),
            "input_digest": input_digest,
            "config_digest": self._config.digest,
        }
        output_digest = digest_json(output_core)
        return GeolocationBatchResult(
            results=tuple(results),
            stats=stats,
            input_digest=input_digest,
            output_digest=output_digest,
            config_digest=self._config.digest,
        )
