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


def test_ingest_cli_requires_explicit_limit():
    with pytest.raises(SystemExit):
        main(["ingest"])


def test_ingest_cli_passes_independent_report_queue_limits(monkeypatch, capsys):
    captured = {}

    def run(**kwargs):
        captured.update(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr("politdata.cli.run_limited_organization_ingestion", run)

    assert main([
        "ingest",
        "--organization-limit", "3",
        "--report-discovery-limit", "5",
        "--report-limit", "7",
        "--report-refresh-interval-days", "2",
        "--skip-downstream",
        "--json",
    ]) == 0

    assert json.loads(capsys.readouterr().out)["status"] == "ok"
    assert captured["organization_limit"] == 3
    assert captured["report_discovery_limit"] == 5
    assert captured["report_limit"] == 7
    assert captured["report_refresh_interval_days"] == 2
    assert captured["run_downstream"] is False


def test_unified_run_cli_supports_read_only_full_plan(capsys):
    assert main([
        "run",
        "--mode", "full-replace",
        "--dry-run",
        "--json",
    ]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "planned"
    assert result["writes"] == 0
    assert result["network_requests"] == 0
    assert result["config"]["mode"] == "full-replace"


def test_unified_incremental_run_requires_explicit_limit():
    with pytest.raises(SystemExit, match="organization_limit"):
        main(["run", "--mode", "incremental", "--dry-run"])


def test_restore_latest_cli_delegates_to_checksum_verifying_store(
    tmp_path, monkeypatch, capsys
):
    calls = []

    class Store:
        def __init__(self, generation_root, latest_path):
            calls.append(("init", generation_root, latest_path))

        def read_latest(self):
            return {"generation_id": "g1"}

        def restore_latest(self, destination):
            calls.append(("restore", destination))
            return str(destination)

    monkeypatch.setattr("politdata.cli.LocalGenerationStore", Store)
    destination = tmp_path / "restored"

    assert main([
        "restore", "--latest",
        "--generation-root", str(tmp_path / "store"),
        "--latest-pointer", str(tmp_path / "latest.json"),
        "--destination", str(destination),
        "--json",
    ]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result == {
        "status": "restored",
        "generation_id": "g1",
        "destination": str(destination),
    }
    assert calls[-1] == ("restore", destination)


def test_restore_cli_requires_explicit_generation_selection(tmp_path):
    with pytest.raises(SystemExit):
        main(["restore", "--destination", str(tmp_path / "target")])
