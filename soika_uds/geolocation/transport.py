"""Rate-limited retrying JSON HTTP transport."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from threading import Lock
from typing import Any, Protocol


class JsonTransport(Protocol):
    def request_json(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> Any: ...


class TransportError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class HttpRetryPolicy:
    attempts: int = 3
    timeout_seconds: float = 15.0
    initial_backoff_seconds: float = 0.5
    multiplier: float = 2.0
    max_backoff_seconds: float = 8.0

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("attempts must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.initial_backoff_seconds < 0:
            raise ValueError("initial_backoff_seconds must not be negative")
        if self.multiplier < 1:
            raise ValueError("multiplier must be at least 1")
        if self.max_backoff_seconds < 0:
            raise ValueError("max_backoff_seconds must not be negative")

    def delay(self, failed_attempt: int) -> float:
        return min(
            self.initial_backoff_seconds * self.multiplier ** (failed_attempt - 1),
            self.max_backoff_seconds,
        )


class RateLimiter:
    def __init__(
        self,
        minimum_interval_seconds: float,
        *,
        clock=time.monotonic,
        sleeper=time.sleep,
    ) -> None:
        if minimum_interval_seconds < 0:
            raise ValueError("minimum interval must not be negative")
        self._interval = float(minimum_interval_seconds)
        self._clock = clock
        self._sleeper = sleeper
        self._lock = Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            delay = max(0.0, self._next_allowed - now)
            if delay:
                self._sleeper(delay)
                now = self._clock()
            self._next_allowed = max(now, self._next_allowed) + self._interval


class RequestsJsonTransport:
    _retryable_statuses = frozenset({408, 425, 429, 500, 502, 503, 504})

    def __init__(
        self,
        *,
        user_agent: str,
        policy: HttpRetryPolicy | None = None,
        rate_limiter: RateLimiter | None = None,
        session: Any | None = None,
        sleeper=time.sleep,
    ) -> None:
        user_agent = user_agent.strip()
        if not user_agent or user_agent.casefold().startswith(
            ("python-requests", "requests/")
        ):
            raise ValueError("a descriptive application User-Agent is required")
        self._headers = {
            "Accept": "application/json",
            "User-Agent": user_agent,
        }
        self._policy = policy or HttpRetryPolicy()
        self._limiter = rate_limiter or RateLimiter(0.0)
        self._session = session
        self._sleeper = sleeper

    @property
    def session(self) -> Any:
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def request_json(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> Any:
        if not url.startswith("https://"):
            raise ValueError("external OSM endpoints must use HTTPS")
        last_error: Exception | None = None
        for attempt in range(1, self._policy.attempts + 1):
            self._limiter.wait()
            try:
                response = self.session.request(
                    method.upper(),
                    url,
                    params=dict(params or {}),
                    data=dict(data or {}),
                    headers=self._headers,
                    timeout=self._policy.timeout_seconds,
                )
                status = int(response.status_code)
                if status >= 400:
                    retryable = status in self._retryable_statuses
                    error = TransportError(
                        f"HTTP {status} from {url}",
                        retryable=retryable,
                        status_code=status,
                    )
                    if not retryable or attempt == self._policy.attempts:
                        raise error
                    last_error = error
                else:
                    return response.json()
            except TransportError:
                raise
            except Exception as error:
                last_error = error
                if attempt == self._policy.attempts:
                    raise TransportError(
                        f"request failed for {url}: {type(error).__name__}",
                        retryable=True,
                    ) from error
            self._sleeper(self._policy.delay(attempt))
        raise TransportError(
            f"request failed for {url}: {last_error}",
            retryable=True,
        )
