from __future__ import annotations

import pymorphy2
import pymorphy3


def test_legacy_pymorphy2_import_uses_pymorphy3():
    assert pymorphy2.MorphAnalyzer is pymorphy3.MorphAnalyzer
