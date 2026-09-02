import json

import pytest

from politdata.orchestrator import (
    FullReplaceNotConfigured,
    RunConfig,
    WriterLock,
    WriterLockError,
    build_run_plan,
    run_pipeline,
)


def _roots(tmp_path):
    return {
        "control_root": tmp_path / "control",
        "run_root": tmp_path / "runs",
        "generation_root": tmp_path / "generations",
    }


def test_incremental_uses_shared_lifecycle_and_writes_completed_journal(tmp_path):
    calls = []
    config = RunConfig(
        mode="incremental",
        run_id="incremental-1",
        organization_limit=3,
        report_discovery_limit=5,
        report_detail_limit=7,
        change_set_path=tmp_path / "change.json",
        **_roots(tmp_path),
    )

    def runner(**kwargs):
        calls.append(kwargs)
        return {"downstream": {"status": "no_changes"}}

    result = run_pipeline(config, incremental_runner=runner)

    assert result["status"] == "completed"
    assert calls == [
        {
            "organization_limit": 3,
            "change_set_path": tmp_path / "change.json",
            "run_downstream": True,
            "report_limit": 7,
            "report_discovery_limit": 5,
            "report_refresh_interval_days": 7,
        }
    ]
    journal = json.loads(
        (tmp_path / "control" / "runs" / "incremental-1.json").read_text(
            encoding="utf-8"
        )
    )
    assert journal["status"] == "completed"
    assert not (tmp_path / "control" / "writer.lock").exists()


def test_writer_lock_refuses_a_second_writer(tmp_path):
    path = tmp_path / "writer.lock"
    first = WriterLock(path, run_id="one", mode="incremental").acquire()
    try:
        with pytest.raises(WriterLockError, match="already exists"):
            WriterLock(path, run_id="two", mode="incremental").acquire()
    finally:
        first.release()


def test_dry_run_is_read_only_and_does_not_call_runner(tmp_path):
    config = RunConfig(
        mode="full-replace",
        run_id="full-plan",
        dry_run=True,
        **_roots(tmp_path),
    )
    result = run_pipeline(
        config,
        full_replace_stage_runner=lambda **_: (_ for _ in ()).throw(
            AssertionError("must not run")
        ),
    )

    assert result["status"] == "planned"
    assert result["writes"] == 0
    assert not (tmp_path / "control").exists()
    assert result["full_replace_paths"]["staging_dir"].endswith(
        "full-plan\\staging"
    ) or result["full_replace_paths"]["staging_dir"].endswith(
        "full-plan/staging"
    )


def test_full_replace_requires_confirmation_and_configured_stage(tmp_path):
    with pytest.raises(ValueError, match="explicit"):
        RunConfig(mode="full-replace", run_id="full-1", **_roots(tmp_path))

    config = RunConfig(
        mode="full-replace",
        run_id="full-1",
        confirm_full_replace=True,
        **_roots(tmp_path),
    )
    with pytest.raises(FullReplaceNotConfigured):
        run_pipeline(config, full_replace_stage_runner=None)
    journal = json.loads(
        (tmp_path / "control" / "runs" / "full-1.json").read_text(
            encoding="utf-8"
        )
    )
    assert journal["status"] == "failed"
    assert not (tmp_path / "control" / "latest.json").exists()


def test_validated_full_generation_does_not_publish_without_flag(tmp_path):
    config = RunConfig(
        mode="full-replace",
        run_id="full-validated",
        confirm_full_replace=True,
        **_roots(tmp_path),
    )

    def stage(*, config, paths):
        (paths.raw_dir / "source.json").write_text("{}", encoding="utf-8")
        (paths.processed_dir / "result.parquet").write_bytes(b"validated")
        return {
            "status": "completed",
            "qa": {"passed": True},
            "row_counts": {"reports": 1},
        }

    result = run_pipeline(config, full_replace_stage_runner=stage)
    generation = tmp_path / "generations" / "full-validated"

    assert result["result"]["status"] == "validated"
    assert (generation / "raw" / "source.json").is_file()
    assert (generation / "processed" / "result.parquet").is_file()
    assert (generation / "generation_manifest.json").is_file()
    assert not (tmp_path / "control" / "latest.json").exists()


def test_full_publish_switches_latest_only_after_qa(tmp_path):
    roots = _roots(tmp_path)
    old_latest = roots["control_root"] / "latest.json"
    old_latest.parent.mkdir(parents=True)
    old_latest.write_text(
        json.dumps({"generation_id": "previous"}), encoding="utf-8"
    )

    def passing_stage(*, config, paths):
        (paths.raw_dir / "source.json").write_text("{}", encoding="utf-8")
        return {"status": "completed", "qa": {"passed": True}}

    published = run_pipeline(
        RunConfig(
            mode="full-replace",
            run_id="full-published",
            confirm_full_replace=True,
            publish=True,
            **roots,
        ),
        full_replace_stage_runner=passing_stage,
    )
    latest = json.loads(old_latest.read_text(encoding="utf-8"))
    assert published["result"]["status"] == "published"
    assert latest["generation_id"] == "full-published"

    def failing_qa(*, config, paths):
        return {"status": "completed", "qa": {"passed": False}}

    with pytest.raises(RuntimeError, match="QA"):
        run_pipeline(
            RunConfig(
                mode="full-replace",
                run_id="full-failed",
                confirm_full_replace=True,
                publish=True,
                **roots,
            ),
            full_replace_stage_runner=failing_qa,
        )
    assert json.loads(old_latest.read_text(encoding="utf-8")) == latest


def test_build_run_plan_accepts_mapping(tmp_path):
    plan = build_run_plan(
        {
            "mode": "incremental",
            "run_id": "mapped",
            "organization_limit": 1,
            **_roots(tmp_path),
        }
    )
    assert plan["config"]["mode"] == "incremental"


def test_orchestrator_can_publish_through_non_filesystem_store(tmp_path):
    calls = []

    class Store:
        latest_location = "memory://latest.json"

        def publish_generation(self, source_dir, generation_id):
            assert (source_dir / "generation_manifest.json").exists()
            calls.append(("generation", generation_id))
            return f"memory://generations/{generation_id}"

        def publish_latest(self, pointer, *, expected_generation_id=None):
            calls.append(("latest", pointer))
            return self.latest_location

    def stage(*, config, paths):
        (paths.raw_dir / "source.json").write_text("{}", encoding="utf-8")
        return {"status": "completed", "qa": {"passed": True}}

    result = run_pipeline(
        RunConfig(
            mode="full-replace", run_id="remote-store",
            confirm_full_replace=True, publish=True, **_roots(tmp_path),
        ),
        full_replace_stage_runner=stage,
        generation_store=Store(),
    )

    assert result["result"]["generation_path"] == "memory://generations/remote-store"
    assert result["result"]["latest_path"] == "memory://latest.json"
    assert calls[0] == ("generation", "remote-store")
    assert calls[1][0] == "latest"
    assert calls[1][1]["generation_path"] == "memory://generations/remote-store"
