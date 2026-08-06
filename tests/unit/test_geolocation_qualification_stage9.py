from __future__ import annotations

import json
from pathlib import Path

import pytest

from pymorphy2.analyzer import MorphAnalyzer as LegacyModuleMorphAnalyzer
from pymorphy2.analyzer import Parse as LegacyModuleParse
from pymorphy3.analyzer import MorphAnalyzer, Parse
from soika_uds.geolocation.qualification_api import (
    GateState,
    load_model_audit,
    load_qualified_registry,
    load_validation_manifest,
    qualify_geolocation,
)

ROOT = Path(__file__).resolve().parents[2]
AUDIT_PATH = ROOT / "models" / "geolocation_model_audit_v1.json"
VALIDATION_PATH = ROOT / "models" / "geolocation_validation_v1.json"


def _passing_metrics(*, model_smoke_passed: bool = True) -> dict:
    cities = {
        city: {
            "samples": 8,
            "resolved": 8,
            "within_tolerance": 8,
            "kind_matches": 8,
            "resolution_rate": 1.0,
            "within_tolerance_rate": 1.0,
            "kind_accuracy": 1.0,
        }
        for city in ("Казань", "Москва", "Санкт-Петербург")
    }
    return {
        "samples": 24,
        "resolved": 24,
        "resolution_rate": 1.0,
        "within_tolerance_rate": 1.0,
        "kind_accuracy": 1.0,
        "median_distance_m": 20.0,
        "p95_distance_m": 90.0,
        "cities": cities,
        "extraction_exact_rate": 1.0,
        "low_confidence_rate": 0.0,
        "model_smoke_passed": model_smoke_passed,
    }


def test_natasha_module_level_pymorphy2_bridge_uses_pymorphy3() -> None:
    assert LegacyModuleMorphAnalyzer is MorphAnalyzer
    assert LegacyModuleParse is Parse


def test_committed_validation_manifest_has_stable_digest() -> None:
    payload = json.loads(VALIDATION_PATH.read_text(encoding="utf-8"))
    manifest = load_validation_manifest(VALIDATION_PATH)
    assert manifest.digest == payload["declared_digest"]
    assert manifest.counts_by_city == {
        "Казань": 8,
        "Москва": 8,
        "Санкт-Петербург": 8,
    }
    assert {item.value for item in manifest.approved_levels} == {
        "house",
        "poi",
        "landmark",
    }


def test_model_audit_is_complete_and_immutable() -> None:
    audit = load_model_audit(AUDIT_PATH)
    assert audit.approved is True
    assert audit.component_version == "1.6.0"
    assert len(audit.artifact_sha256) == 64


def test_qualification_derives_registry_only_from_passed_gates(tmp_path: Path) -> None:
    audit = load_model_audit(AUDIT_PATH)
    validation = load_validation_manifest(VALIDATION_PATH)
    report = qualify_geolocation(
        model_audit=audit,
        validation=validation,
        metrics=_passing_metrics(),
    )
    assert report.approved_for_production is True
    assert all(gate.state is GateState.PASSED for gate in report.gates)
    registry = report.registry_dict()
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    loaded = load_qualified_registry(registry_path)
    assert loaded["registry_digest"] == registry["registry_digest"]
    assert loaded["validation"]["digest"] == validation.digest


def test_missing_model_smoke_blocks_activation() -> None:
    audit = load_model_audit(AUDIT_PATH)
    validation = load_validation_manifest(VALIDATION_PATH)
    report = qualify_geolocation(
        model_audit=audit,
        validation=validation,
        metrics=_passing_metrics(model_smoke_passed=False),
    )
    assert report.approved_for_production is False
    assert next(gate for gate in report.gates if gate.code == "MODEL_SMOKE").state is (
        GateState.BLOCKED
    )
    with pytest.raises(ValueError, match="blocked qualification"):
        report.registry_dict()


def test_registry_loader_rejects_tampering(tmp_path: Path) -> None:
    audit = load_model_audit(AUDIT_PATH)
    validation = load_validation_manifest(VALIDATION_PATH)
    report = qualify_geolocation(
        model_audit=audit,
        validation=validation,
        metrics=_passing_metrics(),
    )
    registry = report.registry_dict()
    registry["profile_id"] = "tampered"
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        load_qualified_registry(path)
