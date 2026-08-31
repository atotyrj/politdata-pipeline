"""Atomic refresh of report manifests while preserving analytical overrides."""

from __future__ import annotations

import os
from pathlib import Path
import uuid

import pandas as pd

from .report_selection import merge_analysis_overrides, select_official_reports


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
    previous_analysis = pd.read_parquet(analysis_reports_path)
    refreshed = refreshed_reports.copy()
    refreshed["organization_id"] = refreshed["organization_id"].astype(str)
    old_all["organization_id"] = old_all["organization_id"].astype(str)
    combined = pd.concat([
        old_all[~old_all["organization_id"].isin(ids)],
        refreshed[refreshed["organization_id"].isin(ids)],
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
