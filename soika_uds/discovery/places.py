"""Bounded place enrichment for geo-first source discovery.

OpenStreetMap supplies nearby named POIs through Overpass. 2GIS supplies
organization metadata and review statistics through the documented Places API.
Review statistics are never emitted as review text messages.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import GeoScope, SourceKind, SourceOutcome, SourceReasonCode, SourceState

_TWO_GIS_ENDPOINT = "https://catalog.api.2gis.com/3.0/items"
_OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"
_MAX_RESPONSE_BYTES = 4_000_000


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _secret(*, direct_env: str, file_env: str) -> str | None:
    path = os.getenv(file_env)
    if path:
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value or None
    value = os.getenv(direct_env)
    return value.strip() if value and value.strip() else None


def _json(body: bytes, source: str) -> Mapping[str, Any]:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PlaceApiError(
            SourceReasonCode.PARSER_FAILED,
            f"{source} returned invalid JSON",
        ) from error
    if not isinstance(value, Mapping):
        raise PlaceApiError(
            SourceReasonCode.PARSER_FAILED,
            f"{source} returned non-object JSON",
        )
    return value


class PlaceApiError(RuntimeError):
    def __init__(
        self,
        code: SourceReasonCode,
        message: str,
        *,
        state: SourceState = SourceState.UNAVAILABLE,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.state = state
        self.retryable = retryable


def _network_error(source: str, error: BaseException) -> PlaceApiError:
    if isinstance(error, HTTPError):
        if error.code == 429:
            return PlaceApiError(
                SourceReasonCode.HTTP_429,
                f"{source} rate limit was reached",
                retryable=True,
            )
        if error.code == 403:
            return PlaceApiError(
                SourceReasonCode.HTTP_403,
                f"{source} returned HTTP 403",
                state=SourceState.BLOCKED,
            )
        if error.code in {401, 402}:
            return PlaceApiError(
                SourceReasonCode.API_CREDENTIALS_MISSING,
                f"{source} rejected credentials with HTTP {error.code}",
                state=SourceState.CONFIGURATION_MISSING,
            )
        return PlaceApiError(
            SourceReasonCode.PARSER_FAILED,
            f"{source} returned HTTP {error.code}",
            retryable=error.code >= 500,
        )
    if isinstance(error, TimeoutError):
        return PlaceApiError(
            SourceReasonCode.SOURCE_TIMEOUT,
            f"{source} request timed out",
            retryable=True,
        )
    if isinstance(error, URLError):
        if isinstance(error.reason, ssl.SSLError):
            return PlaceApiError(
                SourceReasonCode.SSL_ERROR,
                f"{source} TLS validation failed",
            )
        if isinstance(error.reason, socket.gaierror):
            return PlaceApiError(
                SourceReasonCode.DNS_ERROR,
                f"{source} DNS resolution failed",
                retryable=True,
            )
    return PlaceApiError(
        SourceReasonCode.PARSER_FAILED,
        f"{source} network request failed",
        retryable=True,
    )


class TwoGisTransport(Protocol):
    def fetch(
        self,
        params: Mapping[str, str],
        *,
        timeout_seconds: float,
    ) -> tuple[int, Mapping[str, Any]]: ...


class OverpassTransport(Protocol):
    def query(
        self,
        query: str,
        *,
        user_agent: str,
        timeout_seconds: float,
    ) -> tuple[int, Mapping[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class StdlibTwoGisTransport:
    def fetch(
        self,
        params: Mapping[str, str],
        *,
        timeout_seconds: float,
    ) -> tuple[int, Mapping[str, Any]]:
        url = f"{_TWO_GIS_ENDPOINT}?{urlencode(dict(params))}"
        request = Request(
            url,
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with urlopen(
                request,
                timeout=timeout_seconds,
                context=ssl.create_default_context(),
            ) as response:
                body = response.read(_MAX_RESPONSE_BYTES + 1)
                if len(body) > _MAX_RESPONSE_BYTES:
                    raise PlaceApiError(
                        SourceReasonCode.PARSER_FAILED,
                        "2GIS Places API response exceeded size limit",
                    )
                return response.status, _json(body, "2GIS Places API")
        except (HTTPError, URLError, TimeoutError) as error:
            raise _network_error("2GIS Places API", error) from error


@dataclass(frozen=True, slots=True)
class StdlibOverpassTransport:
    def query(
        self,
        query: str,
        *,
        user_agent: str,
        timeout_seconds: float,
    ) -> tuple[int, Mapping[str, Any]]:
        request = Request(
            _OVERPASS_ENDPOINT,
            data=urlencode({"data": query}).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                "User-Agent": user_agent,
            },
            method="POST",
        )
        try:
            with urlopen(
                request,
                timeout=timeout_seconds,
                context=ssl.create_default_context(),
            ) as response:
                body = response.read(_MAX_RESPONSE_BYTES + 1)
                if len(body) > _MAX_RESPONSE_BYTES:
                    raise PlaceApiError(
                        SourceReasonCode.PARSER_FAILED,
                        "OpenStreetMap Overpass response exceeded size limit",
                    )
                return response.status, _json(body, "OpenStreetMap Overpass API")
        except (HTTPError, URLError, TimeoutError) as error:
            raise _network_error("OpenStreetMap Overpass API", error) from error


@dataclass(frozen=True, slots=True)
class PlaceRecord:
    provider: str
    external_id: str
    name: str
    longitude: float
    latitude: float
    address: str | None = None
    categories: tuple[str, ...] = ()
    website: str | None = None
    rating: float | None = None
    review_count: int | None = None
    source_url: str | None = None

    def __post_init__(self) -> None:
        for name in ("provider", "external_id", "name"):
            value = _text(getattr(self, name))
            if value is None:
                raise ValueError(f"{name} must not be empty")
            object.__setattr__(self, name, value)
        longitude = float(self.longitude)
        latitude = float(self.latitude)
        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
            raise ValueError("place coordinates are outside WGS84 bounds")
        object.__setattr__(self, "longitude", longitude)
        object.__setattr__(self, "latitude", latitude)
        object.__setattr__(self, "address", _text(self.address))
        object.__setattr__(self, "website", _text(self.website))
        object.__setattr__(self, "source_url", _text(self.source_url))
        categories = tuple(
            dict.fromkeys(
                cleaned
                for cleaned in (_text(item) for item in self.categories)
                if cleaned is not None
            )
        )
        object.__setattr__(self, "categories", categories)
        if self.rating is not None:
            rating = float(self.rating)
            if not 0 <= rating <= 5:
                raise ValueError("rating must be in [0, 5]")
            object.__setattr__(self, "rating", rating)
        if self.review_count is not None and self.review_count < 0:
            raise ValueError("review_count must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "external_id": self.external_id,
            "name": self.name,
            "point": {"longitude": self.longitude, "latitude": self.latitude},
            "address": self.address,
            "categories": list(self.categories),
            "website": self.website,
            "rating": self.rating,
            "review_count": self.review_count,
            "source_url": self.source_url,
        }


@dataclass(frozen=True, slots=True)
class PlaceEnrichmentResult:
    places: tuple[PlaceRecord, ...] = ()
    outcomes: tuple[SourceOutcome, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "places", tuple(self.places))
        object.__setattr__(self, "outcomes", tuple(self.outcomes))


class PlaceEnricher(Protocol):
    def enrich(self, scope: GeoScope) -> PlaceEnrichmentResult: ...


def _error_outcome(source_id: str, kind: SourceKind, error: PlaceApiError) -> SourceOutcome:
    return SourceOutcome(
        source_id=source_id,
        kind=kind,
        state=error.state,
        reason_code=error.code,
        reason=str(error),
        details={"retryable": error.retryable},
    )


def _point(value: object) -> tuple[float, float] | None:
    if isinstance(value, Mapping):
        lon = _float(value.get("lon") or value.get("longitude"))
        lat = _float(value.get("lat") or value.get("latitude"))
        if lon is not None and lat is not None:
            return lon, lat
    if isinstance(value, str):
        left, sep, right = value.partition(",")
        if sep:
            lon = _float(left.strip())
            lat = _float(right.strip())
            if lon is not None and lat is not None:
                return lon, lat
    return None


def _rubrics(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    names: list[str] = []
    for item in value:
        if isinstance(item, Mapping):
            name = _text(item.get("name"))
            if name:
                names.append(name)
    return tuple(dict.fromkeys(names))


@dataclass(frozen=True, slots=True)
class TwoGisPlacesEnricher:
    api_key: str | None
    transport: TwoGisTransport = StdlibTwoGisTransport()
    radius_meters: int = 300
    max_places: int = 10
    timeout_seconds: float = 15.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "api_key", _text(self.api_key))
        if not 50 <= self.radius_meters <= 2000:
            raise ValueError("radius_meters must be in [50, 2000]")
        if not 1 <= self.max_places <= 50:
            raise ValueError("max_places must be in [1, 50]")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    def enrich(self, scope: GeoScope) -> PlaceEnrichmentResult:
        if self.api_key is None:
            return PlaceEnrichmentResult(
                outcomes=(
                    SourceOutcome(
                        source_id="places:2gis",
                        kind=SourceKind.TWO_GIS,
                        state=SourceState.CONFIGURATION_MISSING,
                        reason_code=SourceReasonCode.API_CREDENTIALS_MISSING,
                        reason="2GIS Places API key is not configured",
                    ),
                )
            )
        params = {
            "key": self.api_key,
            "type": "branch",
            "point": f"{scope.longitude:.7f},{scope.latitude:.7f}",
            "radius": str(self.radius_meters),
            "sort": "distance",
            "page_size": str(self.max_places),
            "locale": "ru_RU",
            "fields": "items.point,items.full_address_name,items.rubrics,items.reviews",
        }
        try:
            status, payload = self.transport.fetch(
                params,
                timeout_seconds=float(self.timeout_seconds),
            )
        except PlaceApiError as error:
            return PlaceEnrichmentResult(
                outcomes=(_error_outcome("places:2gis", SourceKind.TWO_GIS, error),)
            )
        meta = payload.get("meta")
        meta_code = _int(meta.get("code")) if isinstance(meta, Mapping) else None
        if status != 200 or (meta_code is not None and meta_code != 200):
            code = meta_code if meta_code is not None else status
            credential_error = code in {401, 402, 403}
            return PlaceEnrichmentResult(
                outcomes=(
                    SourceOutcome(
                        source_id="places:2gis",
                        kind=SourceKind.TWO_GIS,
                        state=(
                            SourceState.CONFIGURATION_MISSING
                            if credential_error
                            else SourceState.UNAVAILABLE
                        ),
                        reason_code=(
                            SourceReasonCode.API_CREDENTIALS_MISSING
                            if credential_error
                            else SourceReasonCode.PARSER_FAILED
                        ),
                        reason=f"2GIS Places API returned status {code}",
                    ),
                )
            )
        result = payload.get("result")
        raw_items = result.get("items") if isinstance(result, Mapping) else []
        if not isinstance(raw_items, list):
            raw_items = []
        places: list[PlaceRecord] = []
        for item in raw_items[: self.max_places]:
            if not isinstance(item, Mapping):
                continue
            name = _text(item.get("name"))
            external_id = _text(item.get("id"))
            point = _point(item.get("point"))
            if not name or not external_id or point is None:
                continue
            reviews = item.get("reviews")
            rating = None
            review_count = None
            if isinstance(reviews, Mapping):
                rating = _float(reviews.get("general_rating") or reviews.get("rating"))
                review_count = _int(reviews.get("review_count"))
            places.append(
                PlaceRecord(
                    provider="2gis",
                    external_id=external_id,
                    name=name,
                    longitude=point[0],
                    latitude=point[1],
                    address=(
                        _text(item.get("full_address_name"))
                        or _text(item.get("address_name"))
                    ),
                    categories=_rubrics(item.get("rubrics")),
                    rating=rating,
                    review_count=review_count,
                )
            )
        return PlaceEnrichmentResult(
            places=tuple(places),
            outcomes=(
                SourceOutcome(
                    source_id="places:2gis",
                    kind=SourceKind.TWO_GIS,
                    state=(
                        SourceState.PARTIAL
                        if places
                        else SourceState.NO_RELEVANT_RESULTS
                    ),
                    reason_code=(
                        SourceReasonCode.NONE if places else SourceReasonCode.NO_RESULTS
                    ),
                    reason=(
                        "2GIS returned nearby organizations and review statistics; "
                        "documented Places API does not provide review texts"
                        if places
                        else "2GIS Places API returned no nearby organizations"
                    ),
                    details={
                        "places_found": len(places),
                        "radius_meters": self.radius_meters,
                        "review_texts_available": False,
                    },
                ),
            ),
        )


_OSM_KEYS = ("amenity", "shop", "office", "tourism", "leisure", "healthcare", "craft")


def _overpass_query(scope: GeoScope, radius_meters: int, max_places: int) -> str:
    selectors = "\n".join(
        f'nwr(around:{radius_meters},{scope.latitude:.7f},{scope.longitude:.7f})'
        f'["name"]["{key}"];'
        for key in _OSM_KEYS
    )
    return (
        "[out:json][timeout:10];\n(\n"
        f"{selectors}\n"
        ");\n"
        f"out center {max_places};"
    )


def _osm_point(item: Mapping[str, Any]) -> tuple[float, float] | None:
    direct = _point({"lon": item.get("lon"), "lat": item.get("lat")})
    if direct is not None:
        return direct
    return _point(item.get("center"))


def _osm_categories(tags: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in _OSM_KEYS:
        value = _text(tags.get(key))
        if value:
            values.append(f"{key}:{value}")
    return tuple(values)


@dataclass(frozen=True, slots=True)
class OsmPoiEnricher:
    transport: OverpassTransport = StdlibOverpassTransport()
    radius_meters: int = 300
    max_places: int = 50
    timeout_seconds: float = 15.0
    user_agent: str = "SOIKA-UDS/0.20 geo-first-poi"

    def __post_init__(self) -> None:
        if not 50 <= self.radius_meters <= 1000:
            raise ValueError("radius_meters must be in [50, 1000]")
        if not 1 <= self.max_places <= 100:
            raise ValueError("max_places must be in [1, 100]")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if _text(self.user_agent) is None:
            raise ValueError("user_agent must not be empty")

    def enrich(self, scope: GeoScope) -> PlaceEnrichmentResult:
        try:
            status, payload = self.transport.query(
                _overpass_query(scope, self.radius_meters, self.max_places),
                user_agent=self.user_agent,
                timeout_seconds=float(self.timeout_seconds),
            )
        except PlaceApiError as error:
            return PlaceEnrichmentResult(
                outcomes=(
                    _error_outcome("places:osm", SourceKind.OSM_ENTITY, error),
                )
            )
        if status != 200:
            return PlaceEnrichmentResult(
                outcomes=(
                    SourceOutcome(
                        source_id="places:osm",
                        kind=SourceKind.OSM_ENTITY,
                        state=SourceState.UNAVAILABLE,
                        reason_code=SourceReasonCode.PARSER_FAILED,
                        reason=f"OpenStreetMap Overpass returned HTTP {status}",
                    ),
                )
            )
        raw_items = payload.get("elements")
        if not isinstance(raw_items, list):
            raw_items = []
        places: list[PlaceRecord] = []
        for item in raw_items[: self.max_places]:
            if not isinstance(item, Mapping):
                continue
            tags = item.get("tags")
            if not isinstance(tags, Mapping):
                continue
            name = _text(tags.get("name"))
            osm_type = _text(item.get("type"))
            osm_id = _int(item.get("id"))
            point = _osm_point(item)
            if not name or not osm_type or osm_id is None or point is None:
                continue
            address_parts = (
                _text(tags.get("addr:street")),
                _text(tags.get("addr:housenumber")),
            )
            address = " ".join(value for value in address_parts if value) or None
            places.append(
                PlaceRecord(
                    provider="openstreetmap",
                    external_id=f"{osm_type}/{osm_id}",
                    name=name,
                    longitude=point[0],
                    latitude=point[1],
                    address=address,
                    categories=_osm_categories(tags),
                    website=_text(tags.get("website") or tags.get("contact:website")),
                    source_url=f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
                )
            )
        return PlaceEnrichmentResult(
            places=tuple(places),
            outcomes=(
                SourceOutcome(
                    source_id="places:osm",
                    kind=SourceKind.OSM_ENTITY,
                    state=(
                        SourceState.PARTIAL
                        if places
                        else SourceState.NO_RELEVANT_RESULTS
                    ),
                    reason_code=(
                        SourceReasonCode.NONE if places else SourceReasonCode.NO_RESULTS
                    ),
                    reason=(
                        "OpenStreetMap Overpass returned nearby named POIs"
                        if places
                        else "OpenStreetMap Overpass returned no nearby named POIs"
                    ),
                    details={
                        "places_found": len(places),
                        "radius_meters": self.radius_meters,
                        "license": "ODbL",
                        "attribution": "© OpenStreetMap contributors",
                    },
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class CompositePlaceEnricher:
    enrichers: tuple[PlaceEnricher, ...]
    max_places: int = 80

    def __post_init__(self) -> None:
        object.__setattr__(self, "enrichers", tuple(self.enrichers))
        if not 1 <= self.max_places <= 500:
            raise ValueError("max_places must be in [1, 500]")

    def enrich(self, scope: GeoScope) -> PlaceEnrichmentResult:
        places: list[PlaceRecord] = []
        outcomes: list[SourceOutcome] = []
        seen: set[tuple[str, str]] = set()
        for enricher in self.enrichers:
            result = enricher.enrich(scope)
            outcomes.extend(result.outcomes)
            for place in result.places:
                key = (place.name.casefold(), (place.address or "").casefold())
                if key in seen:
                    continue
                seen.add(key)
                places.append(place)
                if len(places) >= self.max_places:
                    break
            if len(places) >= self.max_places:
                break
        return PlaceEnrichmentResult(tuple(places), tuple(outcomes))


def build_place_enricher_from_env() -> CompositePlaceEnricher:
    return CompositePlaceEnricher(
        (
            OsmPoiEnricher(),
            TwoGisPlacesEnricher(
                _secret(
                    direct_env="TWO_GIS_API_KEY",
                    file_env="TWO_GIS_API_KEY_FILE",
                )
            ),
        )
    )


__all__ = [
    "CompositePlaceEnricher",
    "OsmPoiEnricher",
    "OverpassTransport",
    "PlaceApiError",
    "PlaceEnricher",
    "PlaceEnrichmentResult",
    "PlaceRecord",
    "StdlibOverpassTransport",
    "StdlibTwoGisTransport",
    "TwoGisPlacesEnricher",
    "TwoGisTransport",
    "build_place_enricher_from_env",
]
