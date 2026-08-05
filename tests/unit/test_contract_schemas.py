import json
from pathlib import Path

from soika_uds.integration import (
    SCHEMA_FILES,
    AnalysisRequestV1,
    AnalysisResultV1,
    JobStatusV1,
    contract_info,
    export_schema_bundle,
    load_schema_bundle,
    schema_bundle_digest,
)

EXAMPLE_ROOT = Path("examples/contracts/v1")


def test_schema_bundle_uses_one_draft_and_strict_envelopes():
    bundle = load_schema_bundle()

    assert tuple(bundle) == SCHEMA_FILES
    for name, schema in bundle.items():
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"].startswith("https://schemas.soika-uds.dev/1.0.0/")
        if name != "common.schema.json":
            assert schema["type"] == "object"
            assert schema["additionalProperties"] is False


def test_schema_digest_is_deterministic():
    assert schema_bundle_digest() == schema_bundle_digest()
    assert len(schema_bundle_digest()) == 64


def test_contract_info_lists_all_public_schemas():
    info = contract_info()

    assert info["supported_versions"] == ["1.0.0"]
    assert info["schemas"] == list(SCHEMA_FILES)
    assert info["schema_digest"] == schema_bundle_digest()


def test_exported_schema_bundle_matches_packaged_bundle(tmp_path):
    exported = export_schema_bundle(tmp_path)

    assert [path.name for path in exported] == list(SCHEMA_FILES)
    for path in exported:
        assert (
            json.loads(path.read_text(encoding="utf-8"))
            == load_schema_bundle()[path.name]
        )


def test_documented_examples_are_accepted_by_python_contract():
    request = json.loads(
        (EXAMPLE_ROOT / "analysis-request.json").read_text(encoding="utf-8")
    )
    status = json.loads((EXAMPLE_ROOT / "job-status.json").read_text(encoding="utf-8"))
    result = json.loads(
        (EXAMPLE_ROOT / "analysis-result.json").read_text(encoding="utf-8")
    )

    assert AnalysisRequestV1.from_dict(request).analysis_id == "analysis-2026-0001"
    assert JobStatusV1.from_dict(status).progress_percent == 62
    assert AnalysisResultV1.from_dict(result).partial is True
