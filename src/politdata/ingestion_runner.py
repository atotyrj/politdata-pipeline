"""Explicit, bounded entry point for online organization ingestion."""

from __future__ import annotations

from .change_set import DEFAULT_CURRENT_CHANGE_SET_PATH
from .incremental_pipeline import run_incremental_downstream
from .report_details import run_report_detail_batch
from .report_discovery import build_report_manifest_from_snapshots, run_report_discovery_batch
from .report_manifest_update import update_report_manifests
from .sync import run_organization_sync
import pandas as pd


DEFAULT_SELECTED_REPORTS_PATH = "data/interim/reports/selected_reports_manifest.parquet"
DEFAULT_ANALYSIS_REPORTS_PATH = "data/interim/reports/analysis_selected_reports_manifest.parquet"
DEFAULT_ALL_REPORTS_PATH = "data/interim/reports/all_reports_manifest.parquet"


def run_limited_organization_ingestion(
    *,
    organization_limit,
    change_set_path=DEFAULT_CURRENT_CHANGE_SET_PATH,
    run_downstream=True,
    report_limit=None,
    sync_options=None,
):
    """Fetch at most ``organization_limit`` candidate cards, then process changes.

    This is an online operation. It is deliberately limited to organization
    cards: report-list refresh and report-selection policy are not silently
    inferred here, so newly published report instances cannot be missed.
    """

    organization_limit = int(organization_limit)
    if organization_limit <= 0:
        raise ValueError("organization_limit must be positive.")

    options = dict(sync_options or {})
    if "candidate_limit" in options:
        raise ValueError("Pass organization_limit, not sync_options.candidate_limit.")
    if "change_set_path" in options:
        raise ValueError("Pass change_set_path directly.")

    sync = run_organization_sync(
        candidate_limit=organization_limit,
        change_set_path=change_set_path,
        **options,
    )
    result = {
        "mode": "online_organization_sync",
        "organization_limit": organization_limit,
        "sync": sync,
        "change_set_path": str(change_set_path),
    }
    if report_limit is not None:
        report_limit = int(report_limit)
        if report_limit <= 0:
            raise ValueError("report_limit must be positive.")
        changed_ids = [
            str(item["organization_id"])
            for item in sync.get("results", [])
            if item.get("status") in {"new", "meaningful_change"}
        ]
        if not changed_ids:
            result["reports"] = {"status": "no_changed_organizations"}
            if run_downstream:
                result["downstream"] = run_incremental_downstream(
                    change_set_path=change_set_path
                )
            else:
                result["downstream"] = {"status": "not_requested"}
            return result
        manifest = pd.read_parquet(options.get("committed_manifest_path", "data/interim/manifests/organization_manifest_committed.parquet"))
        discovery_summary, _ = run_report_discovery_batch(
            manifest, organization_ids=changed_ids, limit=organization_limit
        )
        refreshed, _ = build_report_manifest_from_snapshots(manifest)
        update = update_report_manifests(
            refreshed, affected_organization_ids=changed_ids,
            all_reports_path=DEFAULT_ALL_REPORTS_PATH,
            selected_reports_path=DEFAULT_SELECTED_REPORTS_PATH,
            analysis_reports_path=DEFAULT_ANALYSIS_REPORTS_PATH,
        )
        selected = pd.read_parquet(DEFAULT_SELECTED_REPORTS_PATH)
        candidates = selected[selected["organization_id"].astype(str).isin(changed_ids)]
        details_summary, _ = run_report_detail_batch(
            candidates, limit=report_limit, change_set_path=change_set_path
        )
        result["reports"] = {"discovery": discovery_summary, "manifest": update, "details": details_summary}
    else:
        result["reports"] = {"status": "not_requested"}
    if run_downstream:
        result["downstream"] = run_incremental_downstream(
            change_set_path=change_set_path
        )
    else:
        result["downstream"] = {"status": "not_requested"}
    return result
