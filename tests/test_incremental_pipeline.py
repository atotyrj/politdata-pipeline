from politdata.change_set import create_change_set, load_change_set, save_change_set
from politdata.incremental_pipeline import run_incremental_downstream
import pytest


def test_no_change_run_skips_all_downstream_stages(tmp_path):
    path = tmp_path / "change.json"
    save_change_set(create_change_set(run_id="empty"), path)
    result = run_incremental_downstream(path)
    assert result["status"] == "no_changes"
    assert {
        item["status"] for item in load_change_set(path)["stages"].values()
    } == {"skipped"}


def test_pipeline_orders_stages_and_resumes_completed_work(tmp_path, monkeypatch):
    path = tmp_path / "change.json"
    change = create_change_set(
        run_id="run-1",
        report_changes=[{
            "report_id": "r1", "organization_id": "o1",
            "change_type": "new", "old_content_hash": None,
            "new_content_hash": "hash",
        }],
    )
    change["stages"]["normalization"]["status"] = "completed"
    save_change_set(change, path)
    calls = []

    monkeypatch.setattr(
        "politdata.incremental_pipeline.promote_normalized_fragments",
        lambda **kwargs: calls.append("promotion") or {},
    )
    monkeypatch.setattr(
        "politdata.incremental_pipeline.plan_incremental_dependencies",
        lambda **kwargs: calls.append("plan") or {},
    )

    def complete(stage):
        def runner(**kwargs):
            calls.append(stage)
            current = load_change_set(path)
            current["stages"][stage]["status"] = "completed"
            save_change_set(current, path)
            return {}
        return runner

    monkeypatch.setattr(
        "politdata.incremental_pipeline.run_incremental_references",
        complete("references"),
    )
    monkeypatch.setattr(
        "politdata.incremental_pipeline.run_incremental_enrichment",
        complete("enrichment"),
    )
    monkeypatch.setattr(
        "politdata.incremental_pipeline.promote_enrichment_delta",
        complete("qa"),
    )
    result = run_incremental_downstream(path)
    assert calls == ["promotion", "plan", "references", "enrichment", "qa"]
    assert result["status"] == "completed"


def test_pipeline_refuses_ambiguous_running_stage(tmp_path):
    path = tmp_path / "change.json"
    change = create_change_set(
        run_id="run-2",
        report_changes=[{
            "report_id": "r1", "organization_id": "o1",
            "change_type": "new", "old_content_hash": None,
            "new_content_hash": "hash",
        }],
    )
    change["stages"]["normalization"]["status"] = "running"
    save_change_set(change, path)
    with pytest.raises(RuntimeError, match="reconcile"):
        run_incremental_downstream(path)
