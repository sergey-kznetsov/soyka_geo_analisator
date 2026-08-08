"""Environment/secret-file configuration for geo-first external services."""

from __future__ import annotations

import os
from pathlib import Path

from .providers import UnavailableSearchProvider, YandexSearchProvider


def read_secret_value(*, direct_env: str, file_env: str) -> str | None:
    """Read a secret file first, then an environment variable without logging either."""

    path = os.getenv(file_env)
    if path:
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value or None
    value = os.getenv(direct_env)
    return value.strip() if value and value.strip() else None


def build_yandex_search_provider_from_env():
    api_key = read_secret_value(
        direct_env="YANDEX_SEARCH_API_KEY",
        file_env="YANDEX_SEARCH_API_KEY_FILE",
    )
    folder_id = read_secret_value(
        direct_env="YANDEX_SEARCH_FOLDER_ID",
        file_env="YANDEX_SEARCH_FOLDER_ID_FILE",
    )
    if not api_key or not folder_id:
        return UnavailableSearchProvider(
            "Yandex Search API key or folder ID is not configured"
        )
    return YandexSearchProvider(folder_id=folder_id, api_key=api_key)


__all__ = ["build_yandex_search_provider_from_env", "read_secret_value"]
