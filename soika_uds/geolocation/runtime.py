"""Deterministic geolocation runtime."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from .crs import metric_crs_for
from .extraction import MentionExtractor
from .models import (
    AddressMention,
    GeocodingCandidate,
    GeolocationBatchResult,
    GeolocationConfig,
    GeolocationStats,
    LocationKind,
    MessageGeolocationResult,
    digest_json,
)
from .providers import CandidateProvider, OverpassClient
from .transport import TransportError


class GeolocationProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


def _strip_city_context(mention: AddressMention, city: str | None) -> AddressMention:
    """Remove an explicitly known city from street comparison components.

    Free-form input can legitimately contain both city and street, for example
    ``Ижевск Пушкинская 277``. Nominatim should still receive the original
    phrase, while semantic street ranking must compare ``Пушкинская`` with the
    candidate road rather than ``Ижевск Пушкинская``.
    """

    if (
        not city
        or mention.kind not in {LocationKind.HOUSE, LocationKind.STREET}
        or not mention.street
    ):
        return mention
    cleaned_city = city.strip()
    if not cleaned_city:
        return mention
    escaped = re.escape(cleaned_city)
    street = mention.street.strip()
    leading = re.compile(
        rf"^(?:(?:г(?:ород)?\.?)\s*)?{escaped}\s*[,.;:\-]?\s*",
        re.I,
    )
    trailing = re.compile(
        rf"\s*[,.;:\-]?\s*(?:(?:г(?:ород)?\.?)\s*)?{escaped}$",
        re.I,
    )
    normalized_street = leading.sub("", street, count=1).strip(" ,.;:-")
    normalized_street = trailing.sub("", normalized_street, count=1).strip(
        " ,.;:-"
    )
    if not normalized_street or normalized_street == street:
        return mention
    normalized = normalized_street.casefold().replace("ё", "е")
    if mention.kind is LocationKind.HOUSE and mention.house_number:
        normalized = f"{normalized}, {mention.house_number.casefold()}"
    return replace(mention, street=normalized_street, normalized=normalized)


def _select_candidate(
    mention: AddressMention,
    candidates: Sequence[GeocodingCandidate],
) -> GeocodingCandidate:
    """Prefer a candidate that preserves the requested address precision."""

    if mention.kind is LocationKind.HOUSE:
        exact_house = next(
            (item for item in candidates if item.kind is LocationKind.HOUSE),
            None,
        )
        if exact_house is not None:
            return exact_house
    return candidates[0]


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

    def _search(
        self,
        mention,
        *,
        city: str | None,
    ) -> tuple[GeocodingCandidate, ...]:
        try:
            primary = tuple(
                self._provider.search(
                    mention,
                    city=city,
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
        *,
        city: str | None = None,
    ) -> GeolocationBatchResult:
        effective_city = city.strip() if isinstance(city, str) and city.strip() else None
        effective_city = effective_city or self._config.default_city
        effective_config = {
            **self._config.to_dict(),
            "effective_city": effective_city,
        }
        effective_config_digest = digest_json(effective_config)
        normalized_messages = tuple(sorted(messages, key=self._message_key))
        input_payload = {
            "city": effective_city,
            "messages": [dict(message) for message in normalized_messages],
        }
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
            "effective_city": effective_city,
            "config_digest": effective_config_digest,
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
            mention = _strip_city_context(mention, effective_city)
            candidates = self._search(mention, city=effective_city)
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
            selected = _select_candidate(mention, candidates)
            confidence = round(
                mention.confidence * selected.confidence,
                6,
            )
            included = confidence >= self._config.min_confidence
            reasons: tuple[str, ...]
            if mention.kind is LocationKind.HOUSE and selected.kind is not LocationKind.HOUSE:
                included = False
                reasons = ("house_candidate_not_resolved",)
            elif included:
                reasons = ()
            else:
                reasons = ("geolocation_below_threshold",)
            if included:
                resolved += 1
            else:
                low_confidence += 1
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
            "config_digest": effective_config_digest,
        }
        output_digest = digest_json(output_core)
        return GeolocationBatchResult(
            results=tuple(results),
            stats=stats,
            input_digest=input_digest,
            output_digest=output_digest,
            config_digest=effective_config_digest,
        )
