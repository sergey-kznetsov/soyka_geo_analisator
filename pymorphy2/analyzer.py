"""Module-level compatibility for packages importing ``pymorphy2.analyzer``.

Natasha 1.6.0 imports :class:`Parse` and :class:`MorphAnalyzer` from this
historical module path.  Python 3.11 production uses the maintained pymorphy3
implementation, whose analyzer API and result-type hook remain compatible.
"""

from __future__ import annotations

from pymorphy3.analyzer import MorphAnalyzer, Parse

__all__ = ["MorphAnalyzer", "Parse"]
