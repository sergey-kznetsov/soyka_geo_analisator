"""Lazy model lifecycle for geolocation extractors."""

from __future__ import annotations

from collections.abc import Callable
from threading import RLock
from typing import Any


class LazyModelManager:
    """Load each heavy model once, on first use, under a lock."""

    def __init__(self) -> None:
        self._models: dict[str, Any] = {}
        self._lock = RLock()

    def get(self, key: str, loader: Callable[[], Any]) -> Any:
        if not isinstance(key, str) or not key.strip():
            raise ValueError("model key must be non-empty")
        with self._lock:
            if key not in self._models:
                self._models[key] = loader()
            return self._models[key]

    def clear(self) -> None:
        with self._lock:
            self._models.clear()
