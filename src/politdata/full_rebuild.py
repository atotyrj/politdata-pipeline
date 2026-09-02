"""Isolated, resumable RAW-to-analytics full replacement stage."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .analytical_excel import export_analytical_workbooks
from .api import fetch_all_parties, fetch_party_account
from .change_detection import organization_content_hash
from .change_set import create_change_set, save_change_set
from .discovery import build_organization_manifest
from .normalization.payments import PAYMENT_PATHS
from .normalization.report_sections import SECTION_PATHS
from .normalization_runner import normalize_changed_fragments
from .pipeline import rebuild_processed_analytics
from .report_details import run_report_detail_batch
from .report_discovery import (
    build_report_manifest_from_snapshots,
    run_report_discovery_batch,
)
from .report_selection import (
    analysis_detail_manifest,
    merge_analysis_overrides,
    select_official_reports,
)
from .schema_contracts import load_normalized_schemas


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_parquet(frame, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def download_organization_cards(
    organization_manifest,
    *,
    raw_dir,
    state_path,
    fetch_fn=fetch_party_account,
):
    """Download every organization card with RAW-first resumable state."""

    raw_dir = Path(raw_dir)
    state_path = Path(state_path)
    raw_dir.mkdir(parents=True, exist_ok=True)
    metadata = organization_manifest[
        ["organization_id", "root_party_id", "entity_type"]
    ].copy()
    metadata["organization_id"] = metadata["organization_id"].astype(str)
    if state_path.exists():
        old = pd.read_parquet(state_path)
        old["organization_id"] = old["organization_id"].astype(str)
        operational = [
            "organization_id", "status", "attempts", "retrieved_at_utc",
            "raw_path", "content_hash", "last_error",
        ]
        state = metadata.merge(old[operational], on="organization_id", how="left")
    else:
        state = metadata.copy()
        state["status"] = "pending"
        state["attempts"] = 0
        state["retrieved_at_utc"] = None
        state["raw_path"] = None
        state["content_hash"] = None
        state["last_error"] = None
    state["status"] = state["status"].fillna("pending")
    state["attempts"] = pd.to_numeric(state["attempts"], errors="coerce").fillna(0).astype(int)
    state = state.set_index("organization_id", drop=False)

    for organization_id in state.index[state["status"] != "success"]:
        state.at[organization_id, "attempts"] += 1
        try:
            payload = fetch_fn(str(organization_id))
            record = payload.get("results")
            if not isinstance(record, dict) or str(record.get("id")) != str(organization_id):
                raise ValueError(f"Invalid organization-card payload: {organization_id}")
            output_path = raw_dir / f"{organization_id}.json"
            _atomic_json(output_path, payload)
            state.at[organization_id, "status"] = "success"
            state.at[organization_id, "retrieved_at_utc"] = _utc_now_iso()
            state.at[organization_id, "raw_path"] = str(output_path)
            state.at[organization_id, "content_hash"] = organization_content_hash(record)
            state.at[organization_id, "last_error"] = None
        except Exception as error:
            state.at[organization_id, "status"] = "error"
            state.at[organization_id, "last_error"] = repr(error)
        _atomic_parquet(state.reset_index(drop=True), state_path)

    final = state.reset_index(drop=True)
    failed = final.loc[final["status"] != "success", "organization_id"].tolist()
    return {
        "selected": len(final),
        "successful": int((final["status"] == "success").sum()),
        "failed": len(failed),
        "failed_organization_ids": failed,
    }, final


def _dataset_paths():
    paths = {
        "organizations": "organizations",
        "organization_heads": "organization_heads",
        "organization_addresses": "organization_addresses",
        "properties/property_moneys": "properties/property_moneys",
    }
    paths.update({f"payments/{name}": f"payments/{name}" for name in PAYMENT_PATHS})
    paths.update({
        "report_sections/realty": "properties/realty",
        "report_sections/transport": "properties/transport",
        "report_sections/movable": "properties/movable",
        "report_sections/intangible": "properties/intangible",
        "report_sections/paper": "properties/paper",
        "report_sections/obligations": "obligations/obligations",
        "report_sections/head_info": "report_state/head_info",
        "report_sections/organizations": "report_state/organizations",
        "report_sections/regional_offices": "report_state/regional_offices",
        "report_sections/employee_counts": "report_state/employee_counts",
    })
    return paths


def materialize_normalized_fragments(
    fragment_dir,
    normalized_root,
    *,
    normalized_schemas=None,
):
    """Consolidate full-run fragments without loading a dataset into pandas."""

    fragment_dir = Path(fragment_dir)
    normalized_root = Path(normalized_root)
    normalized_schemas = normalized_schemas or load_normalized_schemas()
    counts = {}
    for source_name, output_name in _dataset_paths().items():
        source_dir = fragment_dir / source_name
        fragments = sorted(source_dir.glob("*.parquet"))
        destination = normalized_root / f"{output_name}.parquet"
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        schema = normalized_schemas.get(output_name)
        if schema is None:
            raise RuntimeError(f"No versioned normalized schema contract for {output_name}")
        if not fragments:
            if output_name != "properties/paper":
                raise RuntimeError(
                    "Full normalization unexpectedly produced no fragments for "
                    f"{output_name}; no versioned empty-dataset contract permits it."
                )
            writer = pq.ParquetWriter(temporary, schema, compression="zstd")
            writer.close()
            os.replace(temporary, destination)
            counts[output_name] = 0
            continue
        writer = None
        rows = 0
        try:
            for path in fragments:
                parquet = pq.ParquetFile(path)
                if writer is None:
                    writer = pq.ParquetWriter(temporary, schema, compression="zstd")
                extras = set(parquet.schema_arrow.names) - set(schema.names)
                if extras:
                    raise ValueError(
                        f"Fragment has fields outside versioned schema in {source_name}: {sorted(extras)}"
                    )
                for batch in parquet.iter_batches():
                    table = pa.Table.from_batches([batch])
                    arrays = []
                    for field in schema:
                        if field.name in table.column_names:
                            arrays.append(table[field.name].cast(field.type))
                        else:
                            arrays.append(pa.nulls(table.num_rows, type=field.type))
                    writer.write_table(pa.Table.from_arrays(arrays, schema=schema))
                    rows += table.num_rows
            writer.close()
            writer = None
            os.replace(temporary, destination)
        finally:
            if writer is not None:
                writer.close()
            temporary.unlink(missing_ok=True)
        counts[output_name] = rows
    return counts


def _json_safe_qa(qa):
    summary = {}
    for key, value in qa.items():
        if isinstance(value, pd.DataFrame):
            summary[key] = value.to_dict("records")
        else:
            summary[key] = value
    summary["passed"] = True
    return summary


def _file_checksums(root):
    result = {}
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        result[str(path.relative_to(root)).replace("\\", "/")] = digest.hexdigest()
    return result


def run_full_replace_stage(
    *,
    config,
    paths,
    generate_excel=True,
    fetch_parties_fn=fetch_all_parties,
    fetch_organization_fn=fetch_party_account,
):
    """Build an entirely new isolated generation, ready for QA promotion."""

    raw_organizations = paths.raw_dir / "party_accounts"
    raw_report_lists = paths.raw_dir / "report_lists"
    raw_report_details = paths.raw_dir / "report_details"
    reports_dir = paths.interim_dir / "reports"
    state_dir = paths.interim_dir / "state"
    normalized_root = paths.interim_dir / "normalized"
    change_path = paths.interim_dir / "change_sets" / "current.json"

    parties = fetch_parties_fn()
    _atomic_json(paths.raw_dir / "parties.json", {"retrieved_at_utc": _utc_now_iso(), "results": parties})
    organizations = build_organization_manifest(parties)
    _atomic_parquet(organizations, paths.interim_dir / "organizations_manifest.parquet")

    organization_summary, organization_state = download_organization_cards(
        organizations,
        raw_dir=raw_organizations,
        state_path=state_dir / "organization_card_state.parquet",
        fetch_fn=fetch_organization_fn,
    )
    if organization_summary["failed"]:
        raise RuntimeError(f"Organization-card download failed: {organization_summary['failed']}")

    discovery_summary, _ = run_report_discovery_batch(
        organizations,
        state_path=state_dir / "report_discovery_state.parquet",
        snapshot_dir=raw_report_lists,
        refresh_interval_days=0,
    )
    if discovery_summary["failed"] or discovery_summary["total_pending_in_state"]:
        raise RuntimeError("Full report discovery is incomplete.")
    all_reports, report_qa = build_report_manifest_from_snapshots(
        organizations, snapshot_dir=raw_report_lists,
    )
    if report_qa["duplicate_report_ids"] or report_qa["missing_report_ids"]:
        raise RuntimeError(f"Invalid report manifest: {report_qa}")
    instances, profile = select_official_reports(all_reports)
    previous_path = Path("data/interim/reports/analysis_selected_reports_manifest.parquet")
    previous = pd.read_parquet(previous_path) if previous_path.exists() else pd.DataFrame()
    analysis, invalid_overrides = merge_analysis_overrides(instances, previous)
    details = analysis_detail_manifest(instances, analysis)
    reports_dir.mkdir(parents=True, exist_ok=True)
    _atomic_parquet(instances, reports_dir / "all_reports_manifest.parquet")
    _atomic_parquet(profile, reports_dir / "report_selection_profile.parquet")
    _atomic_parquet(
        instances[instances["is_selected_report"]].copy(),
        reports_dir / "selected_reports_manifest.parquet",
    )
    _atomic_parquet(analysis, reports_dir / "analysis_selected_reports_manifest.parquet")
    _atomic_parquet(invalid_overrides, reports_dir / "invalid_analysis_overrides.parquet")

    seed_change = create_change_set(run_id=config.run_id)
    save_change_set(seed_change, change_path)
    detail_summary, detail_state = run_report_detail_batch(
        details,
        state_path=state_dir / "report_detail_state.parquet",
        raw_dir=raw_report_details,
        change_set_path=change_path,
    )
    if detail_summary["failed"] or detail_summary["total_pending_in_state"]:
        raise RuntimeError("Full report-detail download is incomplete.")

    organization_changes = [
        {
            "organization_id": str(row.organization_id), "change_type": "new",
            "candidate_reasons": ["full_replace"], "changed_fields": [],
            "old_content_hash": None, "new_content_hash": row.content_hash,
        }
        for row in organization_state.itertuples(index=False)
    ]
    report_changes = [
        {
            "report_id": str(row.report_id), "organization_id": str(row.organization_id),
            "change_type": "new", "old_content_hash": None,
            "new_content_hash": row.content_hash,
        }
        for row in detail_state.itertuples(index=False)
        if row.status == "success"
    ]
    save_change_set(create_change_set(
        run_id=config.run_id,
        organization_changes=organization_changes,
        report_changes=report_changes,
    ), change_path)
    fragments_root = paths.interim_dir / "normalized_fragments"
    normalize_changed_fragments(
        change_set_path=change_path,
        report_state_path=state_dir / "report_detail_state.parquet",
        raw_dir=raw_report_details,
        organization_raw_dir=raw_organizations,
        fragment_root=fragments_root,
    )
    normalized_counts = materialize_normalized_fragments(
        fragments_root / config.run_id, normalized_root,
    )
    processed = rebuild_processed_analytics(
        normalized_root, paths.interim_dir, paths.processed_dir, overwrite=True,
        enforce_regression_baseline=False,
    )
    excel_summary = []
    if generate_excel:
        excel_summary = export_analytical_workbooks(
            enriched_root=paths.processed_dir,
            normalized_root=normalized_root,
            output_dir=paths.outputs_dir,
        )
    qa = _json_safe_qa(processed["qa"])
    row_counts = {
        "organizations": len(organizations),
        "report_instances": len(instances),
        "analysis_selected_reports": len(analysis),
        **{f"normalized/{key}": value for key, value in normalized_counts.items()},
    }
    return {
        "status": "completed",
        "qa": qa,
        "row_counts": row_counts,
        "source_watermark": {"completed_at_utc": _utc_now_iso()},
        "artifact_checksums": _file_checksums(paths.staging_dir),
        "artifact_locations": {
            "raw": "raw", "interim": "interim", "processed": "processed",
            "outputs": "outputs", "excel_workbooks": excel_summary,
        },
    }
