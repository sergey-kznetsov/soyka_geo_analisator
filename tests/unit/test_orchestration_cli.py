import json
from datetime import UTC, datetime

from soika_uds.cli import main
from soika_uds.contracts import TerritoryContext
from soika_uds.integration import AnalysisRequestV1
from soika_uds.orchestration import FileJobStore, JobRecord


def make_record():
    request = AnalysisRequestV1(
        analysis_id="analysis-cli",
        requested_at=datetime(2026, 8, 5, 8, 0, tzinfo=UTC),
        territory=TerritoryContext(
            analysis_id="analysis-cli",
            city="Ижевск",
            address="Пушкинская улица, 277",
        ),
    )
    return JobRecord.new(request, datetime(2026, 8, 5, 8, 0, tzinfo=UTC))


def test_jobs_list_reads_durable_state(tmp_path, capsys):
    FileJobStore(tmp_path).create(make_record())

    exit_code = main(["jobs", "--state-dir", str(tmp_path), "list"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["analysis_id"] == "analysis-cli"
    assert payload[0]["status"] == "queued"


def test_jobs_status_returns_contract_status(tmp_path, capsys):
    FileJobStore(tmp_path).create(make_record())

    exit_code = main(
        [
            "jobs",
            "--state-dir",
            str(tmp_path),
            "status",
            "--analysis-id",
            "analysis-cli",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["analysis_id"] == "analysis-cli"
    assert payload["progress_percent"] == 0


def test_jobs_missing_record_returns_orchestration_error(tmp_path, capsys):
    exit_code = main(
        [
            "jobs",
            "--state-dir",
            str(tmp_path),
            "status",
            "--analysis-id",
            "missing",
        ]
    )

    assert exit_code == 3
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"]["code"] == "ORCHESTRATION_ERROR"
