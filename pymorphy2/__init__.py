"""Compatibility bridge for the legacy SOIKA geocoder.

The imported research code still executes ``import pymorphy2``.  The original
project is not compatible with Python 3.11, so this module deliberately exposes
the maintained :mod:`pymorphy3` API under the historical import name until the
geocoder is fully decomposed in the geolocation development stage.

New СОЙКА UDS Development code must import :mod:`pymorphy3` directly.
"""

from __future__ import annotations

from pymorphy3 import MorphAnalyzer

__all__ = ["MorphAnalyzer"]
