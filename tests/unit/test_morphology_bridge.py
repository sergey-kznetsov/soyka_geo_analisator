from __future__ import annotations


def test_legacy_pymorphy2_import_uses_pymorphy3():
    import pymorphy2
    import pymorphy3

    assert pymorphy2.MorphAnalyzer is pymorphy3.MorphAnalyzer
