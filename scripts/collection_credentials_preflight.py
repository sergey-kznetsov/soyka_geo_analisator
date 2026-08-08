"""Validate configured collection credentials without printing secret values.

This is a deployment preflight, not an end-to-end collection acceptance test.
Telegram is intentionally policy-blocked under the current platform terms.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from soika_uds.discovery import (
    GeoScope,
    SourceReasonCode,
    SourceState,
    TwoGisPlacesEnricher,
    build_yandex_search_provider_from_env,
)
from soika_uds.discovery.providers import SearchProviderError


TEST_SCOPE = GeoScope(
    raw_address="Ижевск Пушкинская 277",
    city="Ижевск",
    region="Удмуртская Республика",
    district="Октябрьский район",
    street="Пушкинская улица",
    house_number="277",
    longitude=53.2072056,
    latitude=56.8665403,
    precision="house",
    confidence=1.0,
    candidate_id="credentials-preflight",
    label="Ижевск, Пушкинская улица, 277",
)


def _present(name: str) -> bool:
    value = os.getenv(name)
    return bool(value and value.strip())


def _yandex() -> dict[str, Any]:
    provider = build_yandex_search_provider_from_env()
    try:
        hits = provider.search('"Пушкинская 277" Ижевск', limit=1)
    except SearchProviderError as error:
        return {
            "state": "failed",
            "reason_code": error.code.value,
            "reason": str(error),
            "retryable": error.retryable,
            "results": 0,
        }
    return {
        "state": "available",
        "reason_code": SourceReasonCode.NONE.value,
        "reason": "Yandex Search API accepted a Russian web-search request",
        "results": len(hits),
    }


def _two_gis() -> dict[str, Any]:
    key = os.getenv("TWO_GIS_API_KEY")
    result = TwoGisPlacesEnricher(key).enrich(TEST_SCOPE)
    outcome = result.outcomes[0]
    return {
        "state": outcome.state.value,
        "reason_code": outcome.reason_code.value,
        "reason": outcome.reason,
        "places": len(result.places),
        "review_texts_available": False,
    }


def _telegram() -> dict[str, Any]:
    credential_names = (
        "TELEGRAM_API_ID",
        "TELEGRAM_API_HASH",
        "TELEGRAM_STRING_SESSION",
    )
    return {
        "state": SourceState.BLOCKED.value,
        "reason_code": SourceReasonCode.TERMS_RESTRICTED.value,
        "reason": (
            "Telegram collection is not activated: current Telegram API and Content "
            "Licensing terms restrict scraping/aggregation for AI/ML deployment"
        ),
        "credentials_present": all(_present(name) for name in credential_names),
        "credentials_tested": False,
    }


def main() -> int:
    report = {
        "purpose": "credential_preflight_only",
        "acceptance_test": False,
        "secrets_redacted": True,
        "yandex_search": _yandex(),
        "two_gis": _two_gis(),
        "telegram": _telegram(),
    }
    output = Path(os.getenv("SOIKA_PREFLIGHT_OUTPUT", "collection-credentials-preflight.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))

    required_states = {
        report["yandex_search"]["state"],
        report["two_gis"]["state"],
    }
    bad_states = {
        SourceState.CONFIGURATION_MISSING.value,
        SourceState.UNAVAILABLE.value,
        SourceState.BLOCKED.value,
        SourceState.FAILED.value,
        "failed",
    }
    return 1 if required_states & bad_states else 0


if __name__ == "__main__":
    sys.exit(main())
