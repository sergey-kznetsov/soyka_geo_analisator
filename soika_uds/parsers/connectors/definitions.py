"""Prepared connector definitions for the Russian source perimeter."""

from __future__ import annotations

from .catalog import ConnectorCatalog
from .models import (
    CheckpointField,
    CheckpointKind,
    ConnectorAccess,
    ConnectorCapability,
    ConnectorCostModel,
    ConnectorDefinition,
    ConnectorFamily,
    ConnectorLifecycle,
    ConnectorOption,
    OptionKind,
)

_POLICY_TEMPLATE = "configs/sources/source-policy-template.json"


def _option(
    name: str,
    kind: OptionKind,
    description: str,
    *,
    required: bool = False,
) -> ConnectorOption:
    return ConnectorOption(
        name=name,
        kind=kind,
        required=required,
        description=description,
    )


def _checkpoint(
    name: str,
    kind: CheckpointKind,
    description: str,
    *,
    required: bool = False,
) -> CheckpointField:
    return CheckpointField(
        name=name,
        kind=kind,
        required=required,
        description=description,
    )


def prepared_connector_definitions() -> tuple[ConnectorDefinition, ...]:
    return (
        ConnectorDefinition(
            source_id="vk",
            display_name="ВКонтакте",
            family=ConnectorFamily.SOCIAL_NETWORK,
            access_modes=(ConnectorAccess.OFFICIAL_API,),
            capabilities=(
                ConnectorCapability.DOCUMENTS,
                ConnectorCapability.COMMENTS,
                ConnectorCapability.REPLIES,
                ConnectorCapability.EDIT_MARKERS,
                ConnectorCapability.DELETION_MARKERS,
                ConnectorCapability.SEARCH,
                ConnectorCapability.GEO_TAGS,
                ConnectorCapability.ATTACHMENTS,
            ),
            checkpoint_fields=(
                _checkpoint("offset", CheckpointKind.OFFSET, "API result offset"),
                _checkpoint(
                    "post_cursor",
                    CheckpointKind.CURSOR,
                    "Current post cursor",
                ),
            ),
            options=(
                _option(
                    "community_ids",
                    OptionKind.STRING_ARRAY,
                    "Allowlisted public community identifiers",
                    required=True,
                ),
                _option(
                    "query_terms",
                    OptionKind.STRING_ARRAY,
                    "Territory search terms",
                ),
                _option(
                    "include_replies",
                    OptionKind.BOOLEAN,
                    "Collect nested public replies",
                ),
                _option("api_version", OptionKind.STRING, "Pinned VK API version"),
            ),
            cost_model=ConnectorCostModel.FREE_OFFICIAL_API,
            policy_template=_POLICY_TEMPLATE,
            allowed_domains=("api.vk.com", "vk.com"),
            customer_supplied_credentials=True,
            lifecycle=ConnectorLifecycle.REVIEW_REQUIRED,
            notes=(
                "Use only official API methods and an application-owned credential.",
                "No browser-session emulation or hidden endpoints.",
            ),
            metadata={"priority": 1, "territory": "Russian Federation"},
        ),
        ConnectorDefinition(
            source_id="ok",
            display_name="Одноклассники",
            family=ConnectorFamily.SOCIAL_NETWORK,
            access_modes=(ConnectorAccess.OFFICIAL_API,),
            capabilities=(
                ConnectorCapability.DOCUMENTS,
                ConnectorCapability.COMMENTS,
                ConnectorCapability.REPLIES,
                ConnectorCapability.DELETION_MARKERS,
                ConnectorCapability.ATTACHMENTS,
            ),
            checkpoint_fields=(
                _checkpoint("anchor", CheckpointKind.CURSOR, "Discussion page anchor"),
                _checkpoint(
                    "discussion_cursor",
                    CheckpointKind.CURSOR,
                    "Discussion cursor",
                ),
            ),
            options=(
                _option(
                    "discussion_ids",
                    OptionKind.STRING_ARRAY,
                    "Allowlisted public discussion identifiers",
                    required=True,
                ),
                _option(
                    "discussion_types",
                    OptionKind.STRING_ARRAY,
                    "Approved public discussion types",
                    required=True,
                ),
                _option(
                    "include_removed",
                    OptionKind.BOOLEAN,
                    "Request removal markers",
                ),
            ),
            cost_model=ConnectorCostModel.FREE_OFFICIAL_API,
            policy_template=_POLICY_TEMPLATE,
            allowed_domains=("api.ok.ru", "ok.ru"),
            customer_supplied_credentials=True,
            lifecycle=ConnectorLifecycle.REVIEW_REQUIRED,
            notes=(
                "Requires an approved application session and documented "
                "access rights.",
            ),
            metadata={"priority": 2, "territory": "Russian Federation"},
        ),
        ConnectorDefinition(
            source_id="local-media",
            display_name="Локальные СМИ РФ",
            family=ConnectorFamily.LOCAL_MEDIA,
            access_modes=(
                ConnectorAccess.PUBLIC_HTML,
                ConnectorAccess.RSS_ATOM,
                ConnectorAccess.SITEMAP,
                ConnectorAccess.PARTNER_EXPORT,
            ),
            capabilities=(
                ConnectorCapability.DOCUMENTS,
                ConnectorCapability.COMMENTS,
                ConnectorCapability.REPLIES,
                ConnectorCapability.EDIT_MARKERS,
                ConnectorCapability.DELETION_MARKERS,
                ConnectorCapability.RSS_DISCOVERY,
                ConnectorCapability.SITEMAP_DISCOVERY,
                ConnectorCapability.HTML_DISCOVERY,
                ConnectorCapability.PARTNER_EXPORT,
            ),
            checkpoint_fields=(
                _checkpoint(
                    "last_seen_at",
                    CheckpointKind.TIMESTAMP,
                    "Latest processed publication time",
                ),
                _checkpoint("next_url", CheckpointKind.URL, "Next listing or feed URL"),
            ),
            options=(
                _option(
                    "site_profile",
                    OptionKind.OBJECT,
                    "Reviewed local-media site profile",
                    required=True,
                ),
                _option(
                    "include_comments",
                    OptionKind.BOOLEAN,
                    "Collect public comments",
                ),
            ),
            cost_model=ConnectorCostModel.FREE_PUBLIC,
            policy_template=_POLICY_TEMPLATE,
            requires_site_profile=True,
            lifecycle=ConnectorLifecycle.PREPARED,
            notes=(
                "Every domain requires its own policy, robots decision and selectors.",
                "RSS and sitemap are discovery aids; article and comment "
                "content may come from HTML.",
            ),
            metadata={"priority": 3, "territory": "Russian Federation"},
        ),
        ConnectorDefinition(
            source_id="municipal-public",
            display_name="Муниципальные публичные источники РФ",
            family=ConnectorFamily.MUNICIPAL,
            access_modes=(
                ConnectorAccess.PUBLIC_HTML,
                ConnectorAccess.RSS_ATOM,
                ConnectorAccess.SITEMAP,
                ConnectorAccess.PARTNER_EXPORT,
            ),
            capabilities=(
                ConnectorCapability.DOCUMENTS,
                ConnectorCapability.COMMENTS,
                ConnectorCapability.EDIT_MARKERS,
                ConnectorCapability.DELETION_MARKERS,
                ConnectorCapability.GEO_TAGS,
                ConnectorCapability.ATTACHMENTS,
                ConnectorCapability.RSS_DISCOVERY,
                ConnectorCapability.SITEMAP_DISCOVERY,
                ConnectorCapability.HTML_DISCOVERY,
                ConnectorCapability.PARTNER_EXPORT,
            ),
            checkpoint_fields=(
                _checkpoint(
                    "last_seen_at",
                    CheckpointKind.TIMESTAMP,
                    "Latest processed item time",
                ),
                _checkpoint("next_url", CheckpointKind.URL, "Next public listing URL"),
            ),
            options=(
                _option(
                    "site_profile",
                    OptionKind.OBJECT,
                    "Reviewed municipal site profile",
                    required=True,
                ),
                _option(
                    "public_sections",
                    OptionKind.STRING_ARRAY,
                    "Allowlisted public sections",
                    required=True,
                ),
            ),
            cost_model=ConnectorCostModel.FREE_PUBLIC,
            policy_template=_POLICY_TEMPLATE,
            requires_site_profile=True,
            lifecycle=ConnectorLifecycle.PREPARED,
            notes=(
                "Non-public citizen appeals require a partner export and "
                "separate legal basis.",
            ),
            metadata={"priority": 4, "territory": "Russian Federation"},
        ),
        ConnectorDefinition(
            source_id="dzen",
            display_name="Дзен",
            family=ConnectorFamily.COMMUNITY_PLATFORM,
            access_modes=(ConnectorAccess.PUBLIC_HTML,),
            capabilities=(
                ConnectorCapability.DOCUMENTS,
                ConnectorCapability.COMMENTS,
                ConnectorCapability.REPLIES,
                ConnectorCapability.HTML_DISCOVERY,
            ),
            checkpoint_fields=(
                _checkpoint(
                    "next_url",
                    CheckpointKind.URL,
                    "Next reviewed public page",
                ),
            ),
            options=(
                _option(
                    "channel_urls",
                    OptionKind.STRING_ARRAY,
                    "Allowlisted local-media channel URLs",
                    required=True,
                ),
            ),
            cost_model=ConnectorCostModel.FREE_PUBLIC,
            policy_template=_POLICY_TEMPLATE,
            allowed_domains=("dzen.ru",),
            lifecycle=ConnectorLifecycle.REVIEW_REQUIRED,
            notes=(
                "No live adapter until permitted collection mechanics are confirmed.",
            ),
            metadata={"priority": 5, "territory": "Russian Federation"},
        ),
        ConnectorDefinition(
            source_id="pikabu",
            display_name="Пикабу",
            family=ConnectorFamily.COMMUNITY_PLATFORM,
            access_modes=(ConnectorAccess.PUBLIC_HTML,),
            capabilities=(
                ConnectorCapability.DOCUMENTS,
                ConnectorCapability.COMMENTS,
                ConnectorCapability.REPLIES,
                ConnectorCapability.EDIT_MARKERS,
                ConnectorCapability.DELETION_MARKERS,
                ConnectorCapability.HTML_DISCOVERY,
            ),
            checkpoint_fields=(
                _checkpoint(
                    "next_url",
                    CheckpointKind.URL,
                    "Next reviewed public page",
                ),
            ),
            options=(
                _option(
                    "tags",
                    OptionKind.STRING_ARRAY,
                    "Approved locality and issue tags",
                ),
                _option(
                    "community_urls",
                    OptionKind.STRING_ARRAY,
                    "Allowlisted communities",
                ),
            ),
            cost_model=ConnectorCostModel.FREE_PUBLIC,
            policy_template=_POLICY_TEMPLATE,
            allowed_domains=("pikabu.ru",),
            lifecycle=ConnectorLifecycle.REVIEW_REQUIRED,
            notes=(
                "Connector must stop on CAPTCHA or anti-bot challenge.",
                "Public availability is not treated as permission to retain "
                "personal identifiers.",
            ),
            metadata={"priority": 6, "territory": "Russian Federation"},
        ),
        ConnectorDefinition(
            source_id="rutube",
            display_name="RUTUBE",
            family=ConnectorFamily.VIDEO_PLATFORM,
            access_modes=(ConnectorAccess.PUBLIC_HTML,),
            capabilities=(
                ConnectorCapability.DOCUMENTS,
                ConnectorCapability.COMMENTS,
                ConnectorCapability.REPLIES,
                ConnectorCapability.HTML_DISCOVERY,
            ),
            checkpoint_fields=(
                _checkpoint(
                    "next_url",
                    CheckpointKind.URL,
                    "Next reviewed public video page",
                ),
            ),
            options=(
                _option(
                    "channel_urls",
                    OptionKind.STRING_ARRAY,
                    "Allowlisted regional and municipal channels",
                    required=True,
                ),
            ),
            cost_model=ConnectorCostModel.FREE_PUBLIC,
            policy_template=_POLICY_TEMPLATE,
            allowed_domains=("rutube.ru",),
            lifecycle=ConnectorLifecycle.REVIEW_REQUIRED,
            notes=(
                "Use only a documented public interface or an approved partner export.",
            ),
            metadata={"priority": 7, "territory": "Russian Federation"},
        ),
    )


def prepared_connector_catalog() -> ConnectorCatalog:
    catalog = ConnectorCatalog(prepared_connector_definitions())
    catalog.assert_preparation_only()
    return catalog
