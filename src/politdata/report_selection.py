"""Deterministic, conservative selection of report instances."""

from __future__ import annotations

import pandas as pd


REPORT_KEY = ("organization_id", "year", "quarter")
REQUIRED_COLUMNS = ("report_id", *REPORT_KEY, "signed_date")


def select_official_reports(reports):
    """Select one report only for a logical period with exactly one signature.

    Ambiguous periods are retained in the returned profile but intentionally
    produce no selected report ID. An operator may later apply a separately
    auditable analytical override; this function never invents one.
    """

    missing = set(REQUIRED_COLUMNS) - set(reports.columns)
    if missing:
        raise ValueError(f"Missing report-selection columns: {sorted(missing)}")

    instances = reports.copy()
    instances["report_id"] = instances["report_id"].astype(str)
    instances["is_signed"] = instances["signed_date"].notna()
    profile = (
        instances.groupby(list(REPORT_KEY), dropna=False)
        .agg(
            report_instance_count=("report_id", "size"),
            signed_instance_count=("is_signed", "sum"),
        )
        .reset_index()
    )
    profile["unsigned_instance_count"] = (
        profile["report_instance_count"] - profile["signed_instance_count"]
    )
    profile["selection_status"] = "no_signed"
    profile.loc[
        profile["signed_instance_count"] == 1, "selection_status"
    ] = "unique_signed"
    profile.loc[
        profile["signed_instance_count"] > 1, "selection_status"
    ] = "multiple_signed"

    selected = instances[instances["is_signed"]].merge(
        profile[list(REPORT_KEY) + ["selection_status"]],
        on=list(REPORT_KEY),
        how="inner",
    )
    selected = selected[selected["selection_status"] == "unique_signed"]
    selected = selected[list(REPORT_KEY) + ["report_id"]].rename(
        columns={"report_id": "selected_report_id"}
    )
    profile = profile.merge(selected, on=list(REPORT_KEY), how="left")
    instances = instances.merge(profile, on=list(REPORT_KEY), how="left")
    instances["is_selected_report"] = (
        instances["report_id"] == instances["selected_report_id"]
    )
    instances["selection_method"] = instances["selection_status"].map({
        "unique_signed": "unique_signed",
    })
    instances["instance_selection_role"] = "unsigned_unresolved"
    instances.loc[
        (instances["selection_status"] == "unique_signed")
        & instances["is_selected_report"],
        "instance_selection_role",
    ] = "selected"
    instances.loc[
        (instances["selection_status"] == "unique_signed")
        & ~instances["is_selected_report"],
        "instance_selection_role",
    ] = "nonselected_unsigned_instance"
    instances.loc[
        (instances["selection_status"] == "multiple_signed")
        & instances["is_signed"],
        "instance_selection_role",
    ] = "ambiguous_signed_candidate"
    instances.loc[
        (instances["selection_status"] == "multiple_signed")
        & ~instances["is_signed"],
        "instance_selection_role",
    ] = "nonselected_unsigned_instance"
    return instances, profile


def selected_report_manifest(reports):
    """Return detail-download candidates and preserve the selection audit."""

    instances, profile = select_official_reports(reports)
    return instances[instances["is_selected_report"]].copy(), profile
