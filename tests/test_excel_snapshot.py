from pathlib import Path

import pandas as pd

from politdata.excel_snapshot import (
    _current_enrichment_report_index,
    _current_report_frame,
    materialize_current_reference_inputs,
)


def _write(frame, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def _organization(organization_id, name):
    return {
        "organization_id": organization_id,
        "root_party_id": organization_id,
        "name": name,
        "code": "12345678",
        "entity_type": "party",
        "parent_id": None,
        "is_active": True,
        "created_at": None,
        "updated_at": None,
        "web_site_url": None,
        "email": None,
        "phone": None,
        "actual_address_same_register": True,
    }


def test_current_reference_snapshot_uses_incremental_reports(tmp_path):
    root = tmp_path / "generation"
    normalized = root / "data" / "processed" / "normalized_v0_1"
    deltas = root / "data" / "processed" / "normalized_deltas_v0_1"
    reports = root / "data" / "interim" / "reports"

    _write(
        pd.DataFrame([_organization("party-1", "Current party")]),
        normalized / "organizations.parquet",
    )
    _write(
        pd.DataFrame(
            [{
                "organization_id": "party-1",
                "address_type": "register",
                "region": "Kyiv",
            }]
        ),
        normalized / "organization_addresses.parquet",
    )
    deltas.mkdir(parents=True)
    (deltas / "state.json").write_text(
        '{"schema_version":1,"latest_run_id":null,"runs":[],"organizations":{},"reports":{}}',
        encoding="utf-8",
    )
    manifest = pd.DataFrame(
        [
            {
                "report_id": "q2",
                "organization_id": "party-1",
                "root_party_id": "party-1",
                "year": 2026,
                "quarter": 2,
                "official_selected_report_id": "q2",
                "analysis_selected_report_id": "q2",
                "analysis_selection_method": "official",
                "analysis_override": False,
            },
            {
                "report_id": "q3",
                "organization_id": "party-1",
                "root_party_id": "party-1",
                "year": 2026,
                "quarter": 3,
                "official_selected_report_id": "q3",
                "analysis_selected_report_id": "q3",
                "analysis_selection_method": "official",
                "analysis_override": False,
            },
        ]
    )
    _write(manifest, reports / "analysis_selected_reports_manifest.parquet")

    _, context = materialize_current_reference_inputs(root, tmp_path / "snapshot")

    assert set(context["source_report_id"]) == {"q2", "q3"}
    assert set(context["period_label"]) == {"2026 Q2", "2026 Q3"}


def test_current_report_frame_replaces_baseline_and_drops_unselected(tmp_path):
    base = tmp_path / "base.parquet"
    runs = tmp_path / "runs"
    _write(
        pd.DataFrame(
            [
                {"source_report_id": "old", "value": 1},
                {"source_report_id": "unchanged", "value": 2},
            ]
        ),
        base,
    )
    _write(
        pd.DataFrame([{"source_report_id": "new", "value": 3}]),
        runs / "run-1" / "sections" / "new.parquet",
    )

    result = _current_report_frame(
        base,
        report_index={
            "new": {"run_id": "run-1", "deleted": False},
        },
        runs_root=runs,
        fragment_path=lambda report_id: Path("sections") / f"{report_id}.parquet",
        selected_report_ids={"unchanged", "new"},
    )

    assert result.sort_values("source_report_id").to_dict("records") == [
        {"source_report_id": "new", "value": 3},
        {"source_report_id": "unchanged", "value": 2},
    ]


def test_enrichment_index_tracks_indirect_rebuilds(tmp_path):
    runs = tmp_path / "runs"
    for run_id, report_ids in (
        ("run-1", ["direct", "dependent"]),
        ("run-2", ["dependent"]),
    ):
        path = runs / run_id / "manifest.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            '{"affected_report_ids":['
            + ",".join(f'"{value}"' for value in report_ids)
            + "]}",
            encoding="utf-8",
        )
    state = {"runs": [{"run_id": "run-1"}, {"run_id": "run-2"}]}

    result = _current_enrichment_report_index(state, runs)

    assert result["direct"]["run_id"] == "run-1"
    assert result["dependent"]["run_id"] == "run-2"
