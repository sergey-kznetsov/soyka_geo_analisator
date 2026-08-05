"""Configuration contracts for local-media and municipal HTML sources."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit

from .models import ConnectorDefinitionError


class HtmlSourceKind(str, Enum):
    LOCAL_MEDIA = "local_media"
    MUNICIPAL = "municipal"


class DiscoveryMode(str, Enum):
    RSS = "rss"
    ATOM = "atom"
    SITEMAP = "sitemap"
    SECTION = "section"
    INDEX_PAGE = "index_page"


def _required(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConnectorDefinitionError(f"{field_name} must be a non-empty string")
    return value.strip()


def _https_url(value: object, field_name: str) -> tuple[str, str]:
    url = _required(value, field_name)
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise ConnectorDefinitionError(f"{field_name} must use HTTPS")
    if parsed.username or parsed.password:
        raise ConnectorDefinitionError(f"{field_name} must not contain credentials")
    if not parsed.hostname:
        raise ConnectorDefinitionError(f"{field_name} must contain a hostname")
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        raise ConnectorDefinitionError(f"{field_name} must not use an IP literal")
    return url, parsed.hostname.encode("idna").decode("ascii").lower()


@dataclass(frozen=True, slots=True)
class HtmlSelectors:
    title: str
    body: str
    published_at: str
    author: str | None = None
    canonical_url: str | None = None
    comment_item: str | None = None
    comment_text: str | None = None
    comment_id: str | None = None
    comment_author: str | None = None
    comment_published_at: str | None = None
    comment_parent_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("title", "body", "published_at"):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), f"selectors.{field_name}"),
            )
        optional_fields = (
            "author",
            "canonical_url",
            "comment_item",
            "comment_text",
            "comment_id",
            "comment_author",
            "comment_published_at",
            "comment_parent_id",
        )
        for field_name in optional_fields:
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    _required(value, f"selectors.{field_name}"),
                )
        comment_values = (self.comment_item, self.comment_text)
        if any(item is not None for item in comment_values) and not all(
            item is not None for item in comment_values
        ):
            raise ConnectorDefinitionError(
                "comment_item and comment_text must be configured together"
            )

    def to_dict(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for field_name in self.__dataclass_fields__:
            value = getattr(self, field_name)
            if value is not None:
                result[field_name] = value
        return result


@dataclass(frozen=True, slots=True)
class HtmlSourceProfile:
    source_id: str
    display_name: str
    kind: HtmlSourceKind
    base_url: str
    region: str
    municipalities: tuple[str, ...]
    discovery_mode: DiscoveryMode
    discovery_urls: tuple[str, ...]
    selectors: HtmlSelectors
    robots_url: str
    render_javascript: bool = False
    rendering_justification: str | None = None
    enabled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.kind, HtmlSourceKind):
            object.__setattr__(self, "kind", HtmlSourceKind(self.kind))
        source_id = _required(self.source_id, "source_id").lower()
        required_prefix = (
            "local-media." if self.kind is HtmlSourceKind.LOCAL_MEDIA else "municipal."
        )
        if not source_id.startswith(required_prefix):
            raise ConnectorDefinitionError(
                f"source_id must start with {required_prefix!r}"
            )
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(
            self,
            "display_name",
            _required(self.display_name, "display_name"),
        )
        base_url, base_host = _https_url(self.base_url, "base_url")
        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "region", _required(self.region, "region"))
        municipalities = tuple(
            _required(item, "municipalities[]") for item in self.municipalities
        )
        if not municipalities or len(municipalities) != len(set(municipalities)):
            raise ConnectorDefinitionError(
                "municipalities must be non-empty and contain no duplicates"
            )
        object.__setattr__(self, "municipalities", municipalities)
        if not isinstance(self.discovery_mode, DiscoveryMode):
            object.__setattr__(
                self,
                "discovery_mode",
                DiscoveryMode(self.discovery_mode),
            )
        if not self.discovery_urls:
            raise ConnectorDefinitionError("discovery_urls must not be empty")
        normalized_urls: list[str] = []
        for index, item in enumerate(self.discovery_urls):
            url, host = _https_url(item, f"discovery_urls[{index}]")
            if host != base_host and not host.endswith(f".{base_host}"):
                raise ConnectorDefinitionError(
                    "discovery URLs must stay within the configured source domain"
                )
            normalized_urls.append(url)
        if len(normalized_urls) != len(set(normalized_urls)):
            raise ConnectorDefinitionError("discovery_urls must not contain duplicates")
        object.__setattr__(self, "discovery_urls", tuple(normalized_urls))
        if not isinstance(self.selectors, HtmlSelectors):
            raise ConnectorDefinitionError("selectors must be HtmlSelectors")
        robots_url, robots_host = _https_url(self.robots_url, "robots_url")
        if robots_host != base_host and not robots_host.endswith(f".{base_host}"):
            raise ConnectorDefinitionError("robots_url must use the source domain")
        object.__setattr__(self, "robots_url", robots_url)
        if not isinstance(self.render_javascript, bool):
            raise ConnectorDefinitionError("render_javascript must be a boolean")
        if not isinstance(self.enabled, bool):
            raise ConnectorDefinitionError("enabled must be a boolean")
        if self.enabled:
            raise ConnectorDefinitionError(
                "prepared HTML profiles must remain disabled until integration review"
            )
        if self.render_javascript:
            object.__setattr__(
                self,
                "rendering_justification",
                _required(
                    self.rendering_justification,
                    "rendering_justification",
                ),
            )
        elif self.rendering_justification is not None:
            raise ConnectorDefinitionError(
                "rendering_justification requires render_javascript=true"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "display_name": self.display_name,
            "kind": self.kind.value,
            "base_url": self.base_url,
            "region": self.region,
            "municipalities": list(self.municipalities),
            "discovery_mode": self.discovery_mode.value,
            "discovery_urls": list(self.discovery_urls),
            "selectors": self.selectors.to_dict(),
            "robots_url": self.robots_url,
            "render_javascript": self.render_javascript,
            "rendering_justification": self.rendering_justification,
            "enabled": self.enabled,
        }
