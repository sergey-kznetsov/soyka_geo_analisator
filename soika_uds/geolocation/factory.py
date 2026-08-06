"""Safe constructors for public OSM services."""

from __future__ import annotations

from .cache import SQLiteResponseCache
from .semantic_provider import SemanticNominatimClient
from .transport import HttpRetryPolicy, RateLimiter, RequestsJsonTransport


def public_nominatim_client(
    cache: SQLiteResponseCache,
    *,
    user_agent: str,
    policy: HttpRetryPolicy | None = None,
) -> SemanticNominatimClient:
    """Construct a public Nominatim client limited to one request per second."""

    transport = RequestsJsonTransport(
        user_agent=user_agent,
        policy=policy,
        rate_limiter=RateLimiter(1.0),
    )
    return SemanticNominatimClient(transport, cache)
