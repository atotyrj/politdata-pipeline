"""Atomic refresh of report manifests while preserving analytical overrides."""

from __future__ import annotations

import os
from pathlib import Path
import uuid

import pandas as pd

from .report_selection import merge_analysis_overrides, select_official_reports


def _same_report_content(left, right):
    """Compare source report metadata while ignoring retrieval timestamps."""

    columns = sorted(
        (set(left.columns) | set(right.columns)) - {"discovered_at_utc"}
    )
    left = left.reindex(columns=columns)
    right = right.reindex(columns=columns)
    sort_columns = [
        column
        for column in ("organization_id", "year", "quarter", "report_id")
        if column in columns
    ]
    if sort_columns:
        left = left.sort_values(sort_columns, kind="stable", na_position="last")
        right = right.sort_values(sort_columns, kind="stable", na_position="last")
    try:
        pd.testing.assert_frame_equal(
            left.reset_index(drop=True),
            right.reset_index(drop=True),
            check_dtype=False,
            check_like=True,
        )
    except AssertionError:
        return False
    return True


def _atomic_parquet(frame, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp." + uuid.uuid4().hex)
    try:
        frame.to_parquet(temp, index=False)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def update_report_manifests(
    refreshed_reports,
    *,
    affected_organization_ids,
    all_reports_path,
    selected_reports_path,
    analysis_reports_path,
):
    """Replace affected organizations, reselect reports, atomically publish.

    Existing manual analytical overrides are preserved only when their target
    still exists in the newly assembled logical reporting period.
    """

    ids = {str(value) for value in affected_organization_ids}
    if not ids:
        return {"status": "no_affected_organizations", "invalid_overrides": []}
    old_all = pd.read_parquet(all_reports_path)
    refreshed = refreshed_reports.copy()
    old_all["organization_id"] = old_all["organization_id"].astype(str)
    if refreshed.empty:
        refreshed = old_all.iloc[0:0].copy()
    elif "organization_id" not in refreshed.columns:
        raise ValueError("refreshed_reports must contain organization_id.")
    refreshed["organization_id"] = refreshed["organization_id"].astype(str)
    old_affected = old_all[old_all["organization_id"].isin(ids)]
    refreshed_affected = refreshed[refreshed["organization_id"].isin(ids)]
    if _same_report_content(old_affected, refreshed_affected):
        return {
            "status": "no_changes",
            "all_reports": len(old_all),
            "selected_reports": len(pd.read_parquet(selected_reports_path)),
            "invalid_overrides": [],
        }

    previous_analysis = pd.read_parquet(analysis_reports_path)
    combined = pd.concat([
        old_all[~old_all["organization_id"].isin(ids)],
        refreshed_affected,
    ], ignore_index=True)
    instances, _ = select_official_reports(combined)
    selected = instances[instances["is_selected_report"]].copy()
    analysis, invalid = merge_analysis_overrides(instances, previous_analysis)
    _atomic_parquet(combined, all_reports_path)
    _atomic_parquet(selected, selected_reports_path)
    _atomic_parquet(analysis, analysis_reports_path)
    return {
        "status": "updated",
        "all_reports": len(combined),
        "selected_reports": len(selected),
        "invalid_overrides": invalid.to_dict("records"),
    }
