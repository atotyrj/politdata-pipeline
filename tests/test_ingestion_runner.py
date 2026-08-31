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
