import pandas as pd
import pytest

from politdata.change_set import (
    create_change_set,
    load_change_set,
    merge_change_set_changes,
    organization_changes_from_sync_log,
    report_changes_from_states,
    save_change_set,
)


def test_organization_changes_exclude_technical_refresh():
    sync_log = {
        "committed": True,
        "disappeared_ids": ["org-3"],
        "results": [
            {
                "organization_id": "org-1",
                "status": "meaningful_change",
                "candidate_reasons": ["rolling_refresh"],
                "changed_fields": ["name"],
                "old_content_hash": "old",
                "new_content_hash": "new",
            },
            {
                "organization_id": "org-2",
                "status": "technical_refresh",
                "candidate_reasons": ["index_changed"],
                "changed_fields": ["updated_at"],
                "old_content_hash": "same",
                "new_content_hash": "same",
            },
        ],
    }

    changes = organization_changes_from_sync_log(
        sync_log
    )

    assert [
        item["organization_id"]
        for item in changes
    ] == ["org-1", "org-3"]
    assert changes[1]["change_type"] == "disappeared"


def test_uncommitted_organization_sync_is_rejected():
    with pytest.raises(ValueError):
        organization_changes_from_sync_log({
            "committed": False,
        })


def test_report_changes_use_semantic_content_hash():
    previous = pd.DataFrame([
        {
            "report_id": "r1",
            "organization_id": "o1",
            "status": "success",
            "content_hash": "same",
        },
        {
            "report_id": "r2",
            "organization_id": "o2",
            "status": "success",
            "content_hash": "old",
        },
    ])
    current = pd.DataFrame([
        {
            "report_id": "r1",
            "organization_id": "o1",
            "status": "success",
            "content_hash": "same",
        },
        {
            "report_id": "r2",
            "organization_id": "o2",
            "status": "success",
            "content_hash": "new",
        },
        {
            "report_id": "r3",
            "organization_id": "o2",
            "status": "success",
            "content_hash": "first",
        },
    ])

    changes = report_changes_from_states(
        previous,
        current,
    )

    assert [
        (item["report_id"], item["change_type"])
        for item in changes
    ] == [
        ("r2", "meaningful_change"),
        ("r3", "new"),
    ]


def test_create_change_set_builds_dependency_roots():
    change_set = create_change_set(
        run_id="run-1",
        created_at_utc="2026-08-30T00:00:00+00:00",
        organization_changes=[{
            "organization_id": "o1",
            "change_type": "meaningful_change",
        }],
        report_changes=[{
            "report_id": "r1",
            "organization_id": "o2",
            "change_type": "new",
        }],
    )

    assert change_set["affected_organization_ids"] == [
        "o1",
        "o2",
    ]
    assert change_set["affected_report_ids"] == ["r1"]
    assert all(
        stage["status"] == "pending"
        for stage in change_set["stages"].values()
    )


def test_change_set_json_round_trip(tmp_path):
    change_set = create_change_set(
        run_id="run-1",
        created_at_utc="2026-08-30T00:00:00+00:00",
    )
    path = tmp_path / "change_set.json"

    save_change_set(change_set, path)

    assert load_change_set(path) == change_set


def test_merge_change_set_accumulates_report_batches():
    change_set = create_change_set(
        run_id="run-1",
        created_at_utc="2026-08-30T00:00:00+00:00",
        report_changes=[{
            "report_id": "r1",
            "organization_id": "o1",
            "change_type": "new",
            "old_content_hash": None,
            "new_content_hash": "first",
        }],
    )

    merged = merge_change_set_changes(
        change_set,
        report_changes=[
            {
                "report_id": "r1",
                "organization_id": "o1",
                "change_type": "meaningful_change",
                "old_content_hash": "first",
                "new_content_hash": "second",
            },
            {
                "report_id": "r2",
                "organization_id": "o2",
                "change_type": "new",
                "old_content_hash": None,
                "new_content_hash": "initial",
            },
        ],
    )

    assert merged["affected_report_ids"] == ["r1", "r2"]
    assert merged["report_changes"][0] == {
        "report_id": "r1",
        "organization_id": "o1",
        "change_type": "new",
        "old_content_hash": None,
        "new_content_hash": "second",
    }
