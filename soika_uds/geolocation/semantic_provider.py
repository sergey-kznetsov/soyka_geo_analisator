"""Production Nominatim adapter with semantic address ranking."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from .models import (
    AddressMention,
    CandidateSource,
    GeocodingCandidate,
    GeoPoint,
    LocationKind,
)
from .nominatim_ranking import semantic_confidence, semantic_kind, structured_query
from .providers import NominatimClient


def _candidate_id(
    source: str,
    osm_type: object,
    osm_id: object,
    label: str,
) -> str:
    value = f"{source}:{osm_type}:{osm_id}:{label}".encode()
    return hashlib.sha256(value).hexdigest()[:24]


def _payload(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError("Nominatim response must be an array")
    return value


class SemanticNominatimClient(NominatimClient):
    """Use SOIKA semantic evidence instead of raw service rank alone."""

    @property
    def identity(self) -> Mapping[str, Any]:
        return {
            **super().identity,
            "ranking": "semantic-v1",
        }

    def search(
        self,
        mention: AddressMention,
        *,
        city: str | None,
        country_codes: Sequence[str],
        language: str,
        limit: int,
    ) -> Sequence[GeocodingCandidate]:
        if not isinstance(limit, int) or not 1 <= limit <= 40:
            raise ValueError("Nominatim limit must be in [1, 40]")
        params: dict[str, Any] = {
            "q": structured_query(mention, city),
            "format": "jsonv2",
            "addressdetails": 1,
            "limit": limit,
            "accept-language": language,
            "countrycodes": ",".join(country_codes),
            "dedupe": 1,
        }
        if mention.kind in {LocationKind.POI, LocationKind.LANDMARK}:
            params["layer"] = "poi,natural,manmade,address"
        else:
            params["layer"] = "address,poi"
        cache_key = self._cache.key("nominatim.semantic-v1", params)
        cached = self._cache.get(cache_key)
        if cached is None:
            fresh = self._transport.request_json(
                "GET",
                f"{self._base_url}/search",
                params=params,
            )
            payload = _payload(fresh)
            self._cache.set(cache_key, payload, ttl_seconds=self._ttl)
        else:
            payload = _payload(cached)
        candidates: list[GeocodingCandidate] = []
        for rank, raw in enumerate(payload[:limit]):
            if not isinstance(raw, Mapping):
                continue
            try:
                point = GeoPoint(
                    longitude=float(raw["lon"]),
                    latitude=float(raw["lat"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            label = str(raw.get("display_name") or mention.text).strip()
            osm_type = str(raw["osm_type"]) if raw.get("osm_type") else None
            osm_id_raw = raw.get("osm_id")
            osm_id = (
                int(osm_id_raw)
                if isinstance(osm_id_raw, int | str)
                and str(osm_id_raw).isdigit()
                else None
            )
            confidence, reasons = semantic_confidence(
                raw,
                mention,
                label,
                rank,
                limit,
            )
            candidates.append(
                GeocodingCandidate(
                    candidate_id=_candidate_id(
                        "nominatim-semantic-v1",
                        osm_type,
                        osm_id,
                        label,
                    ),
                    label=label,
                    kind=semantic_kind(raw, mention, label),
                    point=point,
                    confidence=confidence,
                    source=CandidateSource.NOMINATIM,
                    osm_type=osm_type,
                    osm_id=osm_id,
                    address=raw.get("address", {}),
                    reasons=reasons,
                )
            )
        return tuple(candidates)


__all__ = ["SemanticNominatimClient"]
