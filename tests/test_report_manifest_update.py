import pandas as pd

from politdata.report_manifest_update import update_report_manifests


def test_manifest_update_replaces_only_affected_org_and_preserves_override(tmp_path):
    paths = [tmp_path / name for name in ("all.parquet", "selected.parquet", "analysis.parquet")]
    old = pd.DataFrame([
        {"report_id": "old", "organization_id": "o1", "year": 2025, "quarter": 1, "signed_date": "2025-01-01"},
        {"report_id": "keep", "organization_id": "o2", "year": 2025, "quarter": 1, "signed_date": "2025-01-01"},
    ])
    old.to_parquet(paths[0], index=False)
    old.to_parquet(paths[1], index=False)
    pd.DataFrame([{
        **old.iloc[0].to_dict(), "analysis_override": True,
        "analysis_selected_report_id": "alternate", "analysis_selection_method": "manual",
        "override_reason": "evidence", "continuity_exact": True,
    }]).to_parquet(paths[2], index=False)
    refreshed = pd.DataFrame([
        {"report_id": "new", "organization_id": "o1", "year": 2025, "quarter": 1, "signed_date": "2025-02-01"},
        {"report_id": "alternate", "organization_id": "o1", "year": 2025, "quarter": 1, "signed_date": None},
    ])

    result = update_report_manifests(refreshed, affected_organization_ids=["o1"], all_reports_path=paths[0], selected_reports_path=paths[1], analysis_reports_path=paths[2])

    assert result["invalid_overrides"] == []
    assert set(pd.read_parquet(paths[0])["report_id"]) == {"new", "alternate", "keep"}
    analysis = pd.read_parquet(paths[2])
    assert analysis.loc[analysis.organization_id == "o1", "analysis_selected_report_id"].item() == "alternate"


def test_manifest_update_does_not_rewrite_semantically_unchanged_files(tmp_path):
    paths = [
        tmp_path / name
        for name in ("all.parquet", "selected.parquet", "analysis.parquet")
    ]
    old = pd.DataFrame(
        [
            {
                "report_id": "r1",
                "organization_id": "o1",
                "year": 2025,
                "quarter": 1,
                "signed_date": "2025-01-01",
                "discovered_at_utc": "2026-01-01T00:00:00+00:00",
            }
        ]
    )
    for path in paths:
        old.to_parquet(path, index=False)
    before = {path: path.read_bytes() for path in paths}
    refreshed = old.copy()
    refreshed["discovered_at_utc"] = "2026-01-10T00:00:00+00:00"

    result = update_report_manifests(
        refreshed,
        affected_organization_ids=["o1"],
        all_reports_path=paths[0],
        selected_reports_path=paths[1],
        analysis_reports_path=paths[2],
    )

    assert result["status"] == "no_changes"
    assert {path: path.read_bytes() for path in paths} == before
