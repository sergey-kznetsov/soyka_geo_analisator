"""Validated blueprints for source connectors before parser integration."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any

_SOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9.-]{1,63}$")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PATH_RE = re.compile(r"^(configs|soika_uds)/[a-zA-Z0-9_./-]+$")


class ConnectorDefinitionError(ValueError):
    """Raised when a connector blueprint is unsafe or inconsistent."""


class ConnectorRegistrationError(RuntimeError):
    """Raised when a connector cannot be registered in the catalog."""


class ConnectorFamily(str, Enum):
    SOCIAL_NETWORK = "social_network"
    LOCAL_MEDIA = "local_media"
    MUNICIPAL = "municipal"
    VIDEO_PLATFORM = "video_platform"
    COMMUNITY_PLATFORM = "community_platform"


class ConnectorAccess(str, Enum):
    OFFICIAL_API = "official_api"
    PUBLIC_HTML = "public_html"
    RSS_ATOM = "rss_atom"
    SITEMAP = "sitemap"
    PARTNER_EXPORT = "partner_export"


class ConnectorCapability(str, Enum):
    DOCUMENTS = "documents"
    COMMENTS = "comments"
    REPLIES = "replies"
    EDIT_MARKERS = "edit_markers"
    DELETION_MARKERS = "deletion_markers"
    SEARCH = "search"
    GEO_TAGS = "geo_tags"
    ATTACHMENTS = "attachments"
    RSS_DISCOVERY = "rss_discovery"
    SITEMAP_DISCOVERY = "sitemap_discovery"
    HTML_DISCOVERY = "html_discovery"
    PARTNER_EXPORT = "partner_export"


class ConnectorLifecycle(str, Enum):
    PREPARED = "prepared"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"
    READY_FOR_INTEGRATION = "ready_for_integration"


class ConnectorCostModel(str, Enum):
    FREE_PUBLIC = "free_public"
    FREE_OFFICIAL_API = "free_official_api"
    PARTNER_EXPORT = "partner_export"
    OPTIONAL_PAID = "optional_paid"


class OptionKind(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    STRING_ARRAY = "string_array"
    OBJECT = "object"


class CheckpointKind(str, Enum):
    CURSOR = "cursor"
    OFFSET = "offset"
    PAGE = "page"
    TIMESTAMP = "timestamp"
    URL = "url"


def _clean_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ConnectorDefinitionError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ConnectorDefinitionError(f"{field_name} must not be empty")
    return cleaned


def _normalize_domain(value: object, field_name: str) -> str:
    domain = _clean_text(value, field_name).lower().rstrip(".")
    if any(char in domain for char in "/:@?#"):
        raise ConnectorDefinitionError(f"{field_name} must be a DNS name")
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        pass
    else:
        raise ConnectorDefinitionError(f"{field_name} must not be an IP literal")
    try:
        encoded = domain.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ConnectorDefinitionError(
            f"{field_name} is not a valid DNS name"
        ) from error
    if "." not in encoded or encoded.startswith(".") or encoded.endswith("."):
        raise ConnectorDefinitionError(
            f"{field_name} must be a fully qualified DNS name"
        )
    return encoded


def _unique_tuple(values: tuple[Any, ...], field_name: str) -> tuple[Any, ...]:
    result = tuple(values)
    if len(result) != len(set(result)):
        raise ConnectorDefinitionError(f"{field_name} must not contain duplicates")
    return result


@dataclass(frozen=True, slots=True)
class ConnectorOption:
    name: str
    kind: OptionKind
    required: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        name = _clean_text(self.name, "option.name")
        if _NAME_RE.fullmatch(name) is None:
            raise ConnectorDefinitionError("option.name has invalid format")
        object.__setattr__(self, "name", name)
        if not isinstance(self.kind, OptionKind):
            object.__setattr__(self, "kind", OptionKind(self.kind))
        if not isinstance(self.required, bool):
            raise ConnectorDefinitionError("option.required must be a boolean")
        object.__setattr__(
            self,
            "description",
            _clean_text(self.description, "option.description"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "required": self.required,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class CheckpointField:
    name: str
    kind: CheckpointKind
    required: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        name = _clean_text(self.name, "checkpoint.name")
        if _NAME_RE.fullmatch(name) is None:
            raise ConnectorDefinitionError("checkpoint.name has invalid format")
        object.__setattr__(self, "name", name)
        if not isinstance(self.kind, CheckpointKind):
            object.__setattr__(self, "kind", CheckpointKind(self.kind))
        if not isinstance(self.required, bool):
            raise ConnectorDefinitionError("checkpoint.required must be a boolean")
        object.__setattr__(
            self,
            "description",
            _clean_text(self.description, "checkpoint.description"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "required": self.required,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class ConnectorDefinition:
    """Source-specific blueprint that is deliberately not a live adapter yet."""

    source_id: str
    display_name: str
    family: ConnectorFamily
    access_modes: tuple[ConnectorAccess, ...]
    capabilities: tuple[ConnectorCapability, ...]
    checkpoint_fields: tuple[CheckpointField, ...]
    options: tuple[ConnectorOption, ...]
    cost_model: ConnectorCostModel
    policy_template: str
    jurisdictions: tuple[str, ...] = ("RU",)
    allowed_domains: tuple[str, ...] = ()
    requires_site_profile: bool = False
    customer_supplied_credentials: bool = False
    paid_fallback_allowed: bool = False
    lifecycle: ConnectorLifecycle = ConnectorLifecycle.PREPARED
    network_enabled: bool = False
    notes: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        source_id = _clean_text(self.source_id, "source_id").lower()
        if _SOURCE_ID_RE.fullmatch(source_id) is None:
            raise ConnectorDefinitionError("source_id has invalid format")
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(
            self,
            "display_name",
            _clean_text(self.display_name, "display_name"),
        )
        if not isinstance(self.family, ConnectorFamily):
            object.__setattr__(self, "family", ConnectorFamily(self.family))
        access_modes = tuple(
            item if isinstance(item, ConnectorAccess) else ConnectorAccess(item)
            for item in self.access_modes
        )
        if not access_modes:
            raise ConnectorDefinitionError("access_modes must not be empty")
        object.__setattr__(
            self,
            "access_modes",
            _unique_tuple(access_modes, "access_modes"),
        )
        capabilities = tuple(
            item if isinstance(item, ConnectorCapability) else ConnectorCapability(item)
            for item in self.capabilities
        )
        if not capabilities:
            raise ConnectorDefinitionError("capabilities must not be empty")
        object.__setattr__(
            self,
            "capabilities",
            _unique_tuple(capabilities, "capabilities"),
        )
        if not all(
            isinstance(item, CheckpointField) for item in self.checkpoint_fields
        ):
            raise ConnectorDefinitionError(
                "checkpoint_fields must contain CheckpointField values"
            )
        checkpoint_names = tuple(item.name for item in self.checkpoint_fields)
        _unique_tuple(checkpoint_names, "checkpoint_fields")
        if not all(isinstance(item, ConnectorOption) for item in self.options):
            raise ConnectorDefinitionError(
                "options must contain ConnectorOption values"
            )
        option_names = tuple(item.name for item in self.options)
        _unique_tuple(option_names, "options")
        if not isinstance(self.cost_model, ConnectorCostModel):
            object.__setattr__(self, "cost_model", ConnectorCostModel(self.cost_model))
        policy_template = _clean_text(self.policy_template, "policy_template")
        if _PATH_RE.fullmatch(policy_template) is None or ".." in policy_template:
            raise ConnectorDefinitionError("policy_template must be a repository path")
        object.__setattr__(self, "policy_template", policy_template)
        jurisdictions = tuple(
            _clean_text(item, "jurisdictions[]").upper() for item in self.jurisdictions
        )
        if jurisdictions != ("RU",):
            raise ConnectorDefinitionError(
                "prepared connectors are restricted to jurisdiction RU"
            )
        object.__setattr__(self, "jurisdictions", jurisdictions)
        domains = tuple(
            _normalize_domain(item, "allowed_domains[]")
            for item in self.allowed_domains
        )
        object.__setattr__(
            self,
            "allowed_domains",
            _unique_tuple(domains, "allowed_domains"),
        )
        for field_name in (
            "requires_site_profile",
            "customer_supplied_credentials",
            "paid_fallback_allowed",
            "network_enabled",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ConnectorDefinitionError(f"{field_name} must be a boolean")
        if not self.allowed_domains and not self.requires_site_profile:
            raise ConnectorDefinitionError(
                "allowed_domains are required unless a site profile supplies them"
            )
        if self.requires_site_profile and self.allowed_domains:
            raise ConnectorDefinitionError(
                "generic site-profile connectors must not hard-code domains"
            )
        if not isinstance(self.lifecycle, ConnectorLifecycle):
            object.__setattr__(
                self,
                "lifecycle",
                ConnectorLifecycle(self.lifecycle),
            )
        if (
            self.network_enabled
            and self.lifecycle is not ConnectorLifecycle.READY_FOR_INTEGRATION
        ):
            raise ConnectorDefinitionError(
                "network access requires ready_for_integration lifecycle"
            )
        if (
            self.cost_model is ConnectorCostModel.OPTIONAL_PAID
            and not self.customer_supplied_credentials
        ):
            raise ConnectorDefinitionError(
                "optional paid connectors require customer-supplied credentials"
            )
        if self.paid_fallback_allowed:
            raise ConnectorDefinitionError(
                "automatic paid fallback is forbidden in the base product"
            )
        object.__setattr__(
            self,
            "notes",
            tuple(_clean_text(item, "notes[]") for item in self.notes),
        )
        if not isinstance(self.metadata, dict):
            raise ConnectorDefinitionError("metadata must be a dictionary")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "display_name": self.display_name,
            "family": self.family.value,
            "access_modes": [item.value for item in self.access_modes],
            "capabilities": [item.value for item in self.capabilities],
            "checkpoint_fields": [item.to_dict() for item in self.checkpoint_fields],
            "options": [item.to_dict() for item in self.options],
            "cost_model": self.cost_model.value,
            "policy_template": self.policy_template,
            "jurisdictions": list(self.jurisdictions),
            "allowed_domains": list(self.allowed_domains),
            "requires_site_profile": self.requires_site_profile,
            "customer_supplied_credentials": self.customer_supplied_credentials,
            "paid_fallback_allowed": self.paid_fallback_allowed,
            "lifecycle": self.lifecycle.value,
            "network_enabled": self.network_enabled,
            "notes": list(self.notes),
            "metadata": dict(self.metadata),
        }
