from __future__ import annotations

import json
from pathlib import Path

import pytest

from soika_uds.parsers.connectors import (
    CheckpointField,
    CheckpointKind,
    ConnectorAccess,
    ConnectorCapability,
    ConnectorCatalog,
    ConnectorCostModel,
    ConnectorDefinition,
    ConnectorDefinitionError,
    ConnectorFamily,
    ConnectorLifecycle,
    ConnectorOption,
    ConnectorRegistrationError,
    DiscoveryMode,
    HtmlSelectors,
    HtmlSourceKind,
    HtmlSourceProfile,
    OptionKind,
    prepared_connector_catalog,
    prepared_connector_definitions,
)


def _definition(**overrides: object) -> ConnectorDefinition:
    values: dict[str, object] = {
        "source_id": "example",
        "display_name": "Example",
        "family": ConnectorFamily.COMMUNITY_PLATFORM,
        "access_modes": (ConnectorAccess.PUBLIC_HTML,),
        "capabilities": (ConnectorCapability.DOCUMENTS,),
        "checkpoint_fields": (
            CheckpointField(
                name="next_url",
                kind=CheckpointKind.URL,
                description="Next page",
            ),
        ),
        "options": (
            ConnectorOption(
                name="urls",
                kind=OptionKind.STRING_ARRAY,
                description="Allowlisted URLs",
            ),
        ),
        "cost_model": ConnectorCostModel.FREE_PUBLIC,
        "policy_template": "configs/sources/source-policy-template.json",
        "allowed_domains": ("example.ru",),
    }
    values.update(overrides)
    return ConnectorDefinition(**values)


def _html_profile(**overrides: object) -> HtmlSourceProfile:
    values: dict[str, object] = {
        "source_id": "local-media.example",
        "display_name": "Example local media",
        "kind": HtmlSourceKind.LOCAL_MEDIA,
        "base_url": "https://example.ru/",
        "region": "Республика Татарстан",
        "municipalities": ("Казань",),
        "discovery_mode": DiscoveryMode.SITEMAP,
        "discovery_urls": ("https://example.ru/sitemap.xml",),
        "selectors": HtmlSelectors(
            title="h1",
            body="article",
            published_at="time[datetime]",
        ),
        "robots_url": "https://example.ru/robots.txt",
    }
    values.update(overrides)
    return HtmlSourceProfile(**values)


def test_prepared_catalog_contains_expected_russian_connectors() -> None:
    assert prepared_connector_catalog().source_ids() == (
        "dzen",
        "local-media",
        "municipal-public",
        "ok",
        "pikabu",
        "rutube",
        "vk",
    )


def test_youtube_is_not_in_russian_connector_perimeter() -> None:
    assert "youtube" not in prepared_connector_catalog().source_ids()


def test_all_prepared_connectors_are_ru_only() -> None:
    assert all(
        item.jurisdictions == ("RU",)
        for item in prepared_connector_definitions()
    )


def test_all_prepared_connectors_keep_network_disabled() -> None:
    catalog = prepared_connector_catalog()
    catalog.assert_preparation_only()
    assert not any(item.network_enabled for item in catalog.list())


def test_automatic_paid_fallback_is_forbidden() -> None:
    assert not any(
        item.paid_fallback_allowed for item in prepared_connector_definitions()
    )


def test_generic_html_connectors_require_site_profiles() -> None:
    catalog = prepared_connector_catalog()
    for source_id in ("local-media", "municipal-public"):
        item = catalog.get(source_id)
        assert item.requires_site_profile is True
        assert item.allowed_domains == ()


def test_social_connectors_use_official_api_blueprints() -> None:
    catalog = prepared_connector_catalog()
    assert catalog.get("vk").access_modes == (ConnectorAccess.OFFICIAL_API,)
    assert catalog.get("ok").access_modes == (ConnectorAccess.OFFICIAL_API,)


def test_duplicate_catalog_registration_is_rejected() -> None:
    definition = _definition()
    with pytest.raises(ConnectorRegistrationError):
        ConnectorCatalog((definition, definition))


def test_unknown_connector_is_rejected() -> None:
    with pytest.raises(ConnectorRegistrationError):
        prepared_connector_catalog().get("missing")


def test_foreign_jurisdiction_is_rejected() -> None:
    with pytest.raises(ConnectorDefinitionError, match="jurisdiction RU"):
        _definition(jurisdictions=("RU", "US"))


def test_ip_literal_domain_is_rejected() -> None:
    with pytest.raises(ConnectorDefinitionError, match="IP literal"):
        _definition(allowed_domains=("127.0.0.1",))


def test_network_cannot_be_enabled_before_integration_review() -> None:
    with pytest.raises(ConnectorDefinitionError, match="ready_for_integration"):
        _definition(network_enabled=True)


def test_ready_connector_may_declare_network_access() -> None:
    definition = _definition(
        lifecycle=ConnectorLifecycle.READY_FOR_INTEGRATION,
        network_enabled=True,
    )
    assert definition.network_enabled is True


def test_optional_paid_connector_requires_customer_credentials() -> None:
    with pytest.raises(ConnectorDefinitionError, match="customer-supplied"):
        _definition(cost_model=ConnectorCostModel.OPTIONAL_PAID)


def test_automatic_paid_fallback_cannot_be_enabled() -> None:
    with pytest.raises(ConnectorDefinitionError, match="paid fallback"):
        _definition(paid_fallback_allowed=True)


def test_duplicate_option_names_are_rejected() -> None:
    option = ConnectorOption(
        name="urls",
        kind=OptionKind.STRING_ARRAY,
        description="URLs",
    )
    with pytest.raises(ConnectorDefinitionError, match="options"):
        _definition(options=(option, option))


def test_duplicate_checkpoint_names_are_rejected() -> None:
    checkpoint = CheckpointField(
        name="cursor",
        kind=CheckpointKind.CURSOR,
        description="Cursor",
    )
    with pytest.raises(ConnectorDefinitionError, match="checkpoint_fields"):
        _definition(checkpoint_fields=(checkpoint, checkpoint))


def test_html_profile_is_disabled_and_ru_scoped_by_configuration() -> None:
    profile = _html_profile()
    assert profile.enabled is False
    assert profile.region == "Республика Татарстан"
    assert profile.municipalities == ("Казань",)


def test_html_profile_accepts_string_enum_values() -> None:
    profile = _html_profile(
        kind="local_media",
        discovery_mode="sitemap",
    )
    assert profile.kind is HtmlSourceKind.LOCAL_MEDIA
    assert profile.discovery_mode is DiscoveryMode.SITEMAP


def test_html_profile_rejects_http() -> None:
    with pytest.raises(ConnectorDefinitionError, match="HTTPS"):
        _html_profile(base_url="http://example.ru/")


def test_html_profile_rejects_cross_domain_discovery() -> None:
    with pytest.raises(ConnectorDefinitionError, match="source domain"):
        _html_profile(discovery_urls=("https://other.ru/sitemap.xml",))


def test_html_profile_cannot_be_enabled_during_preparation() -> None:
    with pytest.raises(ConnectorDefinitionError, match="must remain disabled"):
        _html_profile(enabled=True)


def test_comment_selectors_must_be_configured_together() -> None:
    with pytest.raises(ConnectorDefinitionError, match="configured together"):
        HtmlSelectors(
            title="h1",
            body="article",
            published_at="time",
            comment_item=".comment",
        )


def test_rendered_html_requires_justification() -> None:
    with pytest.raises(ConnectorDefinitionError, match="rendering_justification"):
        _html_profile(render_javascript=True)


def test_rendered_html_profile_accepts_explicit_justification() -> None:
    profile = _html_profile(
        render_javascript=True,
        rendering_justification="Comments are absent from the static HTML fixture.",
    )
    assert profile.render_javascript is True


def test_catalog_document_matches_python_definitions() -> None:
    path = Path("configs/connectors/prepared-connectors.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document == prepared_connector_catalog().to_dict()


def test_connector_schemas_are_valid_json_documents() -> None:
    schema_dir = Path("soika_uds/parsers/connectors/schemas")
    documents = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in schema_dir.glob("*.json")
    ]
    assert len(documents) == 2
    assert all(document["type"] == "object" for document in documents)


def test_html_templates_remain_disabled() -> None:
    for path in Path("configs/connectors").glob("*-site-template.json"):
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["enabled"] is False
