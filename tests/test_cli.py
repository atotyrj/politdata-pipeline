import json

import pytest

from politdata.change_set import create_change_set, save_change_set
from politdata.cli import main


def test_status_reads_change_set_without_mutating_it(tmp_path, capsys):
    path = tmp_path / "change.json"
    save_change_set(create_change_set(run_id="status-check"), path)
    before = path.read_bytes()

    assert main(["status", "--change-set", str(path), "--json"]) == 0

    assert path.read_bytes() == before
    result = json.loads(capsys.readouterr().out)
    assert result["run_id"] == "status-check"
    assert result["organization_changes"] == 0
    assert result["stages"]["qa"] == "pending"


def test_cli_refuses_missing_change_set_without_starting_ingestion(tmp_path):
    missing = tmp_path / "missing.json"
    with pytest.raises(SystemExit, match="will not start a RAW scan"):
        main(["downstream", "--change-set", str(missing)])


def test_preflight_cli_delegates_to_read_only_check(monkeypatch, capsys):
    monkeypatch.setattr(
        "politdata.cli.build_ingestion_preflight",
        lambda: {"mode": "read_only_preflight", "writes": 0},
    )
    assert main(["preflight", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["writes"] == 0
