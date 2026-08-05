"Network and privacy controls for untrusted parser sources."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import socket
from collections.abc import Callable, Iterable, Mapping
from urllib.parse import urlsplit

from .models import (
    AuthorIdentifierMode,
    SecurityPolicy,
    SourcePolicy,
    SourcePolicyError,
)


class UnsafeOutboundRequestError(SourcePolicyError):
    """Raised before a parser can access an unsafe target."""


Resolver = Callable[[str], Iterable[str]]


def _default_resolver(hostname: str) -> tuple[str, ...]:
    results = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    return tuple(sorted({item[4][0] for item in results}))


def _domain_allowed(hostname: str, policy: SecurityPolicy) -> bool:
    host = hostname.lower().rstrip(".")
    for allowed in policy.allowed_domains:
        if host == allowed:
            return True
        if policy.allow_subdomains and host.endswith(f".{allowed}"):
            return True
    return False


def _ip_is_public(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def validate_outbound_url(
    url: str,
    policy: SecurityPolicy,
    *,
    resolver: Resolver = _default_resolver,
) -> str:
    """Validate every initial and redirected URL before a network request."""

    if not isinstance(url, str) or not url.strip():
        raise UnsafeOutboundRequestError("outbound URL must not be empty")
    parsed = urlsplit(url.strip())
    allowed_schemes = {"https"} if policy.https_only else {"http", "https"}
    if parsed.scheme.lower() not in allowed_schemes:
        raise UnsafeOutboundRequestError("outbound URL scheme is not allowed")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeOutboundRequestError("userinfo in outbound URL is forbidden")
    if parsed.hostname is None:
        raise UnsafeOutboundRequestError("outbound URL requires a hostname")
    if parsed.port not in {None, 80, 443}:
        raise UnsafeOutboundRequestError("outbound URL uses a forbidden port")
    if not _domain_allowed(parsed.hostname, policy):
        raise UnsafeOutboundRequestError("outbound URL hostname is not allowlisted")

    if policy.block_private_networks:
        try:
            addresses = tuple(resolver(parsed.hostname))
        except OSError as error:
            raise UnsafeOutboundRequestError(
                "outbound hostname could not be resolved safely"
            ) from error
        if not addresses:
            raise UnsafeOutboundRequestError(
                "outbound hostname resolved to no addresses"
            )
        unsafe = [address for address in addresses if not _ip_is_public(address)]
        if unsafe:
            raise UnsafeOutboundRequestError(
                "outbound hostname resolves to a non-public address"
            )
    return parsed.geturl()


def validate_content_type(content_type: str | None, policy: SecurityPolicy) -> str:
    if content_type is None:
        raise UnsafeOutboundRequestError("response content type is missing")
    normalized = content_type.split(";", 1)[0].strip().lower()
    if normalized not in policy.allowed_content_types:
        raise UnsafeOutboundRequestError(
            f"response content type {normalized!r} is not allowed"
        )
    return normalized


def validate_response_size(
    content_length: int | None,
    bytes_read: int,
    policy: SecurityPolicy,
) -> None:
    if content_length is not None:
        if not isinstance(content_length, int) or content_length < 0:
            raise UnsafeOutboundRequestError("invalid Content-Length")
        if content_length > policy.max_response_bytes:
            raise UnsafeOutboundRequestError(
                "declared response exceeds configured size limit"
            )
    if bytes_read > policy.max_response_bytes:
        raise UnsafeOutboundRequestError(
            "response exceeds configured size limit"
        )


class AuthorPseudonymizer:
    """Stable per-source HMAC pseudonyms without retaining raw identifiers."""

    def __init__(self, secret: bytes) -> None:
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("pseudonymization secret must contain at least 32 bytes")
        self._secret = secret

    def pseudonymize(self, source_id: str, author_id: str) -> str:
        source = source_id.strip().lower()
        author = author_id.strip()
        if not source or not author:
            raise ValueError("source_id and author_id must not be empty")
        digest = hmac.new(
            self._secret,
            f"{source}\0{author}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return f"hmac-sha256:{digest}"


def transform_author_identifier(
    policy: SourcePolicy,
    author_id: str | None,
    *,
    pseudonymizer: AuthorPseudonymizer | None,
) -> str | None:
    if author_id is None:
        return None
    mode = policy.data.author_identifier_mode
    if mode is AuthorIdentifierMode.DROP:
        return None
    if mode is AuthorIdentifierMode.RAW:
        return author_id
    if pseudonymizer is None:
        raise SourcePolicyError(
            "HMAC pseudonymization is required but no pseudonymizer is configured"
        )
    return pseudonymizer.pseudonymize(policy.source_id, author_id)


def filter_metadata(
    metadata: Mapping[str, object],
    allowed_fields: tuple[str, ...],
) -> dict[str, object]:
    """Keep only explicitly approved metadata fields."""

    allowed = set(allowed_fields)
    return {key: value for key, value in metadata.items() if key in allowed}
