import pandas as pd
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


def test_report_flow_runs_due_queue_without_organization_card_changes(monkeypatch):
    calls = []
    manifest = pd.DataFrame(
        [{"organization_id": "o1", "root_party_id": "p1"}]
    )
    selected = pd.DataFrame([{"report_id": "r1", "organization_id": "o1"}])
    monkeypatch.setattr(
        "politdata.ingestion_runner.run_organization_sync",
        lambda **_: {"results": []},
    )
    monkeypatch.setattr(
        "politdata.ingestion_runner.run_report_discovery_batch",
        lambda frame, **kwargs: (
            calls.append(("discovery", frame.copy(), kwargs))
            or (
                {
                    "successful_organization_ids": [],
                    "selected_organization_ids": [],
                },
                pd.DataFrame(),
            )
        ),
    )
    monkeypatch.setattr(
        "politdata.ingestion_runner.run_report_detail_batch",
        lambda frame, **kwargs: (
            calls.append(("details", frame.copy(), kwargs))
            or ({"selected": 0}, pd.DataFrame())
        ),
    )
    monkeypatch.setattr(
        "politdata.ingestion_runner.pd.read_parquet",
        lambda path: manifest.copy()
        if "organization_manifest" in str(path)
        else selected.copy(),
    )
    result = run_limited_organization_ingestion(
        organization_limit=1, report_limit=1, run_downstream=False
    )

    assert [call[0] for call in calls] == ["discovery", "details"]
    assert calls[0][2]["limit"] == 1
    assert calls[1][1]["report_id"].tolist() == ["r1"]
    assert result["reports"]["manifest"]["status"] == (
        "no_due_or_successful_organizations"
    )


def test_report_flow_rebuilds_only_successfully_refreshed_snapshots(monkeypatch):
    manifest = pd.DataFrame(
        [{"organization_id": "o1", "root_party_id": "p1"}]
    )
    refreshed = pd.DataFrame([{"report_id": "r2", "organization_id": "o1"}])
    calls = []
    monkeypatch.setattr(
        "politdata.ingestion_runner.run_organization_sync",
        lambda **_: {"results": []},
    )
    monkeypatch.setattr(
        "politdata.ingestion_runner.pd.read_parquet",
        lambda _path: manifest.copy(),
    )
    monkeypatch.setattr(
        "politdata.ingestion_runner.run_report_discovery_batch",
        lambda *_args, **_kwargs: (
            {
                "successful_organization_ids": ["o1"],
                "selected_organization_ids": ["o1"],
            },
            pd.DataFrame(),
        ),
    )

    def build(_manifest, **kwargs):
        calls.append(("build", kwargs))
        return refreshed, {"rows": 1}

    def update(frame, **kwargs):
        calls.append(("update", frame.copy(), kwargs))
        return {"status": "updated", "invalid_overrides": []}

    monkeypatch.setattr(
        "politdata.ingestion_runner.build_report_manifest_from_snapshots", build
    )
    monkeypatch.setattr(
        "politdata.ingestion_runner.update_report_manifests", update
    )

    result = run_limited_organization_ingestion(
        organization_limit=1,
        report_discovery_limit=1,
        run_downstream=False,
    )

    assert calls[0] == ("build", {"organization_ids": ["o1"]})
    assert calls[1][2]["affected_organization_ids"] == ["o1"]
    assert result["reports"]["details"]["status"] == "not_requested"
