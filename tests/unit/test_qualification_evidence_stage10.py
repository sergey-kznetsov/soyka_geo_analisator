from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_geolocation_evidence.py"
SPEC = importlib.util.spec_from_file_location("verify_geolocation_evidence", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_committed_geolocation_evidence_is_consistent() -> None:
    summary = MODULE.verify_files()

    assert summary == {
        "report_digest": "0d68d8c102da8548703b0468e64f429cd9c9a8d0a210aa40674a55c441bbfd73",
        "registry_digest": "6f3be8ddd720bce2b29183a44640dacccac24fd3a9146ec7c3c2c9605b586da9",
        "prediction_digest": "141228c900845bc300ebfff79705cc06caf2adb83aeec05ccd5b0f39348d6bf2",
        "validation_digest": "67a9573b285f0a8343f9e966fd1951b2fc1a9a3c5f36d8f72aae140b8d791685",
        "samples": 24,
    }


def test_blocked_gate_invalidates_committed_evidence() -> None:
    report = MODULE._load(MODULE.DEFAULT_REPORT)
    registry = MODULE._load(MODULE.DEFAULT_REGISTRY)
    validation = MODULE._load(MODULE.DEFAULT_VALIDATION)
    audit = MODULE._load(MODULE.DEFAULT_AUDIT)
    tampered = copy.deepcopy(report)
    tampered["gates"][0]["state"] = "blocked"
    tampered["report_digest"] = MODULE._digest(tampered, "report_digest")
    registry["qualification_report_digest"] = tampered["report_digest"]
    registry["registry_digest"] = MODULE._digest(registry, "registry_digest")

    with pytest.raises(MODULE.EvidenceVerificationError, match="not all"):
        MODULE.verify_payloads(
            report=tampered,
            registry=registry,
            validation=validation,
            audit=audit,
        )
