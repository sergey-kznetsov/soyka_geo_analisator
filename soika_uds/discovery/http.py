"""Safe ordinary-HTTP fetcher used before the browser fallback."""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from ..parsers import SecurityPolicy
from ..parsers.security import (
    Resolver,
    UnsafeOutboundRequestError,
    validate_content_type,
    validate_outbound_url,
    validate_response_size,
)
from .browser import BrowserRenderError, RenderedPage, classify_browser_block
from .models import SourceReasonCode, SourceState


class _StaticHtmlParser(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template", "svg"}
    _MAIN = {"article", "main"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._title_depth = 0
        self._h1_depth = 0
        self._main_depth = 0
        self.title_parts: list[str] = []
        self.h1_parts: list[str] = []
        self.body_parts: list[str] = []
        self.fallback_parts: list[str] = []
        self.canonical_url: str | None = None
        self.published_at: str | None = None
        self.og_title: str | None = None

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {key.lower(): value or "" for key, value in attrs}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        values = self._attrs(attrs)
        if tag in self._SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._title_depth += 1
        if tag == "h1":
            self._h1_depth += 1
        if tag in self._MAIN:
            self._main_depth += 1
        if tag == "link" and "canonical" in values.get("rel", "").lower():
            href = values.get("href", "").strip()
            if href and self.canonical_url is None:
                self.canonical_url = href
        if tag == "meta":
            key = (
                values.get("property")
                or values.get("name")
                or values.get("itemprop")
                or ""
            ).lower()
            content = values.get("content", "").strip()
            if content:
                if key == "og:title" and self.og_title is None:
                    self.og_title = content
                if key in {
                    "article:published_time",
                    "date",
                    "pubdate",
                    "datepublished",
                } and self.published_at is None:
                    self.published_at = content
        if tag == "time" and self.published_at is None:
            value = values.get("datetime", "").strip()
            if value:
                self.published_at = value

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._title_depth = max(0, self._title_depth - 1)
        if tag == "h1":
            self._h1_depth = max(0, self._h1_depth - 1)
        if tag in self._MAIN:
            self._main_depth = max(0, self._main_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        value = " ".join(data.split())
        if not value:
            return
        if self._title_depth:
            self.title_parts.append(value)
        if self._h1_depth:
            self.h1_parts.append(value)
        self.fallback_parts.append(value)
        if self._main_depth:
            self.body_parts.append(value)

    @staticmethod
    def _join(parts: list[str]) -> str:
        return " ".join(" ".join(parts).split())

    @property
    def title(self) -> str:
        return self._join(self.h1_parts) or self.og_title or self._join(self.title_parts)

    @property
    def body(self) -> str:
        value = self._join(self.body_parts)
        return value if len(value) >= 80 else self._join(self.fallback_parts)


class _RedirectHandler(HTTPRedirectHandler):
    def __init__(self, security: SecurityPolicy, resolver: Resolver | None) -> None:
        super().__init__()
        self.security = security
        self.resolver = resolver
        self.count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del fp, code, msg, headers
        self.count += 1
        if self.count > self.security.max_redirects:
            raise UnsafeOutboundRequestError("redirect limit exceeded")
        absolute = urljoin(req.full_url, newurl)
        kwargs: dict[str, Any] = {}
        if self.resolver is not None:
            kwargs["resolver"] = self.resolver
        validated = validate_outbound_url(absolute, self.security, **kwargs)
        return Request(validated, headers=dict(req.header_items()), method="GET")


@dataclass(frozen=True, slots=True)
class StaticHtmlFetcher:
    timeout_seconds: float = 20.0
    resolver: Resolver | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.timeout_seconds, int | float) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    def fetch(self, url: str, security: SecurityPolicy) -> RenderedPage:
        kwargs: dict[str, Any] = {}
        if self.resolver is not None:
            kwargs["resolver"] = self.resolver
        try:
            target = validate_outbound_url(url, security, **kwargs)
        except UnsafeOutboundRequestError as error:
            raise BrowserRenderError(
                SourceReasonCode.SOURCE_CONFIGURATION_MISSING,
                f"static fetch blocked unsafe outbound URL: {error}",
                state=SourceState.BLOCKED,
            ) from error
        handler = _RedirectHandler(security, self.resolver)
        opener = build_opener(handler, HTTPSHandler(context=ssl.create_default_context()))
        request = Request(
            target,
            headers={
                "User-Agent": security.user_agent,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
            },
            method="GET",
        )
        try:
            with opener.open(request, timeout=self.timeout_seconds) as response:
                content_type = response.headers.get("content-type")
                validate_content_type(content_type, security)
                length = response.headers.get("content-length")
                declared = int(length) if length and length.isdigit() else None
                body = response.read(security.max_response_bytes + 1)
                validate_response_size(declared, len(body), security)
                status = response.status
                final_url = validate_outbound_url(response.geturl(), security, **kwargs)
        except HTTPError as error:
            wall = classify_browser_block(
                final_url=error.geturl(),
                status_code=error.code,
                title="",
                body_text="",
            )
            if wall is not None:
                raise wall from error
            raise BrowserRenderError(
                SourceReasonCode.PARSER_FAILED,
                f"source returned HTTP {error.code}",
                state=SourceState.UNAVAILABLE,
                retryable=error.code >= 500,
            ) from error
        except (URLError, TimeoutError) as error:
            raise BrowserRenderError(
                SourceReasonCode.SOURCE_TIMEOUT,
                "ordinary HTTP fetch failed or timed out",
                retryable=True,
            ) from error
        except UnsafeOutboundRequestError as error:
            raise BrowserRenderError(
                SourceReasonCode.SOURCE_CONFIGURATION_MISSING,
                f"static fetch blocked unsafe response: {error}",
                state=SourceState.BLOCKED,
            ) from error

        encoding = "utf-8"
        for item in (content_type or "").split(";")[1:]:
            name, separator, value = item.strip().partition("=")
            if separator and name.lower() == "charset" and value.strip():
                encoding = value.strip().strip("\"'")
                break
        try:
            html = body.decode(encoding, errors="replace")
        except LookupError:
            html = body.decode("utf-8", errors="replace")
        parser = _StaticHtmlParser()
        parser.feed(html)
        canonical = parser.canonical_url
        if canonical:
            canonical = urljoin(final_url, canonical)
            canonical = validate_outbound_url(canonical, security, **kwargs)
        wall = classify_browser_block(
            final_url=final_url,
            status_code=status,
            title=parser.title,
            body_text=parser.body,
        )
        if wall is not None:
            raise wall
        return RenderedPage(
            requested_url=target,
            final_url=final_url,
            status_code=status,
            title=parser.title,
            body_text=parser.body,
            canonical_url=canonical,
            published_at=parser.published_at,
            comments=(),
        )


__all__ = ["StaticHtmlFetcher"]
