"""Contracts for geo-first discovery of Russian public sources."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty")
    return cleaned


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be finite")
    return result


def _json_value(value: object, field_name: str) -> Any:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        return _number(value, field_name)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field_name} keys must be strings")
            result[key] = _json_value(item, f"{field_name}.{key}")
        return result
    if isinstance(value, Sequence) and not isinstance(
        value,
        str | bytes | bytearray,
    ):
        return [
            _json_value(item, f"{field_name}[{index}]")
            for index, item in enumerate(value)
        ]
    raise ValueError(f"{field_name} contains unsupported {type(value).__name__}")


def _freeze_mapping(
    value: Mapping[str, Any] | None,
    field_name: str,
) -> Mapping[str, Any]:
    return MappingProxyType(_json_value(value or {}, field_name))


def canonical_url(value: str) -> str:
    """Normalize a discovered URL for deterministic deduplication."""

    url = _required_text(value, "url")
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("url must be an absolute HTTP(S) URL")
    host = parsed.hostname.encode("idna").decode("ascii").lower()
    port = parsed.port
    netloc = host
    default_port = (
        parsed.scheme.lower() == "http" and port == 80
        or parsed.scheme.lower() == "https" and port == 443
    )
    if port is not None and not default_port:
        netloc = f"{host}:{port}"
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))


class SourceKind(str, Enum):
    LOCAL_MEDIA = "local_media"
    MUNICIPAL = "municipal"
    LOCAL_FORUM = "local_forum"
    TELEGRAM = "telegram"
    PIKABU = "pikabu"
    DZEN = "dzen"
    YANDEX_MAPS = "yandex_maps"
    TWO_GIS = "two_gis"
    ORGANIZATION_SITE = "organization_site"
    OTHER_WEB = "other_web"
    OSM_ENTITY = "osm_entity"
    VK = "vk"
    OK = "ok"
    RUTUBE = "rutube"
    MAX = "max"
    UNKNOWN = "unknown"


ACTIVE_SOURCE_KINDS = frozenset(
    {
        SourceKind.LOCAL_MEDIA,
        SourceKind.MUNICIPAL,
        SourceKind.LOCAL_FORUM,
        SourceKind.PIKABU,
        SourceKind.DZEN,
        SourceKind.YANDEX_MAPS,
        SourceKind.TWO_GIS,
        SourceKind.ORGANIZATION_SITE,
        SourceKind.OTHER_WEB,
    }
)


class SourceState(str, Enum):
    DISCOVERED = "discovered"
    COLLECTED = "collected"
    PARTIAL = "partial"
    NO_RELEVANT_RESULTS = "no_relevant_results"
    UNAVAILABLE = "unavailable"
    BLOCKED = "blocked"
    AUTH_REQUIRED = "auth_required"
    CONFIGURATION_MISSING = "configuration_missing"
    FAILED = "failed"


class SourceReasonCode(str, Enum):
    NONE = "NONE"
    HTTP_403 = "HTTP_403"
    HTTP_429 = "HTTP_429"
    CAPTCHA = "CAPTCHA"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    API_CREDENTIALS_MISSING = "API_CREDENTIALS_MISSING"
    ROBOTS_DENIED = "ROBOTS_DENIED"
    SOURCE_TIMEOUT = "SOURCE_TIMEOUT"
    DNS_ERROR = "DNS_ERROR"
    SSL_ERROR = "SSL_ERROR"
    ANTI_BOT = "ANTI_BOT"
    PARSER_FAILED = "PARSER_FAILED"
    NO_RELEVANT_CONTENT = "NO_RELEVANT_CONTENT"
    NO_RESULTS = "NO_RESULTS"
    UNSUPPORTED_PAGE = "UNSUPPORTED_PAGE"
    SOURCE_CONFIGURATION_MISSING = "SOURCE_CONFIGURATION_MISSING"
    SEARCH_PROVIDER_UNAVAILABLE = "SEARCH_PROVIDER_UNAVAILABLE"
    SOURCE_OUT_OF_SCOPE = "SOURCE_OUT_OF_SCOPE"
    TERMS_RESTRICTED = "TERMS_RESTRICTED"
    TERRITORY_UNRESOLVED = "TERRITORY_UNRESOLVED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class GeoScope:
    raw_address: str
    city: str
    region: str | None
    district: str | None
    street: str | None
    house_number: str | None
    longitude: float
    latitude: float
    precision: str
    confidence: float
    candidate_id: str
    label: str
    osm_type: str | None = None
    osm_id: int | None = None
    aliases: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        required_fields = (
            "raw_address",
            "city",
            "precision",
            "candidate_id",
            "label",
        )
        for name in required_fields:
            value = _required_text(getattr(self, name), name)
            object.__setattr__(self, name, value)
        optional_fields = ("region", "district", "street", "house_number", "osm_type")
        for name in optional_fields:
            value = _optional_text(getattr(self, name), name)
            object.__setattr__(self, name, value)

        longitude = _number(self.longitude, "longitude")
        latitude = _number(self.latitude, "latitude")
        confidence = _number(self.confidence, "confidence")
        if not -180 <= longitude <= 180:
            raise ValueError("longitude must be in [-180, 180]")
        if not -90 <= latitude <= 90:
            raise ValueError("latitude must be in [-90, 90]")
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be in [0, 1]")
        object.__setattr__(self, "longitude", longitude)
        object.__setattr__(self, "latitude", latitude)
        object.__setattr__(self, "confidence", confidence)

        if self.osm_id is not None and (
            not isinstance(self.osm_id, int) or self.osm_id < 1
        ):
            raise ValueError("osm_id must be a positive integer")
        aliases = tuple(
            dict.fromkeys(
                _required_text(item, "aliases[]") for item in self.aliases
            )
        )
        object.__setattr__(self, "aliases", aliases)
        object.__setattr__(
            self,
            "metadata",
            _freeze_mapping(self.metadata, "metadata"),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "raw_address": self.raw_address,
            "city": self.city,
            "region": self.region,
            "district": self.district,
            "street": self.street,
            "house_number": self.house_number,
            "point": {
                "longitude": self.longitude,
                "latitude": self.latitude,
            },
            "precision": self.precision,
            "confidence": self.confidence,
            "candidate_id": self.candidate_id,
            "label": self.label,
            "aliases": list(self.aliases),
            "metadata": dict(self.metadata),
        }
        if self.osm_type is not None:
            payload["osm_type"] = self.osm_type
        if self.osm_id is not None:
            payload["osm_id"] = self.osm_id
        return payload


@dataclass(frozen=True, slots=True)
class DiscoveryQuery:
    text: str
    purpose: str
    target_kind: SourceKind | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "text",
            _required_text(self.text, "query.text"),
        )
        object.__setattr__(
            self,
            "purpose",
            _required_text(self.purpose, "query.purpose"),
        )
        if self.target_kind is not None and not isinstance(
            self.target_kind,
            SourceKind,
        ):
            object.__setattr__(
                self,
                "target_kind",
                SourceKind(self.target_kind),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "purpose": self.purpose,
            "target_kind": (
                self.target_kind.value if self.target_kind else None
            ),
        }


@dataclass(frozen=True, slots=True)
class SearchHit:
    query: str
    title: str
    url: str
    snippet: str = ""
    rank: int = 0
    provider: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "query",
            _required_text(self.query, "hit.query"),
        )
        object.__setattr__(
            self,
            "title",
            _required_text(self.title, "hit.title"),
        )
        object.__setattr__(self, "url", canonical_url(self.url))
        if not isinstance(self.snippet, str):
            raise ValueError("hit.snippet must be a string")
        object.__setattr__(self, "snippet", self.snippet.strip())
        if not isinstance(self.rank, int) or self.rank < 0:
            raise ValueError("hit.rank must be non-negative")
        object.__setattr__(
            self,
            "provider",
            _required_text(self.provider, "hit.provider"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "rank": self.rank,
            "provider": self.provider,
        }


@dataclass(frozen=True, slots=True)
class SourceCandidate:
    candidate_id: str
    kind: SourceKind
    url: str
    domain: str
    title: str
    discovered_by: str
    query: str
    geo_evidence: tuple[str, ...] = ()
    active: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        candidate_id = _required_text(self.candidate_id, "candidate_id")
        object.__setattr__(self, "candidate_id", candidate_id)
        if not isinstance(self.kind, SourceKind):
            object.__setattr__(self, "kind", SourceKind(self.kind))

        url = canonical_url(self.url)
        object.__setattr__(self, "url", url)
        host = urlsplit(url).hostname or ""
        expected_domain = host.encode("idna").decode("ascii").lower()
        domain = _required_text(self.domain, "domain").lower()
        if domain != expected_domain:
            raise ValueError("candidate domain must equal URL hostname")
        object.__setattr__(self, "domain", domain)

        for name in ("title", "discovered_by", "query"):
            value = _required_text(getattr(self, name), name)
            object.__setattr__(self, name, value)
        evidence = tuple(
            dict.fromkeys(
                _required_text(item, "geo_evidence[]")
                for item in self.geo_evidence
            )
        )
        object.__setattr__(self, "geo_evidence", evidence)
        if not isinstance(self.active, bool):
            raise ValueError("candidate.active must be boolean")
        object.__setattr__(
            self,
            "metadata",
            _freeze_mapping(self.metadata, "metadata"),
        )

    @classmethod
    def from_hit(
        cls,
        hit: SearchHit,
        *,
        kind: SourceKind,
        active: bool,
        geo_evidence: Sequence[str] = (),
    ) -> SourceCandidate:
        digest = hashlib.sha256(hit.url.encode("utf-8")).hexdigest()[:24]
        domain = urlsplit(hit.url).hostname or ""
        return cls(
            candidate_id=f"web:{digest}",
            kind=kind,
            url=hit.url,
            domain=domain,
            title=hit.title,
            discovered_by=hit.provider,
            query=hit.query,
            geo_evidence=tuple(geo_evidence),
            active=active,
            metadata={"snippet": hit.snippet, "rank": hit.rank},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "kind": self.kind.value,
            "url": self.url,
            "domain": self.domain,
            "title": self.title,
            "discovered_by": self.discovered_by,
            "query": self.query,
            "geo_evidence": list(self.geo_evidence),
            "active": self.active,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class SourceOutcome:
    source_id: str
    kind: SourceKind
    state: SourceState
    reason_code: SourceReasonCode
    reason: str
    attempted_urls: tuple[str, ...] = ()
    messages_collected: int = 0
    relevant_messages: int = 0
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        source_id = _required_text(self.source_id, "source_id")
        object.__setattr__(self, "source_id", source_id)
        if not isinstance(self.kind, SourceKind):
            object.__setattr__(self, "kind", SourceKind(self.kind))
        if not isinstance(self.state, SourceState):
            object.__setattr__(self, "state", SourceState(self.state))
        if not isinstance(self.reason_code, SourceReasonCode):
            object.__setattr__(
                self,
                "reason_code",
                SourceReasonCode(self.reason_code),
            )
        object.__setattr__(
            self,
            "reason",
            _required_text(self.reason, "reason"),
        )
        urls = tuple(canonical_url(item) for item in self.attempted_urls)
        object.__setattr__(
            self,
            "attempted_urls",
            tuple(dict.fromkeys(urls)),
        )
        for name in ("messages_collected", "relevant_messages"):
            value = getattr(self, name)
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.relevant_messages > self.messages_collected:
            raise ValueError(
                "relevant_messages cannot exceed messages_collected"
            )
        object.__setattr__(
            self,
            "details",
            _freeze_mapping(self.details, "details"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "kind": self.kind.value,
            "state": self.state.value,
            "reason_code": self.reason_code.value,
            "reason": self.reason,
            "attempted_urls": list(self.attempted_urls),
            "messages_collected": self.messages_collected,
            "relevant_messages": self.relevant_messages,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class DiscoveryPlan:
    scope: GeoScope
    provider: str
    queries: tuple[DiscoveryQuery, ...]
    candidates: tuple[SourceCandidate, ...]
    outcomes: tuple[SourceOutcome, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.scope, GeoScope):
            raise ValueError("scope must be GeoScope")
        object.__setattr__(
            self,
            "provider",
            _required_text(self.provider, "provider"),
        )
        object.__setattr__(self, "queries", tuple(self.queries))
        object.__setattr__(self, "candidates", tuple(self.candidates))
        object.__setattr__(self, "outcomes", tuple(self.outcomes))
        ids = [item.candidate_id for item in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate ids must be unique")

    @property
    def active_candidates(self) -> tuple[SourceCandidate, ...]:
        return tuple(item for item in self.candidates if item.active)

    def to_dict(self) -> dict[str, Any]:
        active_count = len(self.active_candidates)
        candidate_count = len(self.candidates)
        return {
            "scope": self.scope.to_dict(),
            "provider": self.provider,
            "active_source_kinds": sorted(
                item.value for item in ACTIVE_SOURCE_KINDS
            ),
            "queries": [item.to_dict() for item in self.queries],
            "candidates": [item.to_dict() for item in self.candidates],
            "outcomes": [item.to_dict() for item in self.outcomes],
            "stats": {
                "queries": len(self.queries),
                "candidates": candidate_count,
                "active_candidates": active_count,
                "excluded_candidates": candidate_count - active_count,
            },
        }
