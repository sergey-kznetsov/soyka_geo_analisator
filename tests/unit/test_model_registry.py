from __future__ import annotations

import json

from soika_uds.model_registry import load_manifest, tree_digest, verify_models


def test_default_manifest_is_valid():
    models = load_manifest()
    assert {model.name for model in models} >= {
        "category-classifier",
        "topic-classifier",
        "address-ner",
        "rubert-tokenizer",
    }


def test_tree_digest_is_deterministic(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text('{"a": 1}', encoding="utf-8")
    first = tree_digest(model)
    second = tree_digest(model)
    assert first == second


def test_verify_models_detects_changes(tmp_path):
    model = tmp_path / "category"
    model.mkdir()
    config = model / "config.json"
    config.write_text("initial", encoding="utf-8")
    expected = tree_digest(model)
    (tmp_path / "installed-models.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "models": [
                    {
                        "name": "category",
                        "path": str(model),
                        "sha256": expected,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert verify_models(tmp_path)["ok"] is True
    config.write_text("modified", encoding="utf-8")
    assert verify_models(tmp_path)["ok"] is False
