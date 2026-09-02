import json

import pandas as pd

from politdata.report_discovery import (
    build_report_manifest_from_snapshots,
    initialize_report_discovery_state,
    run_report_discovery_batch,
    select_report_discovery_candidates,
)


def _manifest(*organization_ids):
    return pd.DataFrame(
        [
            {
                "organization_id": organization_id,
                "root_party_id": f"party-{organization_id}",
                "entity_type": "office",
                "name": f"Organization {organization_id}",
            }
            for organization_id in organization_ids
        ]
    )


def test_legacy_success_state_becomes_due_without_card_change(tmp_path):
    state_path = tmp_path / "discovery.parquet"
    pd.DataFrame(
        [
            {
                "organization_id": "old-due",
                "root_party_id": "p1",
                "entity_type": "office",
                "name": "Old due",
                "status": "success",
                "last_checked_at_utc": "2026-01-01T00:00:00+00:00",
            },
            {
                "organization_id": "recent",
                "root_party_id": "p2",
                "entity_type": "office",
                "name": "Recent",
                "status": "success",
                "last_checked_at_utc": "2026-01-09T00:00:00+00:00",
            },
            {
                "organization_id": "retry",
                "root_party_id": "p3",
                "entity_type": "office",
                "name": "Retry",
                "status": "error",
                "last_checked_at_utc": "2026-01-09T00:00:00+00:00",
            },
        ]
    ).to_parquet(state_path, index=False)

    state = initialize_report_discovery_state(
        _manifest("old-due", "recent", "retry", "new"),
        state_path=state_path,
        refresh_interval_days=7,
    )
    candidates, due_total = select_report_discovery_candidates(
        state,
        now="2026-01-10T00:00:00+00:00",
    )

    assert due_total == 3
    assert candidates["organization_id"].tolist() == ["new", "retry", "old-due"]
    recent = state.set_index("organization_id").loc["recent"]
    assert recent["next_check_at_utc"] == "2026-01-16T00:00:00+00:00"


def test_due_success_is_refreshed_and_rescheduled(monkeypatch, tmp_path):
    state_path = tmp_path / "discovery.parquet"
    snapshot_dir = tmp_path / "snapshots"
    pd.DataFrame(
        [
            {
                "organization_id": "o1",
                "root_party_id": "p1",
                "entity_type": "office",
                "name": "Office",
                "status": "success",
                "last_checked_at_utc": "2026-01-01T00:00:00+00:00",
            }
        ]
    ).to_parquet(state_path, index=False)

    reports = [
        {
            "id": "new-report",
            "party_id": "o1",
            "is_party_office": True,
            "year": 2026,
            "quarter": 1,
        }
    ]
    metadata = {
        "declared_count": 1,
        "fetched_count": 1,
        "count_difference": 0,
        "count_mismatch": False,
    }
    monkeypatch.setattr(
        "politdata.report_discovery.fetch_all_reports",
        lambda *_args, **_kwargs: (reports, metadata),
    )

    summary, state = run_report_discovery_batch(
        _manifest("o1"),
        state_path=state_path,
        snapshot_dir=snapshot_dir,
        limit=1,
        checkpoint_every=1,
        refresh_interval_days=7,
        now="2026-01-10T00:00:00+00:00",
    )

    assert summary["selected_organization_ids"] == ["o1"]
    assert summary["successful_organization_ids"] == ["o1"]
    row = state.set_index("organization_id").loc["o1"]
    assert row["status"] == "success"
    assert row["next_check_at_utc"] == "2026-01-17T00:00:00+00:00"
    assert row["attempts"] == 1
    snapshot = json.loads((snapshot_dir / "o1.json").read_text(encoding="utf-8"))
    assert snapshot["reports"][0]["id"] == "new-report"


def test_failed_refresh_waits_for_persisted_backoff(monkeypatch, tmp_path):
    def fail(*_args, **_kwargs):
        raise RuntimeError("temporary")

    monkeypatch.setattr("politdata.report_discovery.fetch_all_reports", fail)
    _, state = run_report_discovery_batch(
        _manifest("o1"),
        state_path=tmp_path / "discovery.parquet",
        snapshot_dir=tmp_path / "snapshots",
        limit=1,
        refresh_interval_days=7,
        error_retry_base_hours=6,
        now="2026-01-10T00:00:00+00:00",
    )

    row = state.set_index("organization_id").loc["o1"]
    assert row["status"] == "error"
    assert row["consecutive_errors"] == 1
    assert row["next_check_at_utc"] == "2026-01-10T06:00:00+00:00"
    early, early_total = select_report_discovery_candidates(
        state, now="2026-01-10T01:00:00+00:00"
    )
    due, due_total = select_report_discovery_candidates(
        state, now="2026-01-10T07:00:00+00:00"
    )
    assert early.empty and early_total == 0
    assert due["organization_id"].tolist() == ["o1"] and due_total == 1


def test_scoped_manifest_builder_does_not_scan_other_snapshots(tmp_path):
    snapshot_dir = tmp_path / "snapshots"
    snapshot_dir.mkdir()
    (snapshot_dir / "o1.json").write_text(
        json.dumps(
            {
                "organization_id": "o1",
                "retrieved_at_utc": "2026-01-10T00:00:00+00:00",
                "reports": [
                    {
                        "id": "r1",
                        "party_id": "o1",
                        "is_party_office": True,
                        "year": 2026,
                        "quarter": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (snapshot_dir / "o2.json").write_text("not json", encoding="utf-8")

    reports, qa = build_report_manifest_from_snapshots(
        _manifest("o1", "o2"),
        snapshot_dir=snapshot_dir,
        organization_ids=["o1"],
    )

    assert reports["report_id"].tolist() == ["r1"]
    assert qa["requested_snapshots_missing"] == 0
