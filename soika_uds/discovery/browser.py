"""Headless browser fallback for reviewed public-web sources.

The renderer does not search the web. It only opens an already discovered URL after
source-policy and outbound-network validation. Cross-domain subresources are denied
unless the source policy explicitly allowlists them.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

from ..parsers import SecurityPolicy
from ..parsers.security import (
    Resolver,
    UnsafeOutboundRequestError,
    validate_outbound_url,
)
from .models import SourceReasonCode, SourceState, canonical_url

_AUTH_PATH_RE = re.compile(
    r"/(?:login|signin|sign-in|auth|authorize|sso)(?:/|$)",
    re.I,
)
_CAPTCHA_RE = re.compile(
    r"\b(captcha|капч[аи]|verify\s+(?:that\s+)?you\s+are\s+human|я\s+не\s+робот)\b",
    re.I,
)
_ANTI_BOT_RE = re.compile(
    r"\b(access denied|доступ ограничен|bot detection|anti[- ]bot|challenge-platform)\b",
    re.I,
)


class BrowserRenderError(RuntimeError):
    def __init__(
        self,
        code: SourceReasonCode,
        message: str,
        *,
        state: SourceState = SourceState.UNAVAILABLE,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.state = state
        self.retryable = retryable
        self.details = dict(details or {})


@dataclass(frozen=True, slots=True)
class RenderedComment:
    external_id: str | None
    text: str
    published_at: str | None = None

    def __post_init__(self) -> None:
        if self.external_id is not None:
            value = self.external_id.strip()
            object.__setattr__(self, "external_id", value or None)
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("comment text must not be empty")
        object.__setattr__(self, "text", self.text.strip())
        if self.published_at is not None:
            value = self.published_at.strip()
            object.__setattr__(self, "published_at", value or None)


@dataclass(frozen=True, slots=True)
class RenderedPage:
    requested_url: str
    final_url: str
    status_code: int | None
    title: str
    body_text: str
    canonical_url: str | None = None
    published_at: str | None = None
    comments: tuple[RenderedComment, ...] = ()
    blocked_subrequests: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "requested_url",
            canonical_url(self.requested_url),
        )
        object.__setattr__(self, "final_url", canonical_url(self.final_url))
        if self.status_code is not None and (
            not isinstance(self.status_code, int)
            or not 100 <= self.status_code <= 599
        ):
            raise ValueError("status_code must be an HTTP status")
        if not isinstance(self.title, str):
            raise ValueError("title must be a string")
        if not isinstance(self.body_text, str):
            raise ValueError("body_text must be a string")
        object.__setattr__(self, "title", self.title.strip())
        object.__setattr__(self, "body_text", self.body_text.strip())
        if self.canonical_url is not None:
            object.__setattr__(
                self,
                "canonical_url",
                canonical_url(self.canonical_url),
            )
        if self.published_at is not None:
            value = self.published_at.strip()
            object.__setattr__(self, "published_at", value or None)
        object.__setattr__(self, "comments", tuple(self.comments))
        if (
            not isinstance(self.blocked_subrequests, int)
            or self.blocked_subrequests < 0
        ):
            raise ValueError("blocked_subrequests must be non-negative")


class BrowserRenderer(Protocol):
    def render(self, url: str, security: SecurityPolicy) -> RenderedPage: ...


def _body_signal(title: str, body: str) -> str:
    return f"{title}\n{body[:5000]}"


def classify_browser_block(
    *,
    final_url: str,
    status_code: int | None,
    title: str,
    body_text: str,
) -> BrowserRenderError | None:
    """Turn browser-visible access walls into explicit source outcomes."""

    if status_code == 401:
        return BrowserRenderError(
            SourceReasonCode.AUTH_REQUIRED,
            "source requires authentication (HTTP 401)",
            state=SourceState.AUTH_REQUIRED,
        )
    if status_code == 403:
        return BrowserRenderError(
            SourceReasonCode.HTTP_403,
            "source returned HTTP 403 Forbidden",
        )
    if status_code == 429:
        return BrowserRenderError(
            SourceReasonCode.HTTP_429,
            "source returned HTTP 429 Too Many Requests",
            retryable=True,
        )
    if status_code is not None and status_code >= 500:
        return BrowserRenderError(
            SourceReasonCode.SOURCE_TIMEOUT,
            f"source returned HTTP {status_code}",
            retryable=True,
        )

    parsed = urlsplit(final_url)
    if _AUTH_PATH_RE.search(parsed.path):
        return BrowserRenderError(
            SourceReasonCode.AUTH_REQUIRED,
            "source redirected to an authentication page",
            state=SourceState.AUTH_REQUIRED,
        )
    signal = _body_signal(title, body_text)
    if _CAPTCHA_RE.search(signal):
        return BrowserRenderError(
            SourceReasonCode.CAPTCHA,
            "source presented a CAPTCHA or human-verification page",
            state=SourceState.BLOCKED,
        )
    if _ANTI_BOT_RE.search(signal):
        return BrowserRenderError(
            SourceReasonCode.ANTI_BOT,
            "source presented an anti-bot/access challenge",
            state=SourceState.BLOCKED,
        )
    return None


_EXTRACT_SCRIPT = r"""
() => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const text = element => element ? clean(element.innerText || element.textContent || '') : '';
  const first = selectors => {
    for (const selector of selectors) {
      const found = document.querySelector(selector);
      if (found) return found;
    }
    return null;
  };
  const meta = (...selectors) => {
    for (const selector of selectors) {
      const found = document.querySelector(selector);
      const value = found && found.getAttribute('content');
      if (value) return clean(value);
    }
    return '';
  };
  const titleElement = first(['h1', '[role="heading"][aria-level="1"]']);
  const title = text(titleElement) || meta('meta[property="og:title"]') || clean(document.title);
  const bodyCandidates = Array.from(document.querySelectorAll(
    'article, main, [role="main"], .article, .post, .entry-content, .content, .news-item'
  ));
  let body = '';
  for (const candidate of bodyCandidates) {
    const value = text(candidate);
    if (value.length > body.length) body = value;
  }
  if (!body) body = text(document.body);
  const canonicalNode = document.querySelector('link[rel="canonical"]');
  const canonical = canonicalNode ? canonicalNode.href : '';
  const timeNode = first(['time[datetime]', 'time']);
  const published = meta(
    'meta[property="article:published_time"]',
    'meta[name="date"]',
    'meta[name="pubdate"]',
    'meta[itemprop="datePublished"]'
  ) || (timeNode && (timeNode.getAttribute('datetime') || text(timeNode))) || '';

  const commentSelectors = [
    '[data-comment-id]',
    '[id^="comment-"]',
    '[id^="comment_"]',
    '[class~="comment"]',
    '[class*=" comment-"]',
    '[class*="comment__"]'
  ];
  const seen = new Set();
  const comments = [];
  for (const selector of commentSelectors) {
    for (const node of document.querySelectorAll(selector)) {
      if (comments.length >= 200) break;
      const value = text(node);
      if (value.length < 8 || value.length > 5000 || seen.has(value)) continue;
      seen.add(value);
      const id = node.getAttribute('data-comment-id') || node.id || '';
      const time = node.querySelector('time');
      comments.push({
        external_id: clean(id),
        text: value,
        published_at: time ? clean(time.getAttribute('datetime') || text(time)) : ''
      });
    }
    if (comments.length >= 200) break;
  }
  return {title, body, canonical, published, comments};
}
"""


@dataclass(frozen=True, slots=True)
class PlaywrightBrowserRenderer:
    """Render one reviewed page in Chromium without bypassing access controls."""

    timeout_seconds: float = 20.0
    resolver: Resolver | None = None
    browser_factory: Callable[[], Any] | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.timeout_seconds, int | float)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive")

    def _validate(self, url: str, security: SecurityPolicy) -> str:
        kwargs: dict[str, Any] = {}
        if self.resolver is not None:
            kwargs["resolver"] = self.resolver
        try:
            return validate_outbound_url(url, security, **kwargs)
        except UnsafeOutboundRequestError as error:
            raise BrowserRenderError(
                SourceReasonCode.SOURCE_CONFIGURATION_MISSING,
                f"browser blocked unsafe outbound URL: {error}",
                state=SourceState.BLOCKED,
            ) from error

    def _runtime(self) -> Any:
        if self.browser_factory is not None:
            return self.browser_factory()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise BrowserRenderError(
                SourceReasonCode.SOURCE_CONFIGURATION_MISSING,
                "Playwright browser runtime is not installed",
                state=SourceState.CONFIGURATION_MISSING,
            ) from error
        return sync_playwright()

    def render(self, url: str, security: SecurityPolicy) -> RenderedPage:
        target = self._validate(url, security)
        timeout_ms = int(float(self.timeout_seconds) * 1000)
        blocked: list[str] = []
        runtime = self._runtime()
        entered = hasattr(runtime, "__enter__")
        manager = runtime.__enter__() if entered else runtime
        browser = None
        context = None
        try:
            browser = manager.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=False,
                java_script_enabled=True,
                user_agent=security.user_agent,
                service_workers="block",
            )
            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            page.set_default_navigation_timeout(timeout_ms)

            def route_request(route: Any, request: Any) -> None:
                request_url = str(request.url)
                try:
                    self._validate(request_url, security)
                except BrowserRenderError:
                    host = urlsplit(request_url).hostname or "non-http"
                    blocked.append(host)
                    route.abort()
                    return
                route.continue_()

            page.route("**/*", route_request)
            response = page.goto(target, wait_until="domcontentloaded")
            page.locator("body").wait_for(state="attached")
            try:
                page.wait_for_load_state("load", timeout=min(timeout_ms, 5000))
            except Exception:  # noqa: BLE001 - optional settle, extraction can continue
                pass
            extracted = page.evaluate(_EXTRACT_SCRIPT)
            final_url = self._validate(page.url, security)
            title = str(extracted.get("title", ""))
            body_text = str(extracted.get("body", ""))
            status_code = response.status if response is not None else None
            block = classify_browser_block(
                final_url=final_url,
                status_code=status_code,
                title=title,
                body_text=body_text,
            )
            if block is not None:
                raise block
            if len(body_text.encode()) > security.max_response_bytes:
                raise BrowserRenderError(
                    SourceReasonCode.PARSER_FAILED,
                    "rendered page exceeded configured text-size limit",
                    state=SourceState.FAILED,
                )
            comments = tuple(
                RenderedComment(
                    external_id=item.get("external_id") or None,
                    text=str(item.get("text", "")),
                    published_at=item.get("published_at") or None,
                )
                for item in extracted.get("comments", [])
                if isinstance(item, dict) and str(item.get("text", "")).strip()
            )
            canonical_value = extracted.get("canonical") or None
            if canonical_value is not None:
                try:
                    canonical_value = self._validate(
                        str(canonical_value),
                        security,
                    )
                except BrowserRenderError:
                    canonical_value = None
            return RenderedPage(
                requested_url=target,
                final_url=final_url,
                status_code=status_code,
                title=title,
                body_text=body_text,
                canonical_url=canonical_value,
                published_at=extracted.get("published") or None,
                comments=comments,
                blocked_subrequests=len(blocked),
            )
        except BrowserRenderError:
            raise
        except Exception as error:  # noqa: BLE001 - browser process isolation boundary
            error_name = type(error).__name__
            if error_name == "TimeoutError" or "Timeout" in error_name:
                raise BrowserRenderError(
                    SourceReasonCode.SOURCE_TIMEOUT,
                    "browser navigation timed out",
                    retryable=True,
                ) from error
            raise BrowserRenderError(
                SourceReasonCode.PARSER_FAILED,
                f"browser rendering failed: {error_name}",
                state=SourceState.FAILED,
            ) from error
        finally:
            if context is not None:
                with suppress(Exception):  # noqa: BLE001 - cleanup only
                    context.close()
            if browser is not None:
                with suppress(Exception):  # noqa: BLE001 - cleanup only
                    browser.close()
            if entered:
                with suppress(Exception):  # noqa: BLE001 - cleanup only
                    runtime.__exit__(None, None, None)


__all__ = [
    "BrowserRenderError",
    "BrowserRenderer",
    "PlaywrightBrowserRenderer",
    "RenderedComment",
    "RenderedPage",
    "classify_browser_block",
]
