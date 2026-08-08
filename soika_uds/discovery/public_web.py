"""Generic reviewed public-web collector with HTTP-first/browser-fallback access."""

# ruff: noqa: I001

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from ..contracts import SourceMessage
from ..parsers import SourcePolicy
from .access import SourceAccessAuthorizer
from .browser import (
    BrowserRenderError,
    BrowserRenderer,
    RenderedComment,
    RenderedPage,
)
from .collection import CandidateCollectionError, CandidateCollectionResult
from .http import StaticHtmlFetcher
from .models import (
    GeoScope,
    SourceCandidate,
    SourceKind,
    SourceOutcome,
    SourceReasonCode,
    SourceState,
)

_RELEVANT_HINTS = frozenset({"house", "street", "district"})


def _normalize(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _external_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode()).hexdigest()[:24]
    return f"{prefix}:{digest}"


def _metadata_allowed(policy: SourcePolicy, key: str) -> bool:
    return f"metadata.{key}" in policy.data.allowed_fields


def _message_metadata(
    policy: SourcePolicy,
    *,
    kind: str,
    title: str,
    browser_used: bool,
    relevance: str,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "kind": kind,
        "title": title,
        "browser_used": browser_used,
        "geo_relevance_hint": relevance,
    }
    return {
        key: value
        for key, value in values.items()
        if _metadata_allowed(policy, key)
    }


def geo_relevance_hint(text: str, scope: GeoScope) -> str:
    """Conservative hint only; final inclusion remains the geolocation/filter stage."""

    normalized = _normalize(text)
    street = _normalize(scope.street or "")
    house = _normalize(scope.house_number or "")
    district = _normalize(scope.district or "")
    city = _normalize(scope.city)
    if street and house and street in normalized and house in normalized:
        return "house"
    if street and street in normalized:
        return "street"
    if district and district in normalized:
        return "district"
    if city and city in normalized:
        return "city"
    return "unresolved"


def _content_kind(kind: SourceKind, *, comment: bool = False) -> str:
    if kind is SourceKind.LOCAL_MEDIA:
        return "news_comment" if comment else "news_article"
    if kind in {SourceKind.LOCAL_FORUM, SourceKind.PIKABU}:
        return "forum_comment" if comment else "forum_post"
    if kind is SourceKind.DZEN:
        return "dzen_comment" if comment else "dzen_article"
    if kind is SourceKind.MUNICIPAL:
        return "municipal_comment" if comment else "municipal_publication"
    if kind in {SourceKind.YANDEX_MAPS, SourceKind.TWO_GIS}:
        return "map_review" if comment else "map_place_page"
    return "web_comment" if comment else "web_document"


def _article_message(
    candidate: SourceCandidate,
    policy: SourcePolicy,
    page: RenderedPage,
    *,
    browser_used: bool,
    scope: GeoScope,
) -> SourceMessage | None:
    published = _parse_timestamp(page.published_at)
    if published is None or not page.body_text.strip():
        return None
    url = page.canonical_url or page.final_url
    relevance = geo_relevance_hint(f"{page.title}\n{page.body_text}", scope)
    return SourceMessage(
        source=policy.source_id,
        external_id=_external_id("page", candidate.candidate_id, url),
        text=page.body_text,
        published_at=published,
        url=url if "url" in policy.data.allowed_fields else None,
        metadata=_message_metadata(
            policy,
            kind=_content_kind(candidate.kind),
            title=page.title,
            browser_used=browser_used,
            relevance=relevance,
        ),
    )


def _comment_message(
    candidate: SourceCandidate,
    policy: SourcePolicy,
    page: RenderedPage,
    comment: RenderedComment,
    *,
    index: int,
    scope: GeoScope,
) -> SourceMessage | None:
    published = _parse_timestamp(comment.published_at)
    if published is None:
        return None
    url = page.canonical_url or page.final_url
    raw_id = comment.external_id or _external_id(
        "comment",
        url,
        str(index),
        comment.text,
    )
    relevance = geo_relevance_hint(comment.text, scope)
    return SourceMessage(
        source=policy.source_id,
        external_id=f"{candidate.candidate_id}:{raw_id}"[:240],
        text=comment.text,
        published_at=published,
        url=url if "url" in policy.data.allowed_fields else None,
        metadata=_message_metadata(
            policy,
            kind=_content_kind(candidate.kind, comment=True),
            title=page.title,
            browser_used=True,
            relevance=relevance,
        ),
    )


@dataclass(frozen=True, slots=True)
class PublicWebCollector:
    """Collect one discovered URL only after policy and robots approval."""

    source_kind: SourceKind
    authorizer: SourceAccessAuthorizer
    static_fetcher: StaticHtmlFetcher
    browser: BrowserRenderer | None = None
    minimum_static_chars: int = 240

    def __post_init__(self) -> None:
        dedicated_kinds = {
            SourceKind.TELEGRAM,
            SourceKind.VK,
            SourceKind.OK,
            SourceKind.MAX,
        }
        if self.source_kind in dedicated_kinds:
            raise ValueError(
                "messenger/social API sources require dedicated collectors"
            )
        if (
            not isinstance(self.minimum_static_chars, int)
            or self.minimum_static_chars < 80
        ):
            raise ValueError("minimum_static_chars must be at least 80")

    def _render(
        self,
        candidate: SourceCandidate,
        policy: SourcePolicy,
    ) -> tuple[RenderedPage, bool]:
        force_browser = bool(policy.metadata.get("render_javascript", False))
        if not force_browser:
            try:
                page = self.static_fetcher.fetch(candidate.url, policy.security)
            except BrowserRenderError as error:
                if error.code is not SourceReasonCode.PARSER_FAILED:
                    raise
            else:
                if len(page.body_text) >= self.minimum_static_chars:
                    return page, False
        if self.browser is None:
            raise BrowserRenderError(
                SourceReasonCode.SOURCE_CONFIGURATION_MISSING,
                "page requires browser rendering but browser runtime is not configured",
                state=SourceState.CONFIGURATION_MISSING,
            )
        return self.browser.render(candidate.url, policy.security), True

    def collect(
        self,
        candidate: SourceCandidate,
        scope: GeoScope,
    ) -> CandidateCollectionResult:
        if candidate.kind is not self.source_kind:
            raise ValueError("candidate kind does not match collector source_kind")
        policy = self.authorizer.authorize(candidate)
        try:
            page, browser_used = self._render(candidate, policy)
        except BrowserRenderError as error:
            raise CandidateCollectionError(
                error.code,
                str(error),
                state=error.state,
                retryable=error.retryable,
                details=error.details,
            ) from error

        messages: list[SourceMessage] = []
        relevant = 0
        article = _article_message(
            candidate,
            policy,
            page,
            browser_used=browser_used,
            scope=scope,
        )
        if article is not None:
            messages.append(article)
            article_hint = geo_relevance_hint(
                f"{page.title}\n{page.body_text}",
                scope,
            )
            relevant += int(article_hint in _RELEVANT_HINTS)
        for index, comment in enumerate(page.comments):
            item = _comment_message(
                candidate,
                policy,
                page,
                comment,
                index=index,
                scope=scope,
            )
            if item is not None:
                messages.append(item)
                comment_hint = geo_relevance_hint(comment.text, scope)
                relevant += int(comment_hint in _RELEVANT_HINTS)

        if messages:
            state = SourceState.COLLECTED
            reason = "source collected successfully"
            reason_code = SourceReasonCode.NONE
        elif page.body_text:
            relevant = 0
            state = SourceState.PARTIAL
            reason = (
                "public text was accessible but no record had a usable "
                "timezone-aware publication timestamp"
            )
            reason_code = SourceReasonCode.NO_RELEVANT_CONTENT
        else:
            relevant = 0
            state = SourceState.NO_RELEVANT_RESULTS
            reason = (
                "source was checked successfully but contained no collectable "
                "public text"
            )
            reason_code = SourceReasonCode.NO_RESULTS

        comments_emitted = len(messages) - (1 if article is not None else 0)
        return CandidateCollectionResult(
            messages=tuple(messages),
            outcome=SourceOutcome(
                source_id=candidate.candidate_id,
                kind=candidate.kind,
                state=state,
                reason_code=reason_code,
                reason=reason,
                attempted_urls=(candidate.url, page.final_url),
                messages_collected=len(messages),
                relevant_messages=relevant,
                details={
                    "policy_source_id": policy.source_id,
                    "browser_used": browser_used,
                    "blocked_subrequests": page.blocked_subrequests,
                    "comments_seen": len(page.comments),
                    "comments_emitted": max(0, comments_emitted),
                    "final_geo_filter_required": True,
                },
            ),
        )


__all__ = ["PublicWebCollector", "geo_relevance_hint"]
