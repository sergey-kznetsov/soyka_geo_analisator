"""Nominatim and Overpass provider adapters."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from .cache import SQLiteResponseCache
from .models import (
    AddressMention,
    CandidateSource,
    GeocodingCandidate,
    GeoPoint,
    LocationKind,
)
from .transport import JsonTransport


class CandidateProvider(Protocol):
    @property
    def identity(self) -> Mapping[str, Any]: ...

    def search(
        self,
        mention: AddressMention,
        *,
        city: str | None,
        country_codes: Sequence[str],
        language: str,
        limit: int,
    ) -> Sequence[GeocodingCandidate]: ...


def _candidate_id(
    source: str,
    osm_type: object,
    osm_id: object,
    label: str,
) -> str:
    value = f"{source}:{osm_type}:{osm_id}:{label}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:24]


def _kind_from_nominatim(
    payload: Mapping[str, Any],
    fallback: LocationKind,
) -> LocationKind:
    value = str(payload.get("addresstype") or payload.get("type") or "").casefold()
    category = str(payload.get("category") or "").casefold()
    if value in {"house", "building"}:
        return LocationKind.HOUSE
    if value in {"road", "street", "pedestrian"}:
        return LocationKind.STREET
    if value in {"suburb", "district", "borough", "neighbourhood", "quarter"}:
        return LocationKind.DISTRICT
    if category in {"amenity", "shop", "tourism", "railway"}:
        return LocationKind.POI
    if category in {"historic", "natural", "man_made", "bridge"}:
        return LocationKind.LANDMARK
    return fallback


def _nominatim_confidence(
    payload: Mapping[str, Any],
    rank: int,
    limit: int,
) -> float:
    raw = payload.get("importance", 0.0)
    importance = 0.0
    if not isinstance(raw, bool) and isinstance(raw, int | float):
        importance = max(0.0, min(1.0, float(raw)))
    order_component = 1.0 - (rank / max(1, limit))
    return round(
        max(0.05, min(0.99, 0.55 * importance + 0.45 * order_component)),
        6,
    )


class NominatimClient:
    def __init__(
        self,
        transport: JsonTransport,
        cache: SQLiteResponseCache,
        *,
        base_url: str = "https://nominatim.openstreetmap.org",
        ttl_seconds: int = 30 * 24 * 60 * 60,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("Nominatim base URL must use HTTPS")
        self._transport = transport
        self._cache = cache
        self._base_url = base_url.rstrip("/")
        self._ttl = ttl_seconds

    @property
    def identity(self) -> Mapping[str, Any]:
        return {
            "type": "nominatim",
            "base_url": self._base_url,
            "format": "jsonv2",
        }

    @staticmethod
    def _query(mention: AddressMention, city: str | None) -> str:
        parts = [mention.text]
        if city and city.casefold() not in mention.text.casefold():
            parts.append(city)
        return ", ".join(parts)

    def search(
        self,
        mention: AddressMention,
        *,
        city: str | None,
        country_codes: Sequence[str],
        language: str,
        limit: int,
    ) -> Sequence[GeocodingCandidate]:
        params: dict[str, Any] = {
            "q": self._query(mention, city),
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
        cache_key = self._cache.key("nominatim.search", params)
        payload = self._cache.get(cache_key)
        if payload is None:
            payload = self._transport.request_json(
                "GET",
                f"{self._base_url}/search",
                params=params,
            )
            self._cache.set(cache_key, payload, ttl_seconds=self._ttl)
        if not isinstance(payload, list):
            raise ValueError("Nominatim response must be an array")
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
            candidates.append(
                GeocodingCandidate(
                    candidate_id=_candidate_id(
                        "nominatim",
                        osm_type,
                        osm_id,
                        label,
                    ),
                    label=label,
                    kind=_kind_from_nominatim(raw, mention.kind),
                    point=point,
                    confidence=_nominatim_confidence(raw, rank, limit),
                    source=CandidateSource.NOMINATIM,
                    osm_type=osm_type,
                    osm_id=osm_id,
                    address=raw.get("address", {}),
                    reasons=("ranked_nominatim_match",),
                )
            )
        return tuple(candidates)


class OverpassClient:
    """Read-only nearby OSM fallback for POIs and landmarks."""

    def __init__(
        self,
        transport: JsonTransport,
        cache: SQLiteResponseCache,
        *,
        base_url: str = "https://overpass-api.de/api/interpreter",
        timeout_seconds: int = 25,
        ttl_seconds: int = 7 * 24 * 60 * 60,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("Overpass base URL must use HTTPS")
        if not 1 <= timeout_seconds <= 180:
            raise ValueError("Overpass timeout must be in [1, 180]")
        self._transport = transport
        self._cache = cache
        self._base_url = base_url
        self._timeout = timeout_seconds
        self._ttl = ttl_seconds

    @property
    def identity(self) -> Mapping[str, Any]:
        return {
            "type": "overpass",
            "base_url": self._base_url,
            "timeout_seconds": self._timeout,
        }

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def nearby(
        self,
        mention: AddressMention,
        *,
        center: GeoPoint,
        radius_m: int = 1000,
        limit: int = 5,
    ) -> Sequence[GeocodingCandidate]:
        if mention.kind not in {
            LocationKind.POI,
            LocationKind.LANDMARK,
            LocationKind.DISTRICT,
        }:
            return ()
        name = self._escape(mention.poi or mention.district or mention.text)
        query = (
            f"[out:json][timeout:{self._timeout}];"
            f"nwr[\"name\"~\"^{name}$\",i](around:{radius_m},"
            f"{center.latitude},{center.longitude});out center tags {limit};"
        )
        parameters = {"data": query}
        cache_key = self._cache.key("overpass.nearby", parameters)
        payload = self._cache.get(cache_key)
        if payload is None:
            payload = self._transport.request_json(
                "POST",
                self._base_url,
                data=parameters,
            )
            self._cache.set(cache_key, payload, ttl_seconds=self._ttl)
        if not isinstance(payload, Mapping) or not isinstance(
            payload.get("elements"),
            list,
        ):
            raise ValueError("Overpass response must contain elements")
        candidates: list[GeocodingCandidate] = []
        for raw in payload["elements"][:limit]:
            if not isinstance(raw, Mapping):
                continue
            center_raw = (
                raw.get("center")
                if isinstance(raw.get("center"), Mapping)
                else raw
            )
            try:
                point = GeoPoint(
                    longitude=float(center_raw["lon"]),
                    latitude=float(center_raw["lat"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            tags = (
                raw.get("tags")
                if isinstance(raw.get("tags"), Mapping)
                else {}
            )
            label = str(tags.get("name") or mention.text)
            osm_type = str(raw.get("type") or "") or None
            osm_id = raw.get("id") if isinstance(raw.get("id"), int) else None
            distance = math.hypot(
                point.longitude - center.longitude,
                point.latitude - center.latitude,
            )
            confidence = round(
                max(0.2, 0.62 - min(0.35, distance * 20.0)),
                6,
            )
            candidates.append(
                GeocodingCandidate(
                    candidate_id=_candidate_id(
                        "overpass",
                        osm_type,
                        osm_id,
                        label,
                    ),
                    label=label,
                    kind=mention.kind,
                    point=point,
                    confidence=confidence,
                    source=CandidateSource.OVERPASS,
                    osm_type=osm_type,
                    osm_id=osm_id,
                    address=tags,
                    reasons=("nearby_overpass_name_match",),
                )
            )
        return tuple(candidates)
