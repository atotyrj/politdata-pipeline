from pathlib import Path

import pandas as pd

from politdata.scheduled_incremental import run_scheduled_incremental


class GenerationStore:
    def read_latest(self):
        return {
            "generation_id": "g1",
            "generation_manifest_hash": "hash",
        }

    def restore_latest(self, destination):
        root = Path(destination)
        (root / "data" / "interim" / "state").mkdir(parents=True)
        return str(root)


class CheckpointStore:
    def __init__(self):
        self.calls = []

    def restore_latest(self, destination, *, base_generation_id):
        self.calls.append(("restore", Path(destination), base_generation_id))
        return {"status": "restored", "checkpoint_id": "previous"}

    def publish_checkpoint(
        self, source_dir, checkpoint_id, *, base_generation_id
    ):
        self.calls.append(
            ("publish", Path(source_dir), checkpoint_id, base_generation_id)
        )
        return {"status": "published", "checkpoint_id": checkpoint_id}


def test_no_change_run_restores_and_publishes_operational_checkpoint(
    monkeypatch, tmp_path
):
    calls = []
    checkpoint = CheckpointStore()
    monkeypatch.setattr(
        "politdata.scheduled_incremental.run_limited_organization_ingestion",
        lambda **kwargs: calls.append(kwargs) or {"status": "complete"},
    )
    monkeypatch.setattr(
        "politdata.scheduled_incremental.load_change_set",
        lambda _path: {"organization_changes": [], "report_changes": []},
    )
    monkeypatch.setattr(
        "politdata.scheduled_incremental._file_hash",
        lambda _path: "analysis-hash",
    )
    monkeypatch.setattr(
        "politdata.scheduled_incremental._published_excel_state",
        lambda _path: {
            "analysis_manifest_hash": "analysis-hash",
            "analytical_excel_contract_version": 2,
        },
    )

    result = run_scheduled_incremental(
        GenerationStore(),
        tmp_path / "work",
        "weekly-10-1",
        organization_limit=None,
        organization_all=True,
        report_discovery_limit=None,
        report_discovery_all_due=True,
        report_refresh_interval_days=0,
        report_detail_limit=None,
        report_details_all_pending=True,
        checkpoint_store=checkpoint,
    )

    assert result["status"] == "no_changes"
    assert [item[0] for item in checkpoint.calls] == ["restore", "publish"]
    assert checkpoint.calls[1][3] == "g1"
    assert calls[0]["report_discovery_limit"] is None
    assert calls[0]["report_discovery_all_due"] is True
    assert calls[0]["report_refresh_interval_days"] == 0
    assert calls[0]["organization_all"] is True
    assert calls[0]["organization_limit"] is None
    assert calls[0]["report_details_all_pending"] is True
    assert calls[0]["report_limit"] is None


def test_no_change_run_repairs_stale_excel_and_publishes(monkeypatch, tmp_path):
    checkpoint = CheckpointStore()
    store = GenerationStore()
    work = tmp_path / "work"
    monkeypatch.setattr(
        "politdata.scheduled_incremental.run_limited_organization_ingestion",
        lambda **_kwargs: {"status": "complete"},
    )
    monkeypatch.setattr(
        "politdata.scheduled_incremental.load_change_set",
        lambda _path: {
            "run_id": "run-1",
            "organization_changes": [],
            "report_changes": [],
        },
    )
    monkeypatch.setattr(
        "politdata.scheduled_incremental._file_hash",
        lambda _path: "new-hash",
    )
    monkeypatch.setattr(
        "politdata.scheduled_incremental._published_excel_state",
        lambda _path: {},
    )
    monkeypatch.setattr(
        "politdata.scheduled_incremental._replace_outputs",
        lambda _root, summary: (
            summary.extend([{"file": "test.xlsx"}])
            or {
                "payment_reference_identity": pd.DataFrame({"mismatches": [0]})
            }
        ),
    )
    captured = {}

    def write_manifest(_root, generation_id, **kwargs):
        captured.update(kwargs)
        return {"generation_id": generation_id}

    monkeypatch.setattr(
        "politdata.scheduled_incremental.write_generation_manifest",
        write_manifest,
    )
    store.publish_generation = lambda *_args: "generation"
    store.publish_latest = lambda *_args, **_kwargs: "latest"

    result = run_scheduled_incremental(
        store,
        work,
        "weekly-11-1",
        checkpoint_store=checkpoint,
    )

    assert result["status"] == "published"
    assert captured["metadata"]["analysis_manifest_hash"] == "new-hash"
    assert captured["metadata"]["excel_refresh_without_source_changes"] is True
