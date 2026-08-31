import pytest

from politdata.ingestion_runner import run_limited_organization_ingestion


def test_limited_online_runner_passes_limit_and_runs_downstream(monkeypatch, tmp_path):
    calls = []
    path = tmp_path / "change.json"
    monkeypatch.setattr(
        "politdata.ingestion_runner.run_organization_sync",
        lambda **kwargs: calls.append(("sync", kwargs)) or {"committed": True},
    )
    monkeypatch.setattr(
        "politdata.ingestion_runner.run_incremental_downstream",
        lambda **kwargs: calls.append(("downstream", kwargs)) or {"status": "no_changes"},
    )

    result = run_limited_organization_ingestion(
        organization_limit=3, change_set_path=path
    )

    assert result["mode"] == "online_organization_sync"
    assert calls == [
        ("sync", {"candidate_limit": 3, "change_set_path": path}),
        ("downstream", {"change_set_path": path}),
    ]


def test_limited_online_runner_requires_positive_limit():
    with pytest.raises(ValueError, match="positive"):
        run_limited_organization_ingestion(organization_limit=0)


def test_report_flow_skips_stateful_report_stages_without_changes(monkeypatch):
    monkeypatch.setattr(
        "politdata.ingestion_runner.run_organization_sync",
        lambda **_: {"results": []},
    )
    monkeypatch.setattr(
        "politdata.ingestion_runner.run_report_discovery_batch",
        lambda **_: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    result = run_limited_organization_ingestion(
        organization_limit=1, report_limit=1, run_downstream=False
    )
    assert result["reports"]["status"] == "no_changed_organizations"
