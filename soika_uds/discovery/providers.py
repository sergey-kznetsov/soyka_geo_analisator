"""Search-provider boundary with Yandex Search API as the RU-first implementation."""

from __future__ import annotations

import base64
import json
import socket
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from defusedxml.ElementTree import fromstring as safe_xml_fromstring

from .models import SearchHit, SourceReasonCode

_YANDEX_SEARCH_ENDPOINT = "https://searchapi.api.cloud.yandex.net/v2/web/search"
_MAX_SEARCH_RESPONSE_BYTES = 8_000_000


class SearchProviderError(RuntimeError):
    def __init__(
        self,
        code: SourceReasonCode,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class SearchProvider(Protocol):
    provider_id: str

    def search(self, query: str, *, limit: int = 10) -> tuple[SearchHit, ...]: ...


class JsonPostTransport(Protocol):
    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> tuple[int, Mapping[str, Any]]: ...


def _json_object(body: bytes, *, required: bool) -> Mapping[str, Any]:
    try:
        value = json.loads(body.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        if not required:
            return {}
        raise SearchProviderError(
            SourceReasonCode.PARSER_FAILED,
            "Yandex Search API returned invalid JSON",
        ) from error
    if not isinstance(value, Mapping):
        if not required:
            return {}
        raise SearchProviderError(
            SourceReasonCode.PARSER_FAILED,
            "Yandex Search API returned a non-object JSON response",
        )
    return value


@dataclass(frozen=True, slots=True)
class StdlibJsonPostTransport:
    """HTTPS-only stdlib transport dedicated to the fixed Yandex API endpoint."""

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> tuple[int, Mapping[str, Any]]:
        if url != _YANDEX_SEARCH_ENDPOINT:
            raise SearchProviderError(
                SourceReasonCode.SOURCE_CONFIGURATION_MISSING,
                "Yandex Search transport refused an unexpected endpoint",
            )
        request = Request(
            url,
            data=json.dumps(dict(payload), ensure_ascii=False).encode(),
            headers=dict(headers),
            method="POST",
        )
        context = ssl.create_default_context()
        try:
            with urlopen(
                request,
                timeout=timeout_seconds,
                context=context,
            ) as response:
                body = response.read(_MAX_SEARCH_RESPONSE_BYTES + 1)
                if len(body) > _MAX_SEARCH_RESPONSE_BYTES:
                    raise SearchProviderError(
                        SourceReasonCode.PARSER_FAILED,
                        "Yandex Search API response exceeded the size limit",
                    )
                return response.status, _json_object(body, required=True)
        except HTTPError as error:
            body = error.read(_MAX_SEARCH_RESPONSE_BYTES)
            return error.code, _json_object(body, required=False)
        except TimeoutError as error:
            raise SearchProviderError(
                SourceReasonCode.SOURCE_TIMEOUT,
                "Yandex Search API request timed out",
                retryable=True,
            ) from error
        except URLError as error:
            if isinstance(error.reason, ssl.SSLError):
                raise SearchProviderError(
                    SourceReasonCode.SSL_ERROR,
                    "Yandex Search API TLS validation failed",
                ) from error
            if isinstance(error.reason, socket.gaierror):
                raise SearchProviderError(
                    SourceReasonCode.DNS_ERROR,
                    "Yandex Search API DNS resolution failed",
                    retryable=True,
                ) from error
            raise SearchProviderError(
                SourceReasonCode.SEARCH_PROVIDER_UNAVAILABLE,
                "Yandex Search API connection failed",
                retryable=True,
            ) from error
        except ssl.SSLError as error:
            raise SearchProviderError(
                SourceReasonCode.SSL_ERROR,
                "Yandex Search API TLS validation failed",
            ) from error


def _xml_text(element: Any) -> str:
    return " ".join(" ".join(element.itertext()).split())


def _first_descendant_text(element: Any, local_name: str) -> str | None:
    for child in element.iter():
        if child.tag.rsplit("}", 1)[-1].lower() == local_name:
            value = _xml_text(child)
            if value:
                return value
    return None


def _passages(element: Any) -> str:
    values: list[str] = []
    for child in element.iter():
        if child.tag.rsplit("}", 1)[-1].lower() == "passage":
            value = _xml_text(child)
            if value:
                values.append(value)
    return " ".join(values)


def parse_yandex_xml(
    raw_xml: bytes,
    *,
    query: str,
    provider: str,
) -> tuple[SearchHit, ...]:
    """Parse only document fields from Yandex XML; fields are optional."""

    try:
        root = safe_xml_fromstring(raw_xml)
    except Exception as error:  # defusedxml exposes multiple parser exception types
        raise SearchProviderError(
            SourceReasonCode.PARSER_FAILED,
            "Yandex Search XML response could not be parsed safely",
        ) from error
    hits: list[SearchHit] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1].lower() != "doc":
            continue
        url = _first_descendant_text(element, "url")
        title = _first_descendant_text(element, "title")
        if not url or not title:
            continue
        if not url.startswith(("http://", "https://")):
            continue
        try:
            hit = SearchHit(
                query=query,
                title=title,
                url=url,
                snippet=_passages(element),
                rank=len(hits),
                provider=provider,
            )
        except ValueError:
            continue
        hits.append(hit)
    return tuple(hits)


@dataclass(frozen=True, slots=True)
class YandexSearchProvider:
    """Synchronous Yandex Search API v2 provider for the Russian search index."""

    folder_id: str
    api_key: str
    transport: JsonPostTransport = StdlibJsonPostTransport()
    timeout_seconds: float = 20.0
    region_id: str | None = None
    provider_id: str = "yandex-search-api-v2-ru"

    def __post_init__(self) -> None:
        for name in ("folder_id", "api_key"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise SearchProviderError(
                    SourceReasonCode.API_CREDENTIALS_MISSING,
                    f"Yandex Search {name} is not configured",
                )
            object.__setattr__(self, name, value.strip())
        if not isinstance(self.timeout_seconds, int | float) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.region_id is not None:
            region = self.region_id.strip()
            object.__setattr__(self, "region_id", region or None)

    def search(self, query: str, *, limit: int = 10) -> tuple[SearchHit, ...]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must not be empty")
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be in [1, 100]")
        payload: dict[str, Any] = {
            "query": {
                "searchType": "SEARCH_TYPE_RU",
                "queryText": query.strip(),
                "familyMode": "FAMILY_MODE_MODERATE",
                "page": "0",
                "fixTypoMode": "FIX_TYPO_MODE_ON",
            },
            "groupSpec": {
                "groupMode": "GROUP_MODE_FLAT",
                "groupsOnPage": str(limit),
                "docsInGroup": "1",
            },
            "maxPassages": "2",
            "l10N": "LOCALIZATION_RU",
            "folderId": self.folder_id,
            "responseFormat": "FORMAT_XML",
            "userAgent": "SOIKA-UDS geo-first discovery",
        }
        if self.region_id is not None:
            payload["region"] = self.region_id
        status, response = self.transport.post_json(
            _YANDEX_SEARCH_ENDPOINT,
            headers={
                "Authorization": f"Api-Key {self.api_key}",
                "Content-Type": "application/json",
            },
            payload=payload,
            timeout_seconds=float(self.timeout_seconds),
        )
        if status in {401, 403}:
            raise SearchProviderError(
                SourceReasonCode.API_CREDENTIALS_MISSING,
                f"Yandex Search API rejected credentials with HTTP {status}",
            )
        if status == 429:
            raise SearchProviderError(
                SourceReasonCode.HTTP_429,
                "Yandex Search API rate limit was reached",
                retryable=True,
            )
        if status >= 500:
            raise SearchProviderError(
                SourceReasonCode.SEARCH_PROVIDER_UNAVAILABLE,
                f"Yandex Search API returned HTTP {status}",
                retryable=True,
            )
        if not 200 <= status <= 299:
            raise SearchProviderError(
                SourceReasonCode.SEARCH_PROVIDER_UNAVAILABLE,
                f"Yandex Search API returned HTTP {status}",
            )
        encoded = response.get("rawData")
        if not isinstance(encoded, str) or not encoded:
            raise SearchProviderError(
                SourceReasonCode.PARSER_FAILED,
                "Yandex Search API response did not contain rawData",
            )
        try:
            raw_xml = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as error:
            raise SearchProviderError(
                SourceReasonCode.PARSER_FAILED,
                "Yandex Search API rawData is not valid Base64",
            ) from error
        return parse_yandex_xml(
            raw_xml,
            query=query.strip(),
            provider=self.provider_id,
        )[:limit]


@dataclass(frozen=True, slots=True)
class UnavailableSearchProvider:
    reason: str = "Yandex Search API credentials are not configured"
    provider_id: str = "yandex-search-api-v2-ru"

    def search(self, query: str, *, limit: int = 10) -> tuple[SearchHit, ...]:
        del query, limit
        raise SearchProviderError(
            SourceReasonCode.API_CREDENTIALS_MISSING,
            self.reason,
        )


__all__ = [
    "JsonPostTransport",
    "SearchProvider",
    "SearchProviderError",
    "StdlibJsonPostTransport",
    "UnavailableSearchProvider",
    "YandexSearchProvider",
    "parse_yandex_xml",
]
