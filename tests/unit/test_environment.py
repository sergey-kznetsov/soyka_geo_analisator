from __future__ import annotations

from pathlib import Path

from soika_uds import environment


def _repository_fixture(root: Path) -> Path:
    data = root / "factfinder" / "src"
    data.mkdir(parents=True)
    (data / "exceptions_countries.csv").write_text("header\n", encoding="utf-8")
    (data / "exсeptions_city.csv").write_text("header\n", encoding="utf-8")
    return root


def test_liveness_payload_is_lightweight():
    payload = environment.liveness_payload()
    assert payload["status"] == "alive"
    assert payload["service"] == "soika-uds-development"


def test_readiness_uses_writable_directories(monkeypatch, tmp_path):
    repository = _repository_fixture(tmp_path / "repository")
    monkeypatch.setattr(environment, "SYSTEM_LIBRARIES", ())
    monkeypatch.setenv("SOIKA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOIKA_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("SOIKA_REQUIRE_CUDA", "false")

    payload = environment.readiness_payload(repository_root=repository)

    assert payload["status"] == "ready"
    checks = {item["name"]: item for item in payload["checks"]}
    assert checks["python"]["ok"] is True
    assert checks["storage"]["ok"] is True
    assert checks["model-cache"]["ok"] is True
    assert checks["cuda"]["required"] is False


def test_required_cuda_failure_is_reported(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "torch", None)
    check = environment._cuda_check(required=True)
    assert check.ok is False
    assert check.required is True
