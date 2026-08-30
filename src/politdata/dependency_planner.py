from __future__ import annotations

from pathlib import Path
import json
import os
import uuid

import pandas as pd

from .change_set import (
    DEFAULT_CURRENT_CHANGE_SET_PATH,
    load_change_set,
)
from .promotion import (
    DEFAULT_DELTA_ROOT,
    load_promotion_state,
)


DEFAULT_REFERENCE_ROOT = Path(
    "data/processed/enriched_v0_1/reference"
)
DEFAULT_PLAN_PATH = Path(
    "data/interim/change_sets/dependency_plan.json"
)


def _sorted_ids(values):
    return sorted({
        str(value)
        for value in values
        if value is not None and not pd.isna(value)
    })


def _require_columns(frame, name, columns):
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError(
            f"{name} missing columns: {sorted(missing)}"
        )


def _validate_promoted_run(change_set, promotion_state):
    run_id = change_set["run_id"]
    promoted_ids = {
        item["run_id"]
        for item in promotion_state["runs"]
    }
    if run_id not in promoted_ids:
        raise ValueError(
            f"Normalized run is not promoted: {run_id}"
        )

    for change in change_set["organization_changes"]:
        organization_id = str(change["organization_id"])
        indexed = promotion_state["organizations"].get(
            organization_id
        )
        if indexed is None or indexed["run_id"] != run_id:
            raise ValueError(
                "Promoted organization index does not match run: "
                f"{organization_id}"
            )

    for change in change_set["report_changes"]:
        report_id = str(change["report_id"])
        indexed = promotion_state["reports"].get(report_id)
        if indexed is None or indexed["run_id"] != run_id:
            raise ValueError(
                "Promoted report index does not match run: "
                f"{report_id}"
            )


def build_incremental_dependency_plan(
    change_set,
    organization_reference,
    report_context,
    *,
    promotion_state=None,
):
    """Compute conservative downstream closure from changed entities."""

    _require_columns(
        organization_reference,
        "organization_reference",
        ("organization_id", "root_party_id"),
    )
    _require_columns(
        report_context,
        "report_context",
        (
            "source_report_id",
            "organization_id",
            "root_party_id",
        ),
    )

    if promotion_state is not None:
        _validate_promoted_run(
            change_set,
            promotion_state,
        )

    explicit_organization_ids = _sorted_ids(
        item["organization_id"]
        for item in change_set["organization_changes"]
    )
    direct_report_ids = _sorted_ids(
        item["report_id"]
        for item in change_set["report_changes"]
    )
    direct_report_organization_ids = _sorted_ids(
        item["organization_id"]
        for item in change_set["report_changes"]
    )

    organization_reference = organization_reference.copy()
    organization_reference["organization_id"] = (
        organization_reference["organization_id"].astype(str)
    )
    organization_reference["root_party_id"] = (
        organization_reference["root_party_id"].astype(str)
    )
    root_dependents = organization_reference[
        organization_reference["root_party_id"].isin(
            explicit_organization_ids
        )
    ]["organization_id"].tolist()
    affected_organization_ids = _sorted_ids(
        explicit_organization_ids
        + direct_report_organization_ids
        + root_dependents
    )

    report_context = report_context.copy()
    report_context["source_report_id"] = (
        report_context["source_report_id"].astype(str)
    )
    report_context["organization_id"] = (
        report_context["organization_id"].astype(str)
    )
    report_context["root_party_id"] = (
        report_context["root_party_id"].astype(str)
    )
    dependent_mask = (
        report_context["organization_id"].isin(
            affected_organization_ids
        )
        |
        report_context["root_party_id"].isin(
            explicit_organization_ids
        )
    )
    organization_dependent_report_ids = _sorted_ids(
        report_context.loc[
            dependent_mask,
            "source_report_id",
        ]
    )
    affected_report_ids = _sorted_ids(
        direct_report_ids
        + organization_dependent_report_ids
    )
    deleted_organization_ids = _sorted_ids(
        item["organization_id"]
        for item in change_set["organization_changes"]
        if item["change_type"] == "disappeared"
    )

    return {
        "schema_version": 1,
        "run_id": change_set["run_id"],
        "roots": {
            "explicit_organization_ids":
                explicit_organization_ids,
            "direct_report_ids": direct_report_ids,
            "direct_report_organization_ids":
                direct_report_organization_ids,
            "deleted_organization_ids":
                deleted_organization_ids,
        },
        "closure": {
            "affected_organization_ids":
                affected_organization_ids,
            "root_dependent_organization_ids":
                _sorted_ids(root_dependents),
            "organization_dependent_report_ids":
                organization_dependent_report_ids,
            "affected_report_ids": affected_report_ids,
        },
        "reference_updates": {
            "organization_reference": {
                "organization_ids":
                    affected_organization_ids,
            },
            "report_context": {
                "report_ids": affected_report_ids,
            },
            "report_account_reference": {
                "report_ids": affected_report_ids,
            },
            "state_funding_account_reference": {
                "organization_ids":
                    affected_organization_ids,
            },
        },
        "enrichment_updates": {
            "payments": {
                "report_ids": affected_report_ids,
            },
            "report_sections": {
                "report_ids": affected_report_ids,
            },
        },
        "qa_scope": {
            "organization_ids": affected_organization_ids,
            "report_ids": affected_report_ids,
            "require_delta_reconciliation": True,
        },
    }


def save_dependency_plan(plan, path=DEFAULT_PLAN_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(
        path.name + ".tmp." + uuid.uuid4().hex
    )
    try:
        with temp_path.open(
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                plan,
                file,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return path


def plan_incremental_dependencies(
    change_set_path=DEFAULT_CURRENT_CHANGE_SET_PATH,
    delta_root=DEFAULT_DELTA_ROOT,
    reference_root=DEFAULT_REFERENCE_ROOT,
    output_path=DEFAULT_PLAN_PATH,
):
    """Load promoted state and narrow reference indexes, then save a plan."""

    change_set = load_change_set(change_set_path)
    promotion_state = load_promotion_state(delta_root)
    reference_root = Path(reference_root)
    organization_reference = pd.read_parquet(
        reference_root / "organization_reference.parquet",
        columns=["organization_id", "root_party_id"],
    )
    report_context = pd.read_parquet(
        reference_root / "report_context.parquet",
        columns=[
            "source_report_id",
            "organization_id",
            "root_party_id",
        ],
    )
    plan = build_incremental_dependency_plan(
        change_set,
        organization_reference,
        report_context,
        promotion_state=promotion_state,
    )
    save_dependency_plan(plan, output_path)
    return plan
