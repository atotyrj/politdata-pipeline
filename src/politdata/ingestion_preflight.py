"""Read-only readiness checks before an explicitly authorized RAW ingestion."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .change_set import DEFAULT_CURRENT_CHANGE_SET_PATH, load_change_set
from .refresh import DEFAULT_REFRESH_STATE_PATH
from .report_details import DEFAULT_STATE_PATH as DEFAULT_REPORT_DETAIL_STATE_PATH
from .report_discovery import DEFAULT_STATE_PATH as DEFAULT_REPORT_DISCOVERY_STATE_PATH
from .sync import DEFAULT_COMMITTED_MANIFEST


def _parquet_summary(path, *, required_columns=(), status_column="status"):
    path = Path(path)
    summary = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return summary

    try:
        frame = pd.read_parquet(path)
    except Exception as error:
        summary["readable"] = False
        summary["error"] = str(error)
        return summary

    summary["readable"] = True
    summary["rows"] = len(frame)
    summary["columns"] = sorted(frame.columns.tolist())
    summary["missing_required_columns"] = sorted(
        set(required_columns) - set(frame.columns)
    )
    if status_column in frame.columns:
        summary["statuses"] = {
            str(status): int(count)
            for status, count in frame[status_column]
            .fillna("<missing>")
            .value_counts()
            .sort_index()
            .items()
        }
    return summary


def _change_set_summary(path):
    path = Path(path)
    summary = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return summary

    try:
        change_set = load_change_set(path)
    except Exception as error:
        summary["readable"] = False
        summary["error"] = str(error)
        return summary

    summary.update({
        "readable": True,
        "run_id": change_set["run_id"],
        "organization_changes": len(change_set["organization_changes"]),
        "report_changes": len(change_set["report_changes"]),
        "stages": {
            name: state["status"]
            for name, state in change_set["stages"].items()
        },
    })
    return summary


def build_ingestion_preflight(
    *,
    committed_manifest_path=DEFAULT_COMMITTED_MANIFEST,
    refresh_state_path=DEFAULT_REFRESH_STATE_PATH,
    report_discovery_state_path=DEFAULT_REPORT_DISCOVERY_STATE_PATH,
    report_detail_state_path=DEFAULT_REPORT_DETAIL_STATE_PATH,
    change_set_path=DEFAULT_CURRENT_CHANGE_SET_PATH,
):
    """Return a read-only local snapshot for an operator before ingestion.

    This function intentionally does not import or call API client code, create
    directories, write state, or select a rolling-refresh batch.
    """

    organization_manifest = _parquet_summary(
        committed_manifest_path,
        required_columns=("organization_id",),
    )
    refresh_state = _parquet_summary(
        refresh_state_path,
        required_columns=("organization_id",),
    )
    report_discovery = _parquet_summary(
        report_discovery_state_path,
        required_columns=("organization_id", "status"),
    )
    report_details = _parquet_summary(
        report_detail_state_path,
        required_columns=("report_id", "status"),
    )
    current_change_set = _change_set_summary(change_set_path)

    checks = {
        "committed_manifest_ready": (
            organization_manifest.get("readable") is True
            and not organization_manifest["missing_required_columns"]
        ),
        "refresh_state_ready": (
            refresh_state.get("readable") is True
            and not refresh_state["missing_required_columns"]
        ),
        "report_states_readable": (
            report_discovery.get("readable") is True
            and report_details.get("readable") is True
        ),
        "no_running_change_set": (
            not current_change_set.get("exists")
            or "running" not in current_change_set.get("stages", {}).values()
        ),
    }
    checks["ready_for_explicit_ingestion"] = all(checks.values())

    return {
        "mode": "read_only_preflight",
        "network_requests": 0,
        "writes": 0,
        "checks": checks,
        "organization_manifest": organization_manifest,
        "refresh_state": refresh_state,
        "report_discovery_state": report_discovery,
        "report_detail_state": report_details,
        "current_change_set": current_change_set,
    }
