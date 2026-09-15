from pathlib import Path

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

    result = run_scheduled_incremental(
        GenerationStore(),
        tmp_path / "work",
        "weekly-10-1",
        organization_limit=250,
        report_discovery_limit=None,
        report_discovery_all_due=True,
        report_refresh_interval_days=0,
        checkpoint_store=checkpoint,
    )

    assert result["status"] == "no_changes"
    assert [item[0] for item in checkpoint.calls] == ["restore", "publish"]
    assert checkpoint.calls[1][3] == "g1"
    assert calls[0]["report_discovery_limit"] is None
    assert calls[0]["report_discovery_all_due"] is True
    assert calls[0]["report_refresh_interval_days"] == 0
