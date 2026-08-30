import pandas as pd

from politdata.change_set import (
    create_change_set,
    load_change_set,
    save_change_set,
)
from politdata.report_details import (
    update_report_change_set,
)


def test_report_batch_updates_existing_organization_change_set(
    tmp_path,
):
    path = tmp_path / "current.json"
    initial = create_change_set(
        run_id="run-1",
        created_at_utc="2026-08-30T00:00:00+00:00",
        organization_changes=[{
            "organization_id": "o1",
            "change_type": "meaningful_change",
        }],
    )
    save_change_set(initial, path)

    previous = pd.DataFrame([
        {
            "report_id": "r1",
            "organization_id": "o1",
            "status": "success",
            "content_hash": "old",
        }
    ])
    current = pd.DataFrame([
        {
            "report_id": "r1",
            "organization_id": "o1",
            "status": "success",
            "content_hash": "new",
        }
    ])

    update_report_change_set(
        previous,
        current,
        change_set_path=path,
    )

    updated = load_change_set(path)
    assert updated["run_id"] == "run-1"
    assert updated["affected_organization_ids"] == ["o1"]
    assert updated["affected_report_ids"] == ["r1"]
