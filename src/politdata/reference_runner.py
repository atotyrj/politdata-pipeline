from __future__ import annotations

from pathlib import Path
import json
import os
import shutil
import uuid

import pandas as pd

from .change_set import (
    DEFAULT_CURRENT_CHANGE_SET_PATH,
    load_change_set,
    save_change_set,
    set_change_set_stage_status,
)
from .dependency_planner import DEFAULT_PLAN_PATH
from .normalization.reference import (
    build_organization_reference,
    build_report_context,
)
from .promotion import (
    DEFAULT_DELTA_ROOT,
    load_promotion_state,
)
from .reference import (
    build_report_account_reference,
    build_state_funding_account_reference,
)


DEFAULT_NORMALIZED_ROOT = Path(
    "data/processed/normalized_v0_1"
)
DEFAULT_ANALYSIS_MANIFEST = Path(
    "data/interim/reports/analysis_selected_reports_manifest.parquet"
)
DEFAULT_REFERENCE_DELTA_ROOT = Path(
    "data/processed/reference_deltas_v0_1/runs"
)


def _read_json(path):
    with Path(path).open(
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def _read_filtered(path, column, values):
    values = sorted({str(value) for value in values})
    if values:
        return pd.read_parquet(
            path,
            filters=[(column, "in", values)],
        )
    frame = pd.read_parquet(path)
    return frame.iloc[0:0].copy()


def _replace_entity_rows(
    base,
    *,
    key_column,
    entity_ids,
    entity_index,
    delta_root,
    fragment_path,
):
    output = base.copy()
    output[key_column] = output[key_column].astype(str)

    for entity_id in sorted({str(value) for value in entity_ids}):
        indexed = entity_index.get(entity_id)
        if indexed is None:
            continue

        output = output[
            output[key_column] != entity_id
        ].copy()

        if indexed.get("deleted"):
            continue

        path = (
            Path(delta_root)
            / "runs"
            / indexed["run_id"]
            / fragment_path(entity_id)
        )
        if path.exists():
            fragment = pd.read_parquet(path)
            output = pd.concat(
                [output, fragment],
                ignore_index=True,
            )

    return output.reset_index(drop=True)


def _current_organizations(
    organization_ids,
    normalized_root,
    delta_root,
    promotion_state,
):
    organizations_path = normalized_root / "organizations.parquet"
    addresses_path = normalized_root / "organization_addresses.parquet"

    def load(ids):
        organizations = _read_filtered(
            organizations_path,
            "organization_id",
            ids,
        )
        organizations = _replace_entity_rows(
            organizations,
            key_column="organization_id",
            entity_ids=ids,
            entity_index=promotion_state["organizations"],
            delta_root=delta_root,
            fragment_path=lambda entity_id: (
                Path("organizations")
                / f"{entity_id}.parquet"
            ),
        )
        return organizations

    organizations = load(organization_ids)
    root_ids = sorted({
        str(value)
        for value in organizations["root_party_id"].dropna()
    })
    support_ids = sorted(
        set(organization_ids) | set(root_ids)
    )
    organizations = load(support_ids)
    addresses = _read_filtered(
        addresses_path,
        "organization_id",
        support_ids,
    )
    addresses = _replace_entity_rows(
        addresses,
        key_column="organization_id",
        entity_ids=support_ids,
        entity_index=promotion_state["organizations"],
        delta_root=delta_root,
        fragment_path=lambda entity_id: (
            Path("organization_addresses")
            / f"{entity_id}.parquet"
        ),
    )
    return organizations, addresses


def _current_report_artifact(
    *,
    base_path,
    filter_column,
    filter_values,
    report_ids,
    promotion_state,
    delta_root,
    fragment_path,
):
    base = _read_filtered(
        base_path,
        filter_column,
        filter_values,
    )
    return _replace_entity_rows(
        base,
        key_column="source_report_id",
        entity_ids=report_ids,
        entity_index=promotion_state["reports"],
        delta_root=delta_root,
        fragment_path=fragment_path,
    )


def build_incremental_reference_frames(
    plan,
    *,
    organizations,
    addresses,
    analysis_manifest,
    property_moneys,
    state_funding,
):
    """Build scoped reference frames using the validated builders."""

    affected_organization_ids = set(
        plan["closure"]["affected_organization_ids"]
    )
    deleted_organization_ids = set(
        plan["roots"]["deleted_organization_ids"]
    )
    active_organization_ids = (
        affected_organization_ids
        - deleted_organization_ids
    )
    affected_report_ids = set(
        plan["closure"]["affected_report_ids"]
    )

    full_org_ref = build_organization_reference(
        organizations,
        addresses,
    )
    organization_reference = full_org_ref[
        full_org_ref["organization_id"].astype(str).isin(
            active_organization_ids
        )
    ].reset_index(drop=True)

    manifest = analysis_manifest[
        analysis_manifest[
            "analysis_selected_report_id"
        ].astype(str).isin(affected_report_ids)
    ].copy()
    deleted_report_ids = set(
        manifest.loc[
            manifest["organization_id"].astype(str).isin(
                deleted_organization_ids
            ),
            "analysis_selected_report_id",
        ].astype(str)
    )
    active_report_ids = affected_report_ids - deleted_report_ids
    manifest = manifest[
        manifest["analysis_selected_report_id"].astype(str).isin(
            active_report_ids
        )
    ].copy()

    observed_report_ids = set(
        manifest["analysis_selected_report_id"].astype(str)
    )
    if observed_report_ids != active_report_ids:
        missing = sorted(active_report_ids - observed_report_ids)
        raise ValueError(
            "Affected reports missing from analysis manifest: "
            f"{missing}"
        )

    report_context = build_report_context(
        manifest,
        full_org_ref,
    )
    property_moneys = property_moneys[
        property_moneys["source_report_id"].astype(str).isin(
            active_report_ids
        )
    ].copy()
    report_account_reference = (
        build_report_account_reference(property_moneys)
    )
    state_funding = state_funding[
        state_funding["organization_id"].astype(str).isin(
            active_organization_ids
        )
    ].copy()
    state_funding_account_reference = (
        build_state_funding_account_reference(
            state_funding,
            full_org_ref,
        )
    )

    return {
        "organization_reference": organization_reference,
        "report_context": report_context,
        "report_account_reference": report_account_reference,
        "state_funding_account_reference":
            state_funding_account_reference,
        "deleted_organization_ids":
            sorted(deleted_organization_ids),
        "deleted_report_ids": sorted(deleted_report_ids),
    }


def run_incremental_references(
    change_set_path=DEFAULT_CURRENT_CHANGE_SET_PATH,
    plan_path=DEFAULT_PLAN_PATH,
    normalized_root=DEFAULT_NORMALIZED_ROOT,
    analysis_manifest_path=DEFAULT_ANALYSIS_MANIFEST,
    delta_root=DEFAULT_DELTA_ROOT,
    output_root=DEFAULT_REFERENCE_DELTA_ROOT,
):
    """Build and atomically publish one immutable reference delta run."""

    change_set = load_change_set(change_set_path)
    plan = _read_json(plan_path)
    run_id = change_set["run_id"]
    if plan.get("run_id") != run_id:
        raise ValueError("Dependency plan run_id does not match change set.")

    promotion_state = load_promotion_state(delta_root)
    normalized_root = Path(normalized_root)
    affected_orgs = plan["closure"]["affected_organization_ids"]
    affected_reports = plan["closure"]["affected_report_ids"]
    deleted_orgs = set(plan["roots"]["deleted_organization_ids"])

    organizations, addresses = _current_organizations(
        affected_orgs,
        normalized_root,
        delta_root,
        promotion_state,
    )
    analysis_manifest = pd.read_parquet(
        analysis_manifest_path
    )
    property_moneys = _current_report_artifact(
        base_path=(
            normalized_root / "properties" / "property_moneys.parquet"
        ),
        filter_column="source_report_id",
        filter_values=affected_reports,
        report_ids=affected_reports,
        promotion_state=promotion_state,
        delta_root=delta_root,
        fragment_path=lambda report_id: (
            Path("properties")
            / "property_moneys"
            / f"{report_id}.parquet"
        ),
    )
    active_orgs = sorted(set(affected_orgs) - deleted_orgs)
    report_ids_for_orgs = [
        report_id
        for report_id, item in promotion_state["reports"].items()
        if item["organization_id"] in active_orgs
    ]
    state_funding = _current_report_artifact(
        base_path=(
            normalized_root / "payments" / "state_funding.parquet"
        ),
        filter_column="organization_id",
        filter_values=active_orgs,
        report_ids=report_ids_for_orgs,
        promotion_state=promotion_state,
        delta_root=delta_root,
        fragment_path=lambda report_id: (
            Path("payments")
            / "state_funding"
            / f"{report_id}.parquet"
        ),
    )

    change_set = set_change_set_stage_status(
        change_set,
        "references",
        "running",
    )
    save_change_set(change_set, change_set_path)
    output_root = Path(output_root)
    destination = output_root / run_id
    if destination.exists():
        raise FileExistsError(destination)
    temp = output_root / (
        ".tmp." + run_id + "." + uuid.uuid4().hex
    )

    try:
        frames = build_incremental_reference_frames(
            plan,
            organizations=organizations,
            addresses=addresses,
            analysis_manifest=analysis_manifest,
            property_moneys=property_moneys,
            state_funding=state_funding,
        )
        temp.mkdir(parents=True, exist_ok=True)
        for name in (
            "organization_reference",
            "report_context",
            "report_account_reference",
            "state_funding_account_reference",
        ):
            frames[name].to_parquet(
                temp / f"{name}.parquet",
                index=False,
            )
        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "rows": {
                name: len(frames[name])
                for name in (
                    "organization_reference",
                    "report_context",
                    "report_account_reference",
                    "state_funding_account_reference",
                )
            },
            "deleted_organization_ids":
                frames["deleted_organization_ids"],
            "deleted_report_ids": frames["deleted_report_ids"],
        }
        with (temp / "manifest.json").open(
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(manifest, file, ensure_ascii=False, indent=2)
        output_root.mkdir(parents=True, exist_ok=True)
        os.replace(temp, destination)
        change_set = set_change_set_stage_status(
            change_set,
            "references",
            "completed",
        )
        save_change_set(change_set, change_set_path)
        return manifest
    except Exception as exc:
        if temp.exists():
            shutil.rmtree(temp)
        change_set = set_change_set_stage_status(
            change_set,
            "references",
            "failed",
            error=repr(exc),
        )
        save_change_set(change_set, change_set_path)
        raise
