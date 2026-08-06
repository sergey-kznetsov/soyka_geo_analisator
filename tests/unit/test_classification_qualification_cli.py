from __future__ import annotations

import json
from pathlib import Path

from soika_uds.classification.qualification_cli import main


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def audit_path() -> Path:
    return (
        repository_root()
        / "configs"
        / "classification"
        / "stage8b-legacy-qualification.json"
    )


def test_cli_writes_deterministic_blocked_report(tmp_path: Path) -> None:
    output = tmp_path / "qualification-report.json"
    exit_code = main(
        [
            "--input",
            str(audit_path()),
            "--output",
            str(output),
            "--strict",
        ]
    )
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 2
    assert payload["approved_for_production"] is False
    assert "model.topic.repository" in payload["blockers"]
    assert len(payload["report_digest"]) == 64


def test_cli_non_strict_mode_reports_without_failure(capsys) -> None:
    exit_code = main(["--input", str(audit_path())])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["approved_for_production"] is False
