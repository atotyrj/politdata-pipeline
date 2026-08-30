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
from .enrichment.payment_batch import (
    rebuild_payment_from_normalized_frame,
)
from .enrichment.report_sections import enrich_report_section_frame
from .normalization.payments import PAYMENT_PATHS


DEFAULT_FRAGMENT_ROOT = Path("data/interim/normalized_changes")
DEFAULT_REFERENCE_ROOT = Path("data/processed/enriched_v0_1/reference")
DEFAULT_REFERENCE_DELTA_ROOT = Path("data/processed/reference_deltas_v0_1/runs")
DEFAULT_ENRICHMENT_DELTA_ROOT = Path("data/processed/enriched_deltas_v0_1/runs")


def _read_json(path):
    with Path(path).open("r", encoding="utf-8") as file:
        return json.load(file)


def _overlay(base, delta, key, deleted=()):
    base = base.copy()
    delta = delta.copy()
    base[key] = base[key].astype(str)
    delta[key] = delta[key].astype(str)
    replaced = set(delta[key]) | {str(value) for value in deleted}
    return pd.concat(
        [base[~base[key].isin(replaced)], delta],
        ignore_index=True,
    )


def load_current_reference_overlay(
    report_ids,
    *,
    reference_root,
    reference_delta_dir,
):
    """Overlay this run's scoped references on the validated reference base."""

    reference_root = Path(reference_root)
    reference_delta_dir = Path(reference_delta_dir)
    manifest = _read_json(reference_delta_dir / "manifest.json")
    deleted_orgs = manifest.get("deleted_organization_ids", [])
    deleted_reports = manifest.get("deleted_report_ids", [])

    specs = {
        "organization_reference": ("organization_id", deleted_orgs, None),
        "report_context": ("source_report_id", deleted_reports, report_ids),
        "report_account_reference": (
            "source_report_id", deleted_reports, report_ids,
        ),
        "state_funding_account_reference": (
            "organization_id", deleted_orgs, None,
        ),
    }
    result = {}
    for name, (key, deleted, filtered_ids) in specs.items():
        path = reference_root / f"{name}.parquet"
        if filtered_ids:
            base = pd.read_parquet(
                path,
                filters=[(key, "in", sorted(set(filtered_ids)))],
            )
        else:
            base = pd.read_parquet(path)
        delta = pd.read_parquet(reference_delta_dir / f"{name}.parquet")
        result[name] = _overlay(base, delta, key, deleted)
    return result


def run_incremental_enrichment(
    change_set_path=DEFAULT_CURRENT_CHANGE_SET_PATH,
    plan_path=DEFAULT_PLAN_PATH,
    fragment_root=DEFAULT_FRAGMENT_ROOT,
    reference_root=DEFAULT_REFERENCE_ROOT,
    reference_delta_root=DEFAULT_REFERENCE_DELTA_ROOT,
    output_root=DEFAULT_ENRICHMENT_DELTA_ROOT,
):
    """Enrich only affected payment and report-section fragments atomically."""

    change_set = load_change_set(change_set_path)
    plan = _read_json(plan_path)
    run_id = change_set["run_id"]
    if plan.get("run_id") != run_id:
        raise ValueError("Dependency plan run_id does not match change set.")

    report_ids = sorted(set(plan["closure"]["affected_report_ids"]))
    fragments = Path(fragment_root) / run_id
    references_dir = Path(reference_delta_root) / run_id
    if not (fragments / "manifest.json").is_file():
        raise FileNotFoundError(fragments / "manifest.json")
    if not (references_dir / "manifest.json").is_file():
        raise FileNotFoundError(references_dir / "manifest.json")

    references = load_current_reference_overlay(
        report_ids,
        reference_root=reference_root,
        reference_delta_dir=references_dir,
    )
    output_root = Path(output_root)
    destination = output_root / run_id
    if destination.exists():
        raise FileExistsError(destination)
    temp = output_root / (".tmp." + run_id + "." + uuid.uuid4().hex)

    change_set = set_change_set_stage_status(
        change_set, "enrichment", "running"
    )
    save_change_set(change_set, change_set_path)

    try:
        rows = {"payments": {}, "report_sections": {}}
        for report_id in report_ids:
            for section in PAYMENT_PATHS:
                source = fragments / "payments" / section / f"{report_id}.parquet"
                if not source.exists():
                    rows["payments"].setdefault(section, 0)
                    continue
                normalized = pd.read_parquet(source)
                enriched = rebuild_payment_from_normalized_frame(
                    normalized,
                    section=section,
                    report_context=references["report_context"],
                    organization_reference=references["organization_reference"],
                    report_account_reference=
                        references["report_account_reference"],
                    state_account_reference=
                        references["state_funding_account_reference"],
                )
                target = temp / "payments" / section / f"{report_id}.parquet"
                target.parent.mkdir(parents=True, exist_ok=True)
                enriched.to_parquet(target, index=False)
                rows["payments"][section] = (
                    rows["payments"].get(section, 0) + len(enriched)
                )

            sections_root = fragments / "report_sections"
            if sections_root.exists():
                for section_dir in sections_root.iterdir():
                    source = section_dir / f"{report_id}.parquet"
                    if not source.exists():
                        continue
                    normalized = pd.read_parquet(source)
                    enriched = enrich_report_section_frame(
                        normalized,
                        report_context=references["report_context"],
                    )
                    target = (
                        temp / "report_sections" / section_dir.name
                        / f"{report_id}.parquet"
                    )
                    target.parent.mkdir(parents=True, exist_ok=True)
                    enriched.to_parquet(target, index=False)
                    rows["report_sections"][section_dir.name] = (
                        rows["report_sections"].get(section_dir.name, 0)
                        + len(enriched)
                    )

        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "affected_report_ids": report_ids,
            "rows": rows,
        }
        temp.mkdir(parents=True, exist_ok=True)
        with (temp / "manifest.json").open("w", encoding="utf-8") as file:
            json.dump(manifest, file, ensure_ascii=False, indent=2)
        output_root.mkdir(parents=True, exist_ok=True)
        os.replace(temp, destination)
        change_set = set_change_set_stage_status(
            change_set, "enrichment", "completed"
        )
        save_change_set(change_set, change_set_path)
        return manifest
    except Exception as exc:
        if temp.exists():
            shutil.rmtree(temp)
        change_set = set_change_set_stage_status(
            change_set, "enrichment", "failed", error=repr(exc)
        )
        save_change_set(change_set, change_set_path)
        raise
