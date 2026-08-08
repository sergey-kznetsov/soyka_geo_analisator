"""Bounded POI enrichment for geo-first discovery.

OpenStreetMap contributes nearby named POIs through Overpass. 2GIS contributes
organization metadata and review statistics through its documented Places API.
Neither enricher treats ratings or review counters as review text messages.
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
_MAX_JSON_BYTES = 4_000_000


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _read_secret(*, direct_env: str, file_env: str) -> str | None:
    file_value = os.getenv(file_env)
    if file_value:
        try:
            value = Path(file_value).read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value or None
    value = os.getenv(direct_env)
    return value.strip() if value and value.strip() else None


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
        for field_name in ("provider", "external_id", "name"):
            value = _clean_text(getattr(self, field_name))
            if value is None:
                raise ValueError(f"{field_name} must not be empty")
            object.__setattr__(self, field_name, value)
        longitude = float(self.longitude)
        latitude = float(self.latitude)
        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
            raise ValueError("place coordinates are outside WGS84 bounds")
        object.__setattr__(self, "longitude", longitude)
        object.__setattr__(self, "latitude", latitude)
        object.__setattr__(self, "address", _clean_text(self.address))
        object.__setattr__(self, "website", _clean_text(self.website))
        object.__setattr__(self, "source_url", _clean_text(self.source_url))
        categories = tuple(
            dict.fromkeys(
                value
                for value in (_clean_text(item) for item in self.categories)
                if value is not None
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
        if not all(isinstance(item, PlaceRecord) for item in self.places):
            raise TypeError("places must contain PlaceRecord values")
        if not all(isinstance(item, SourceOutcome) for item in self.outcomes):
            raise TypeError("outcomes must contain SourceOutcome values")


class PlaceEnricher(Protocol):
    def enrich(self, scope: GeoScope) -> PlaceEnrichmentResult: ...


class JsonEndpointError(RuntimeError):
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


class JsonTransport(Protocol):
    def get_json(
        self,
        endpoint: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> tuple[int, Mapping[str, Any]]: ...

    def post_form_json(
        self,
        endpoint: str,
        *,
        form: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> tuple[int, Mapping[str, Any]]: ...


def _parse_json(body: bytes, *, source: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise JsonEndpointError(
            SourceReasonCode.PARSER_FAILED,
            f"{source} returned invalid JSON",
        ) from error
    if not isinstance(payload, Mapping):
        raise JsonEndpointError(
            SourceReasonCode.PARSER_FAILED,
            f"{source} returned a non-object JSON response",
        )
    return payload


@dataclass(frozen=True, slots=True)
class StdlibJsonTransport:
    """Fixed-endpoint HTTPS transport that never accepts arbitrary URLs."""

    def _request(
        self,
        endpoint: str,
        *,
        data: bytes | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
        source: str,
    ) -> tuple[int, Mapping[str, Any]]:
        if endpoint not in {_TWO_GIS_ENDPOINT, _OVERPASS_ENDPOINT}:
            raise JsonEndpointError(
                SourceReasonCode.SOURCE_CONFIGURATION_MISSING,
                "place transport refused an unexpected endpoint",
                state=SourceState.BLOCKED,
            )
        request = Request(
            endpoint,
            data=data,
            headers=dict(headers),
            method="POST" if data is not None else "GET",
        )
        try:
            with urlopen(
                request,
                timeout=timeout_seconds,
                context=ssl.create_default_context(),
            ) as response:
                body = response.read(_MAX_JSON_BYTES + 1)
                if len(body) > _MAX_JSON_BYTES:
                    raise JsonEndpointError(
                        SourceReasonCode.PARSER_FAILED,
                        f"{source} response exceeded the size limit",
                    )
                return response.status, _parse_json(body, source=source)
        except HTTPError as error:
            if error.code == 429:
                raise JsonEndpointError(
                    SourceReasonCode.HTTP_429,
                    f"{source} rate limit was reached",
                    retryable=True,
                ) from error
            if error.code == 403:
                raise JsonEndpointError(
                    SourceReasonCode.HTTP_403,
                    f"{source} returned HTTP 403",
                    state=SourceState.BLOCKED,
                ) from error
            if error.code in {401, 402}:
                raise JsonEndpointError(
                    SourceReasonCode.API_CREDENTIALS_MISSING,
                    f"{source} rejected credentials with HTTP {error.code}",
                    state=SourceState.CONFIGURATION_MISSING,
                ) from error
            raise JsonEndpointError(
                SourceReasonCode.PARSER_FAILED,
                f"{source} returned HTTP {error.code}",
                retryable=error.code >= 500,
            ) from error
        except TimeoutError as error:
            raise JsonEndpointError(
                SourceReasonCode.SOURCE_TIMEOUT,
                f"{source} request timed out",
                retryable=True,
            ) from error
        except URLError as error:
            if isinstance(error.reason, ssl.SSLError):
                code = SourceReasonCode.SSL_ERROR
            elif isinstance(error.reason, socket.gaierror):
                code = SourceReasonCode.DNS_ERROR
            else:
                code = SourceReasonCode.PARSER_FAILED
            raise JsonEndpointError(
                code,
                f"{source} network request failed",
                retryable=code is not SourceReasonCode.SSL_ERROR,
            ) from error

    def get_json(
        self,
        endpoint: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> tuple[int, Mapping[str, Any]]:
        if endpoint != _TWO_GIS_ENDPOINT:
            raise JsonEndpointError(
                SourceReasonCode.SOURCE_CONFIGURATION_MISSING,
                "GET place transport is restricted to the 2GIS endpoint",
                state=SourceState.BLOCKED,
            )
        query = urlencode(dict(params))
        return self._request(
            f"{endpoint}?{query}",
            data=None,
            headers=headers,
            timeout_seconds=timeout_seconds,
            source="2GIS Places API",
        )

    def post_form_json(
        self,
        endpoint: str,
        *,
        form: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> tuple[int, Mapping[str, Any]]:
        if endpoint != _OVERPASS_ENDPOINT:
            raise JsonEndpointError(
                SourceReasonCode.SOURCE_CONFIGURATION_MISSING,
                "POST place transport is restricted to the Overpass endpoint",
                state=SourceState.BLOCKED,
            )
        merged_headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            **dict(headers),
        }
        return self._request(
            endpoint,
            data=urlencode(dict(form)).encode("utf-8"),
            headers=merged_headers,
            timeout_seconds=timeout_seconds,
            source="OpenStreetMap Overpass API",
        )


def _outcome_from_error(source_id: str, kind: SourceKind, error: JsonEndpointError) -> SourceOutcome:
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
        lon = _optional_float(value.get("lon") or value.get("longitude"))
        lat = _optional_float(value.get("lat") or value.get("latitude"))
        return (lon, lat) if lon is not None and lat is not None else None
    if isinstance(value, str):
        left, separator, right = value.partition(",")
        if separator:
            lon = _optional_float(left.strip())
            lat = _optional_float(right.strip())
            return (lon, lat) if lon is not None and lat is not None else None
    return None


def _rubric_names(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        name = _clean_text(item.get("name"))
        if name:
            result.append(name)
    return tuple(dict.fromkeys(result))


@dataclass(frozen=True, slots=True)
class TwoGisPlacesEnricher:
    api_key: str | None
    transport: JsonTransport = StdlibJsonTransport()
    radius_meters: int = 300
    max_places: int = 10
    timeout_seconds: float = 15.0

    def __post_init__(self) -> None:
        if self.api_key is not None:
            value = _clean_text(self.api_key)
            object.__setattr__(self, "api_key", value)
        if not 50 <= self.radius_meters <= 2000:
            raise ValueError("radius_meters must be in [50, 2000]")
        if not 1 <= self.max_places <= 50:
            raise ValueError("max_places must be in [1, 50]")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    def enrich(self, scope: GeoScope) -> PlaceEnrichmentResult:
        if not self.api_key:
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
            "fields": (
                "items.point,items.full_address_name,items.rubrics,items.reviews"
            ),
        }
        try:
            status, payload = self.transport.get_json(
                _TWO_GIS_ENDPOINT,
                params=params,
                headers={"Accept": "application/json"},
                timeout_seconds=float(self.timeout_seconds),
            )
        except JsonEndpointError as error:
            return PlaceEnrichmentResult(
                outcomes=(_outcome_from_error("places:2gis", SourceKind.TWO_GIS, error),)
            )
        meta = payload.get("meta")
        if isinstance(meta, Mapping):
            code = _optional_int(meta.get("code"))
            if code is not None and code != 200:
                state = SourceState.CONFIGURATION_MISSING if code in {401, 402} else SourceState.UNAVAILABLE
                reason_code = (
                    SourceReasonCode.API_CREDENTIALS_MISSING
                    if code in {401, 402}
                    else SourceReasonCode.PARSER_FAILED
                )
                return PlaceEnrichmentResult(
                    outcomes=(
                        SourceOutcome(
                            source_id="places:2gis",
                            kind=SourceKind.TWO_GIS,
                            state=state,
                            reason_code=reason_code,
                            reason=f"2GIS Places API returned meta.code {code}",
                        ),
                    )
                )
        if status != 200:
            return PlaceEnrichmentResult(
                outcomes=(
                    SourceOutcome(
                        source_id="places:2gis",
                        kind=SourceKind.TWO_GIS,
                        state=SourceState.UNAVAILABLE,
                        reason_code=SourceReasonCode.PARSER_FAILED,
                        reason=f"2GIS Places API returned HTTP {status}",
                    ),
                )
            )
        result = payload.get("result")
        raw_items = result.get("items") if isinstance(result, Mapping) else None
        if not isinstance(raw_items, list):
            raw_items = []
        places: list[PlaceRecord] = []
        for item in raw_items[: self.max_places]:
            if not isinstance(item, Mapping):
                continue
            name = _clean_text(item.get("name"))
            external_id = _clean_text(item.get("id"))
            point = _point(item.get("point"))
            if not name or not external_id or point is None:
                continue
            reviews = item.get("reviews")
            rating = None
            review_count = None
            if isinstance(reviews, Mapping):
                rating = _optional_float(
                    reviews.get("general_rating") or reviews.get("rating")
                )
                review_count = _optional_int(reviews.get("review_count"))
            places.append(
                PlaceRecord(
                    provider="2gis",
                    external_id=external_id,
                    name=name,
                    longitude=point[0],
                    latitude=point[1],
                    address=(
                        _clean_text(item.get("full_address_name"))
                        or _clean_text(item.get("address_name"))
                    ),
                    categories=_rubric_names(item.get("rubrics")),
                    rating=rating,
                    review_count=review_count,
                )
            )
        state = SourceState.PARTIAL if places else SourceState.NO_RELEVANT_RESULTS
        reason_code = SourceReasonCode.NONE if places else SourceReasonCode.NO_RESULTS
        reason = (
            "2GIS Places API returned nearby organization metadata and review statistics; "
            "review texts are not available through the documented Places API"
            if places
            else "2GIS Places API returned no nearby organizations"
        )
        return PlaceEnrichmentResult(
            places=tuple(places),
            outcomes=(
                SourceOutcome(
                    source_id="places:2gis",
                    kind=SourceKind.TWO_GIS,
                    state=state,
                    reason_code=reason_code,
                    reason=reason,
                    details={
                        "places_found": len(places),
                        "radius_meters": self.radius_meters,
                        "review_texts_available": False,
                    },
                ),
            ),
        )


_OSM_CATEGORY_KEYS = (
    "amenity",
    "shop",
    "office",
    "tourism",
    "leisure",
    "healthcare",
    "craft",
)


def _osm_query(scope: GeoScope, radius_meters: int, max_places: int) -> str:
    selectors = "\n".join(
        f'nwr(around:{radius_meters},{scope.latitude:.7f},{scope.longitude:.7f})'
        f'["name"]["{key}"];'
        for key in _OSM_CATEGORY_KEYS
    )
    return (
        "[out:json][timeout:10];\n(\n"
        f"{selectors}\n"
        ");\n"
        f"out center {max_places};"
    )


def _osm_categories(tags: Mapping[str, Any]) -> tuple[str, ...]:
    result: list[str] = []
    for key in _OSM_CATEGORY_KEYS:
        value = _clean_text(tags.get(key))
        if value:
            result.append(f"{key}:{value}")
    return tuple(result)


def _osm_point(item: Mapping[str, Any]) -> tuple[float, float] | None:
    lon = _optional_float(item.get("lon"))
    lat = _optional_float(item.get("lat"))
    if lon is not None and lat is not None:
        return lon, lat
    center = item.get("center")
    return _point(center)


@dataclass(frozen=True, slots=True)
class OsmPoiEnricher:
    transport: JsonTransport = StdlibJsonTransport()
    radius_meters: int = 300
    max_places: int = 50
    timeout_seconds: float = 15.0
    user_agent: str = "SOIKA-UDS/0.20 geo-first-poi (OpenStreetMap attribution)"

    def __post_init__(self) -> None:
        if not 50 <= self.radius_meters <= 1000:
            raise ValueError("radius_meters must be in [50, 1000]")
        if not 1 <= self.max_places <= 100:
            raise ValueError("max_places must be in [1, 100]")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if _clean_text(self.user_agent) is None:
            raise ValueError("user_agent must not be empty")

    def enrich(self, scope: GeoScope) -> PlaceEnrichmentResult:
        try:
            status, payload = self.transport.post_form_json(
                _OVERPASS_ENDPOINT,
                form={"data": _osm_query(scope, self.radius_meters, self.max_places)},
                headers={
                    "Accept": "application/json",
                    "User-Agent": self.user_agent,
                },
                timeout_seconds=float(self.timeout_seconds),
            )
        except JsonEndpointError as error:
            return PlaceEnrichmentResult(
                outcomes=(
                    _outcome_from_error("places:osm", SourceKind.OSM_ENTITY, error),
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
                        reason=f"OpenStreetMap Overpass API returned HTTP {status}",
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
            name = _clean_text(tags.get("name"))
            osm_type = _clean_text(item.get("type"))
            osm_id = _optional_int(item.get("id"))
            point = _osm_point(item)
            if not name or not osm_type or osm_id is None or point is None:
                continue
            website = _clean_text(tags.get("website") or tags.get("contact:website"))
            address_parts = [
                _clean_text(tags.get("addr:street")),
                _clean_text(tags.get("addr:housenumber")),
            ]
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
                    website=website,
                    source_url=f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
                )
            )
        state = SourceState.PARTIAL if places else SourceState.NO_RELEVANT_RESULTS
        reason_code = SourceReasonCode.NONE if places else SourceReasonCode.NO_RESULTS
        reason = (
            "OpenStreetMap Overpass returned nearby named POIs for discovery enrichment"
            if places
            else "OpenStreetMap Overpass returned no nearby named POIs"
        )
        return PlaceEnrichmentResult(
            places=tuple(places),
            outcomes=(
                SourceOutcome(
                    source_id="places:osm",
                    kind=SourceKind.OSM_ENTITY,
                    state=state,
                    reason_code=reason_code,
                    reason=reason,
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
    """Build safe enrichment with optional 2GIS key and keyless bounded OSM."""

    return CompositePlaceEnricher(
        (
            OsmPoiEnricher(),
            TwoGisPlacesEnricher(
                _read_secret(
                    direct_env="TWO_GIS_API_KEY",
                    file_env="TWO_GIS_API_KEY_FILE",
                )
            ),
        )
    )


__all__ = [
    "CompositePlaceEnricher",
    "JsonEndpointError",
    "JsonTransport",
    "OsmPoiEnricher",
    "PlaceEnricher",
    "PlaceEnrichmentResult",
    "PlaceRecord",
    "StdlibJsonTransport",
    "TwoGisPlacesEnricher",
    "build_place_enricher_from_env",
]
