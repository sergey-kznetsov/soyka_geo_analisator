from __future__ import annotations

import pymorphy3

import pymorphy2


def test_legacy_pymorphy2_import_uses_pymorphy3():
    assert pymorphy2.MorphAnalyzer is pymorphy3.MorphAnalyzer
