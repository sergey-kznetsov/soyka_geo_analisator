"Allowlisted HTTP transport used by parser adapters."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol
from urllib.parse import urljoin

from .models import PermanentParserError, SourcePolicy, TemporaryParserError
from .security import (
    Resolver,
    validate_content_type,
    validate_outbound_url,
    validate_response_size,
)


@dataclass(frozen=True, slots=True)
class TransportRequest:
    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float = 20.0
    max_response_bytes: int = 5_000_000

    def __post_init__(self) -> None:
        method = self.method.strip().upper()
        if method not in {"GET", "HEAD"}:
            raise ValueError("parser transport permits only GET and HEAD")
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "url", self.url.strip())
        normalized_headers: dict[str, str] = {}
        for key, value in self.headers.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError("transport headers must be strings")
            header_name = key.strip()
            header_value = value.strip()
            if not header_name or "\n" in header_name or "\r" in header_name:
                raise ValueError("invalid transport header name")
            if "\n" in header_value or "\r" in header_value:
                raise ValueError("invalid transport header value")
            normalized_headers[header_name] = header_value
        object.__setattr__(
            self,
            "headers",
            MappingProxyType(normalized_headers),
        )


@dataclass(frozen=True, slots=True)
class TransportResponse:
    status_code: int
    url: str
    headers: Mapping[str, str]
    body: bytes

    def __post_init__(self) -> None:
        if type(self.status_code) is not int or not 100 <= self.status_code <= 599:
            raise ValueError("status_code must be an HTTP status")
        if not isinstance(self.body, bytes):
            raise TypeError("transport response body must be bytes")
        object.__setattr__(
            self,
            "headers",
            MappingProxyType(
                {
                    str(key).lower(): str(value)
                    for key, value in self.headers.items()
                }
            ),
        )

    @property
    def content_type(self) -> str | None:
        return self.headers.get("content-type")

    @property
    def content_length(self) -> int | None:
        raw = self.headers.get("content-length")
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError as error:
            raise PermanentParserError(
                "upstream sent invalid Content-Length",
                code="INVALID_CONTENT_LENGTH",
            ) from error

    def json_value(self) -> Any:
        try:
            decoded = self.body.decode("utf-8")
        except UnicodeDecodeError as error:
            raise PermanentParserError(
                "upstream JSON is not UTF-8",
                code="INVALID_JSON_ENCODING",
            ) from error
        try:
            return json.loads(decoded)
        except json.JSONDecodeError as error:
            raise PermanentParserError(
                "upstream returned invalid JSON",
                code="INVALID_JSON_RESPONSE",
            ) from error


class LowLevelHttpClient(Protocol):
    def send(self, request: TransportRequest) -> TransportResponse:
        ...


class CredentialProvider(Protocol):
    def headers_for(
        self,
        credential_reference: str,
        source_id: str,
    ) -> Mapping[str, str]:
        ...


class NullCredentialProvider:
    def headers_for(
        self,
        credential_reference: str,
        source_id: str,
    ) -> Mapping[str, str]:
        del credential_reference, source_id
        raise PermanentParserError(
            "source credential is not configured",
            code="CREDENTIAL_UNAVAILABLE",
        )


class SafeHttpTransport:
    """Validate domains, IPs, redirects, types, and sizes for every request."""

    _REDIRECTS = {301, 302, 303, 307, 308}

    def __init__(
        self,
        policy: SourcePolicy,
        client: LowLevelHttpClient,
        *,
        credential_provider: CredentialProvider | None = None,
        resolver: Resolver,
    ) -> None:
        self.policy = policy
        self.client = client
        self.credential_provider = credential_provider or NullCredentialProvider()
        self.resolver = resolver

    def _headers(
        self,
        extra_headers: Mapping[str, str] | None,
    ) -> dict[str, str]:
        headers = {"User-Agent": self.policy.security.user_agent}
        credential_reference = self.policy.security.credential_reference
        if credential_reference is not None:
            headers.update(
                self.credential_provider.headers_for(
                    credential_reference,
                    self.policy.source_id,
                )
            )
        for key, value in (extra_headers or {}).items():
            if key.lower() in {
                "authorization",
                "cookie",
                "proxy-authorization",
                "host",
            }:
                raise PermanentParserError(
                    f"adapter cannot set protected header {key!r}",
                    code="PROTECTED_HEADER",
                )
            headers[key] = value
        return headers

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> TransportResponse:
        current_url = url
        request_headers = self._headers(headers)
        for redirect_count in range(self.policy.security.max_redirects + 1):
            safe_url = validate_outbound_url(
                current_url,
                self.policy.security,
                resolver=self.resolver,
            )
            response = self.client.send(
                TransportRequest(
                    method="GET",
                    url=safe_url,
                    headers=request_headers,
                    timeout_seconds=self.policy.rate_limit.timeout_seconds,
                    max_response_bytes=self.policy.security.max_response_bytes,
                )
            )
            if response.status_code in self._REDIRECTS:
                if redirect_count >= self.policy.security.max_redirects:
                    raise PermanentParserError(
                        "upstream redirect limit exceeded",
                        code="REDIRECT_LIMIT_EXCEEDED",
                    )
                location = response.headers.get("location")
                if not location:
                    raise PermanentParserError(
                        "upstream redirect has no Location header",
                        code="INVALID_REDIRECT",
                    )
                current_url = urljoin(safe_url, location)
                continue

            validate_response_size(
                response.content_length,
                len(response.body),
                self.policy.security,
            )
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                raise TemporaryParserError(
                    f"upstream returned HTTP {response.status_code}",
                    code="UPSTREAM_TEMPORARY_HTTP_ERROR",
                    details={"status_code": response.status_code},
                )
            if not 200 <= response.status_code <= 299:
                raise PermanentParserError(
                    f"upstream returned HTTP {response.status_code}",
                    code="UPSTREAM_HTTP_ERROR",
                    details={"status_code": response.status_code},
                )
            validate_content_type(
                response.content_type,
                self.policy.security,
            )
            return response
        raise RuntimeError("unreachable redirect state")


class UnavailableTransport:
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> TransportResponse:
        del url, headers
        raise PermanentParserError(
            "network transport is not configured for this worker",
            code="TRANSPORT_UNAVAILABLE",
        )


@dataclass(frozen=True, slots=True)
class ParserServices:
    transport: SafeHttpTransport | UnavailableTransport
