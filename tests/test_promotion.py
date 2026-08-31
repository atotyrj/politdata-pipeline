import json

from politdata.change_set import (
    create_change_set,
    save_change_set,
    set_change_set_stage_status,
)
from politdata.promotion import (
    _replace_directory_with_retry,
    load_promotion_state,
    promote_normalized_fragments,
)
import os


def _write_run(
    root,
    run_id,
    organization_id,
    *,
    change_type,
):
    run = root / run_id
    run.mkdir(parents=True)
    deleted = change_type == "disappeared"

    if not deleted:
        for artifact in (
            "organizations",
            "organization_heads",
            "organization_addresses",
        ):
            path = run / artifact / f"{organization_id}.parquet"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"fragment")

    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "report_count": 0,
        "reports": [],
        "organization_count": 1,
        "organizations": [{
            "organization_id": organization_id,
            "change_type": change_type,
            "deleted": deleted,
        }],
        "deleted_organization_ids": (
            [organization_id] if deleted else []
        ),
        "organization_normalization_pending": False,
    }
    (run / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )


def _write_change_set(
    path,
    run_id,
    organization_id,
    change_type,
):
    change_set = create_change_set(
        run_id=run_id,
        organization_changes=[{
            "organization_id": organization_id,
            "change_type": change_type,
            "new_content_hash": (
                None if change_type == "disappeared" else run_id
            ),
        }],
    )
    change_set = set_change_set_stage_status(
        change_set,
        "normalization",
        "completed",
    )
    save_change_set(change_set, path)


def test_promotion_is_immutable_and_idempotent(tmp_path):
    fragments = tmp_path / "fragments"
    delta_root = tmp_path / "deltas"
    change_set_path = tmp_path / "change_set.json"
    _write_run(
        fragments,
        "run-1",
        "o1",
        change_type="new",
    )
    _write_change_set(
        change_set_path,
        "run-1",
        "o1",
        "new",
    )

    first = promote_normalized_fragments(
        change_set_path,
        fragments,
        delta_root,
    )
    second = promote_normalized_fragments(
        change_set_path,
        fragments,
        delta_root,
    )

    assert first == second
    assert (delta_root / "runs" / "run-1").is_dir()
    assert len(load_promotion_state(delta_root)["runs"]) == 1


def test_update_and_delete_advance_entity_index(tmp_path):
    fragments = tmp_path / "fragments"
    delta_root = tmp_path / "deltas"
    change_set_path = tmp_path / "change_set.json"

    for run_id, change_type in (
        ("run-1", "new"),
        ("run-2", "meaningful_change"),
        ("run-3", "disappeared"),
    ):
        _write_run(
            fragments,
            run_id,
            "o1",
            change_type=change_type,
        )
        _write_change_set(
            change_set_path,
            run_id,
            "o1",
            change_type,
        )
        promote_normalized_fragments(
            change_set_path,
            fragments,
            delta_root,
        )

    state = load_promotion_state(delta_root)
    assert len(state["runs"]) == 3
    assert state["latest_run_id"] == "run-3"
    assert state["organizations"]["o1"] == {
        "run_id": "run-3",
        "change_type": "disappeared",
        "deleted": True,
        "content_hash": None,
    }
    assert (
        delta_root / "runs" / "run-1" / "organizations"
    ).is_dir()
    assert (
        delta_root / "runs" / "run-2" / "organizations"
    ).is_dir()


def test_rejects_incomplete_normalization(tmp_path):
    change_set_path = tmp_path / "change_set.json"
    save_change_set(
        create_change_set(run_id="run-1"),
        change_set_path,
    )

    try:
        promote_normalized_fragments(
            change_set_path,
            tmp_path / "fragments",
            tmp_path / "deltas",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Expected incomplete normalization failure")


def test_report_promotion_indexes_latest_partition(tmp_path):
    fragments = tmp_path / "fragments"
    run = fragments / "report-run"
    payment_sections = (
        "monetary_contributions",
        "other_contributions",
        "state_funding",
        "other_incomes",
        "budget_expenses",
        "outgoing_expenses",
        "return_expenses",
        "transfer_expenses",
    )
    for section in payment_sections:
        path = run / "payments" / section / "r1.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fragment")

    manifest = {
        "schema_version": 1,
        "run_id": "report-run",
        "organization_count": 0,
        "organizations": [],
        "report_count": 1,
        "reports": [{
            "report_id": "r1",
            "organization_id": "o1",
            "content_hash": "new",
            "payments": {
                section: 0
                for section in payment_sections
            },
            "property_moneys": 0,
            "report_sections": {},
        }],
    }
    (run / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    change_set = create_change_set(
        run_id="report-run",
        report_changes=[{
            "report_id": "r1",
            "organization_id": "o1",
            "change_type": "meaningful_change",
            "old_content_hash": "old",
            "new_content_hash": "new",
        }],
    )
    change_set = set_change_set_stage_status(
        change_set,
        "normalization",
        "completed",
    )
    change_set_path = tmp_path / "change_set.json"
    save_change_set(change_set, change_set_path)
    delta_root = tmp_path / "deltas"

    promote_normalized_fragments(
        change_set_path,
        fragments,
        delta_root,
    )

    assert load_promotion_state(delta_root)["reports"]["r1"] == {
        "run_id": "report-run",
        "organization_id": "o1",
        "change_type": "meaningful_change",
        "deleted": False,
        "content_hash": "new",
    }


def test_directory_promotion_retries_transient_windows_lock(tmp_path, monkeypatch):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    (source / "fragment.txt").write_text("ok", encoding="utf-8")
    real_replace = os.replace
    attempts = []

    def flaky_replace(from_path, to_path):
        attempts.append((from_path, to_path))
        if len(attempts) < 3:
            raise PermissionError(5, "Access is denied")
        return real_replace(from_path, to_path)

    monkeypatch.setattr("politdata.promotion.os.replace", flaky_replace)
    monkeypatch.setattr("politdata.promotion.time.sleep", lambda _: None)

    _replace_directory_with_retry(source, destination)

    assert len(attempts) == 3
    assert (destination / "fragment.txt").read_text(encoding="utf-8") == "ok"
