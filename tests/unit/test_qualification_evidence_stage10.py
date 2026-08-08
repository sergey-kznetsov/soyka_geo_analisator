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
        "report_digest": "1caf3948277aa83c89c9d87ba7eb78ec28fe116a04a3dec6f404b4990247b39a",
        "registry_digest": "15aebed2c9a26481671780aae284fde11d7fea12314beda3dccc6094a072f40c",
        "prediction_digest": "4b2dc32f527d5081476d2e726b52764d3474fc6f36b7ae4a4ca7cdf8a8203848",
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
