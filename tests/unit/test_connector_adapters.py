from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from soika_uds.parsers import InMemoryParserCheckpointStore, ParserRequest, ParserRunner
from soika_uds.parsers.connectors import (
    OkApiCredentials,
    OkMd5Signer,
    build_prepared_parser_registry,
)
from tests.unit.connector_fixture_support import (
    FixtureTransport,
    NOW,
    PURPOSE,
    compliance_context,
    connector_policies,
    connector_requests,
)


def build_registry():
    return build_prepared_parser_registry(
        connector_policies(),
        ok_signer=OkMd5Signer(OkApiCredentials("app", "secret", "token")),
    )


def test_all_prepared_adapters_register_in_existing_registry() -> None:
    registry = build_registry()
    assert tuple(item.source_id for item in registry.list_policies()) == (
        "dzen",
        "local-media",
        "municipal-public",
        "ok",
        "pikabu",
        "rutube",
        "vk",
    )


def test_fixture_suite_runs_through_parser_runner_for_all_sources() -> None:
    source_policies = connector_policies()
    transport = FixtureTransport()
    runner = ParserRunner(
        build_prepared_parser_registry(
            source_policies,
            ok_signer=OkMd5Signer(OkApiCredentials("app", "secret", "token")),
        ),
        InMemoryParserCheckpointStore(),
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
        sleep=lambda seconds: None,
        transport_factory=lambda policy_value: transport,
    )
    results = {}
    for source_id, request in connector_requests().items():
        result = runner.run(request, compliance_context(source_policies[source_id]))
        results[source_id] = result
        assert result.coverage.messages_emitted >= 1
        assert result.messages
        assert all(message.source == source_id for message in result.messages)
    assert set(results) == set(source_policies)
    assert results["vk"].coverage.messages_emitted == 2
    assert results["municipal-public"].coverage.pages_collected == 2
    assert {urlsplit(url).hostname for url in transport.urls} >= {
        "api.vk.com",
        "api.ok.ru",
        "media.example.ru",
        "municipal.example.ru",
        "dzen.ru",
        "pikabu.ru",
        "rutube.ru",
    }


def test_ok_signatures_are_deterministic_and_do_not_expose_secret() -> None:
    signer = OkMd5Signer(OkApiCredentials("app", "private-secret", "token"))
    first = signer.signed_parameters(
        {"method": "discussions.getComments", "count": 10}
    )
    second = signer.signed_parameters(
        {"count": 10, "method": "discussions.getComments"}
    )
    assert first == second
    assert first["sig"]
    assert "private-secret" not in str(first)


def test_html_connector_rejects_cross_domain_request() -> None:
    adapter = build_registry().get("dzen")
    request = ParserRequest(
        analysis_id="cross-domain",
        source_id="dzen",
        purpose=PURPOSE,
        territory={"city": "Казань"},
        options={"urls": ["https://example.com/"]},
    )
    services = type("Services", (), {"transport": FixtureTransport()})()
    with pytest.raises(Exception, match="allowlist"):
        adapter.fetch_page(request, None, services)


def test_controlled_external_targets_cover_all_sources_once() -> None:
    targets = json.loads(
        Path("configs/connectors/external-probe-targets.json").read_text(
            encoding="utf-8"
        )
    )
    assert [item["source_id"] for item in targets] == [
        "vk",
        "ok",
        "local-media",
        "municipal-public",
        "dzen",
        "pikabu",
        "rutube",
    ]
    assert all(str(item["url"]).startswith("https://") for item in targets)
