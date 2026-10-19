"""Materialize the current incremental state for analytical Excel export."""

from __future__ import annotations

from pathlib import Path
import json
import os
import uuid

import pandas as pd

from .enrichment.report_sections import SECTION_PATHS as ENRICHED_SECTION_PATHS
from .normalization.payments import PAYMENT_PATHS
from .normalization.reference import (
    build_organization_reference,
    build_report_context,
)
from .promotion import load_promotion_state


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _current_entity_frame(
    base_path: Path,
    *,
    entity_key: str,
    entity_index: dict,
    runs_root: Path,
    fragment_path,
) -> pd.DataFrame:
    """Overlay the latest entity fragments on one monolithic baseline."""

    base = pd.read_parquet(base_path)
    base[entity_key] = base[entity_key].astype("string")
    replaced = {str(value) for value in entity_index}
    frames = [base.loc[~base[entity_key].isin(replaced)].copy()]
    for entity_id, item in sorted(entity_index.items()):
        if item.get("deleted"):
            continue
        path = runs_root / item["run_id"] / fragment_path(str(entity_id))
        if path.exists():
            frames.append(pd.read_parquet(path))
    return pd.concat(frames, ignore_index=True, sort=False)


def materialize_current_reference_inputs(
    generation_root: Path,
    destination_root: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build current organizations and report context from the delta index."""

    generation_root = Path(generation_root)
    destination_root = Path(destination_root)
    data_root = generation_root / "data"
    normalized_root = data_root / "processed" / "normalized_v0_1"
    delta_root = data_root / "processed" / "normalized_deltas_v0_1"
    state = load_promotion_state(delta_root)
    runs_root = delta_root / "runs"

    organizations = _current_entity_frame(
        normalized_root / "organizations.parquet",
        entity_key="organization_id",
        entity_index=state["organizations"],
        runs_root=runs_root,
        fragment_path=lambda organization_id: (
            Path("organizations") / f"{organization_id}.parquet"
        ),
    )
    addresses = _current_entity_frame(
        normalized_root / "organization_addresses.parquet",
        entity_key="organization_id",
        entity_index=state["organizations"],
        runs_root=runs_root,
        fragment_path=lambda organization_id: (
            Path("organization_addresses") / f"{organization_id}.parquet"
        ),
    )
    snapshot_normalized = destination_root / "normalized"
    _atomic_parquet(organizations, snapshot_normalized / "organizations.parquet")
    _atomic_parquet(
        addresses,
        snapshot_normalized / "organization_addresses.parquet",
    )

    organization_reference = build_organization_reference(
        organizations,
        addresses,
    )
    analysis_manifest = pd.read_parquet(
        data_root
        / "interim"
        / "reports"
        / "analysis_selected_reports_manifest.parquet"
    )
    report_context = build_report_context(
        analysis_manifest,
        organization_reference,
    )
    if "continuity_exact" not in report_context.columns:
        report_context["continuity_exact"] = False
    expected_ids = set(
        analysis_manifest["analysis_selected_report_id"].astype(str)
    )
    actual_ids = set(report_context["source_report_id"].astype(str))
    if actual_ids != expected_ids:
        raise RuntimeError(
            "Current report context does not match the analysis manifest."
        )

    snapshot_reference = destination_root / "enriched" / "reference"
    _atomic_parquet(
        organization_reference,
        snapshot_reference / "organization_reference.parquet",
    )
    _atomic_parquet(
        report_context,
        snapshot_reference / "report_context.parquet",
    )
    return organization_reference, report_context


def _current_report_frame(
    base_path: Path,
    *,
    report_index: dict,
    runs_root: Path,
    fragment_path,
    selected_report_ids: set[str],
) -> pd.DataFrame:
    """Overlay current report fragments and remove obsolete selections."""

    base = pd.read_parquet(base_path)
    if "source_report_id" not in base.columns:
        raise KeyError(f"{base_path} is missing source_report_id")
    base["source_report_id"] = base["source_report_id"].astype("string")
    replaced = {str(value) for value in report_index}
    frames = [base.loc[~base["source_report_id"].isin(replaced)].copy()]
    for report_id, item in sorted(report_index.items()):
        if item.get("deleted"):
            continue
        path = runs_root / item["run_id"] / fragment_path(str(report_id))
        if path.exists():
            frames.append(pd.read_parquet(path))
    current = pd.concat(frames, ignore_index=True, sort=False)
    current["source_report_id"] = current["source_report_id"].astype("string")
    return current.loc[
        current["source_report_id"].isin(selected_report_ids)
    ].reset_index(drop=True)


def _current_enrichment_report_index(state: dict, runs_root: Path) -> dict:
    """Resolve the latest enrichment run for every directly or indirectly affected report."""

    index = {}
    for run in state["runs"]:
        run_id = run["run_id"]
        manifest_path = Path(runs_root) / run_id / "manifest.json"
        if not manifest_path.exists():
            continue
        with manifest_path.open("r", encoding="utf-8") as stream:
            manifest = json.load(stream)
        for report_id in manifest.get("affected_report_ids", []):
            index[str(report_id)] = {"run_id": run_id, "deleted": False}
    return index


def materialize_current_excel_inputs(
    generation_root: Path,
    destination_root: Path,
) -> dict:
    """Create a disposable monolithic snapshot consumed by the Excel exporter.

    The immutable baseline and incremental fragments remain unchanged.
    """

    generation_root = Path(generation_root)
    destination_root = Path(destination_root)
    data_root = generation_root / "data"
    normalized_root = data_root / "processed" / "normalized_v0_1"
    enriched_root = data_root / "processed" / "enriched_v0_1"
    delta_root = data_root / "processed" / "normalized_deltas_v0_1"
    state = load_promotion_state(delta_root)
    normalized_runs = delta_root / "runs"
    enriched_runs = (
        data_root / "processed" / "enriched_deltas_v0_1" / "runs"
    )

    _, report_context = materialize_current_reference_inputs(
        generation_root,
        destination_root,
    )
    selected_report_ids = set(report_context["source_report_id"].astype(str))
    report_index = state["reports"]
    enrichment_report_index = _current_enrichment_report_index(
        state,
        enriched_runs,
    )
    snapshot_enriched = destination_root / "enriched"
    snapshot_normalized = destination_root / "normalized"

    property_moneys = _current_report_frame(
        normalized_root / "properties" / "property_moneys.parquet",
        report_index=report_index,
        runs_root=normalized_runs,
        fragment_path=lambda report_id: (
            Path("properties") / "property_moneys" / f"{report_id}.parquet"
        ),
        selected_report_ids=selected_report_ids,
    )
    _atomic_parquet(
        property_moneys,
        snapshot_enriched / "properties" / "property_moneys.parquet",
    )

    for payment_type in PAYMENT_PATHS:
        frame = _current_report_frame(
            enriched_root / "payments" / f"{payment_type}.parquet",
            report_index=enrichment_report_index,
            runs_root=enriched_runs,
            fragment_path=lambda report_id, section=payment_type: (
                Path("payments") / section / f"{report_id}.parquet"
            ),
            selected_report_ids=selected_report_ids,
        )
        _atomic_parquet(
            frame,
            snapshot_enriched / "payments" / f"{payment_type}.parquet",
        )

    for section, relative_path in ENRICHED_SECTION_PATHS.items():
        frame = _current_report_frame(
            enriched_root / relative_path,
            report_index=enrichment_report_index,
            runs_root=enriched_runs,
            fragment_path=lambda report_id, name=section: (
                Path("report_sections") / name / f"{report_id}.parquet"
            ),
            selected_report_ids=selected_report_ids,
        )
        _atomic_parquet(frame, snapshot_enriched / relative_path)

    return {
        "normalized_root": snapshot_normalized,
        "enriched_root": snapshot_enriched,
        "selected_reports": len(selected_report_ids),
    }
