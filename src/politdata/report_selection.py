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

    signed = instances[instances["is_signed"]].copy()
    signed["_signed_sort"] = pd.to_datetime(signed["signed_date"], errors="coerce")
    signed = signed.sort_values([*REPORT_KEY, "_signed_sort", "report_id"])
    selected = signed.merge(
        profile[list(REPORT_KEY) + ["selection_status"]],
        on=list(REPORT_KEY),
        how="inner",
    )
    selected = selected[selected["selection_status"].isin({
        "unique_signed", "multiple_signed",
    })]
    selected = selected.groupby(list(REPORT_KEY), dropna=False).tail(1)
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
        "multiple_signed": "latest_of_multiple_signed",
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


def merge_analysis_overrides(instances, previous_analysis):
    """Attach still-valid manual overrides to current official selections.

    Overrides are preserved only if their chosen report still belongs to the
    same logical period. Invalidated overrides are reported, never applied.
    """

    selected = instances[instances["is_selected_report"]].copy()
    selected["official_selected_report_id"] = selected["selected_report_id"]
    selected["analysis_selected_report_id"] = selected["selected_report_id"]
    selected["analysis_selection_method"] = "official_selected_signed_report"
    selected["analysis_override"] = False
    selected["override_reason"] = None
    selected["continuity_exact"] = None
    if previous_analysis.empty or "analysis_override" not in previous_analysis:
        return selected, pd.DataFrame()

    previous = previous_analysis[previous_analysis["analysis_override"]].copy()
    valid_report_keys = {
        (*key, str(report_id))
        for key, report_id in zip(
            instances[list(REPORT_KEY)].itertuples(index=False, name=None),
            instances["report_id"],
        )
    }
    invalid = []
    for _, override in previous.iterrows():
        key = tuple(override[column] for column in REPORT_KEY)
        chosen = str(override["analysis_selected_report_id"])
        mask = (selected[list(REPORT_KEY)] == pd.Series(key, index=REPORT_KEY)).all(axis=1)
        if (*key, chosen) not in valid_report_keys or not mask.any():
            invalid.append({**{column: override[column] for column in REPORT_KEY}, "analysis_selected_report_id": chosen})
            continue
        index = selected.index[mask][0]
        for column in ("analysis_selected_report_id", "analysis_selection_method", "analysis_override", "override_reason", "continuity_exact"):
            selected.at[index, column] = override[column]
    return selected, pd.DataFrame(invalid)


def analysis_detail_manifest(instances, analysis_manifest):
    """Resolve analysis-selected report IDs to detail-download rows.

    The analytical choice may intentionally differ from the official signed
    report.  Downloading ``instances[is_selected_report]`` would therefore
    silently normalize the wrong report after a full rebuild.
    """

    required_instances = {*REPORT_KEY, "report_id"}
    required_analysis = {*REPORT_KEY, "analysis_selected_report_id"}
    missing_instances = required_instances - set(instances.columns)
    missing_analysis = required_analysis - set(analysis_manifest.columns)
    if missing_instances:
        raise ValueError(
            f"Report instances missing columns: {sorted(missing_instances)}"
        )
    if missing_analysis:
        raise ValueError(
            f"Analysis manifest missing columns: {sorted(missing_analysis)}"
        )

    choices = analysis_manifest.copy()
    choices["report_id"] = choices["analysis_selected_report_id"].astype(str)
    instance_rows = instances.copy()
    instance_rows["report_id"] = instance_rows["report_id"].astype(str)
    join_key = [*REPORT_KEY, "report_id"]
    if instance_rows.duplicated(join_key).any():
        raise ValueError("Report instances contain duplicate logical report IDs.")

    analysis_columns = [
        column for column in choices.columns
        if column not in instance_rows.columns or column in join_key
    ]
    resolved = choices[analysis_columns].merge(
        instance_rows,
        on=join_key,
        how="left",
        validate="one_to_one",
        indicator=True,
        suffixes=("", "_instance"),
    )
    unresolved = resolved.loc[resolved["_merge"] != "both", join_key]
    if not unresolved.empty:
        raise ValueError(
            "Analysis-selected reports do not resolve to report instances: "
            f"{unresolved.to_dict('records')[:10]}"
        )
    resolved = resolved.drop(columns="_merge")
    if "analysis_selection_method" in resolved.columns:
        resolved["selection_method"] = resolved["analysis_selection_method"]
    return resolved
