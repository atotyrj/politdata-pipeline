"""Restore, update, validate, and publish one incremental generation."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import os
import shutil
import uuid

from .analytical_excel import export_analytical_workbooks
from .change_set import load_change_set
from .excel_snapshot import materialize_current_excel_inputs
from .ingestion_runner import run_limited_organization_ingestion
from .production_baseline import write_generation_manifest
from .qa import validate_enriched_output
from .storage import payload_hash


ANALYTICAL_EXCEL_CONTRACT_VERSION = 2


@contextmanager
def _working_directory(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _normalize_restored_layout(root: Path) -> None:
    """Migrate the initial root-level baseline to the normal data/ layout."""

    data = root / "data"
    data.mkdir(exist_ok=True)
    for name in ("raw", "interim", "processed"):
        legacy = root / name
        canonical = data / name
        if legacy.exists() and canonical.exists():
            raise RuntimeError(f"Both legacy and canonical restored paths exist: {name}")
        if legacy.exists():
            os.replace(legacy, canonical)


def _replace_outputs(root: Path, summary) -> dict:
    temporary = root / f".outputs.{uuid.uuid4().hex[:8]}.tmp"
    current = root / "outputs"
    backup = root / f".outputs.{uuid.uuid4().hex[:8]}.backup"
    snapshot = root / f".excel-inputs.{uuid.uuid4().hex[:8]}.tmp"
    try:
        inputs = materialize_current_excel_inputs(root, snapshot)
        qa = validate_enriched_output(
            inputs["enriched_root"],
            organization_reference=(
                inputs["enriched_root"]
                / "reference"
                / "organization_reference.parquet"
            ),
            enforce_regression_baseline=False,
        )
        generated = export_analytical_workbooks(
            enriched_root=inputs["enriched_root"],
            normalized_root=inputs["normalized_root"],
            output_dir=temporary,
        )
        summary.extend(generated)
        if current.exists():
            os.replace(current, backup)
        os.replace(temporary, current)
        shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        if current.exists():
            shutil.rmtree(current, ignore_errors=True)
        if backup.exists():
            os.replace(backup, current)
        raise
    finally:
        shutil.rmtree(snapshot, ignore_errors=True)
        shutil.rmtree(temporary, ignore_errors=True)
    return qa


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _published_excel_state(root: Path) -> dict:
    manifest_path = Path(root) / "generation_manifest.json"
    if not manifest_path.exists():
        return {}
    with manifest_path.open("r", encoding="utf-8") as stream:
        manifest = json.load(stream)
    return manifest.get("metadata", {})


def run_scheduled_incremental(
    store,
    work_root,
    generation_id,
    *,
    organization_limit=250,
    organization_all=False,
    report_discovery_limit=500,
    report_discovery_all_due=False,
    report_detail_limit=1000,
    report_details_all_pending=False,
    report_refresh_interval_days=7,
    code_revision=None,
    checkpoint_store=None,
):
    """Publish current data changes or repair stale analytical Excel outputs."""

    current = store.read_latest()
    if current is None:
        raise RuntimeError("No published baseline generation exists.")
    current_id = current["generation_id"]
    work_root = Path(work_root)
    store.restore_latest(work_root)
    _normalize_restored_layout(work_root)
    checkpoint_restore = None
    if checkpoint_store is not None:
        checkpoint_restore = checkpoint_store.restore_latest(
            work_root,
            base_generation_id=current_id,
        )

    with _working_directory(work_root):
        ingestion = run_limited_organization_ingestion(
            organization_limit=(
                int(organization_limit)
                if organization_limit is not None
                else None
            ),
            organization_all=bool(organization_all),
            report_discovery_limit=(
                int(report_discovery_limit)
                if report_discovery_limit is not None
                else None
            ),
            report_discovery_all_due=bool(report_discovery_all_due),
            report_limit=(
                int(report_detail_limit)
                if report_detail_limit is not None
                else None
            ),
            report_details_all_pending=bool(report_details_all_pending),
            report_refresh_interval_days=float(report_refresh_interval_days),
            run_downstream=True,
        )
        change_set = load_change_set("data/interim/change_sets/current.json")
        has_changes = bool(
            change_set["organization_changes"] or change_set["report_changes"]
        )
        analysis_manifest_hash = _file_hash(
            Path("data/interim/reports/analysis_selected_reports_manifest.parquet")
        )
        published_excel_state = _published_excel_state(work_root)
        excel_needs_refresh = bool(
            published_excel_state.get("analysis_manifest_hash")
            != analysis_manifest_hash
            or published_excel_state.get("analytical_excel_contract_version")
            != ANALYTICAL_EXCEL_CONTRACT_VERSION
        )
        if not has_changes and not excel_needs_refresh:
            checkpoint = None
            if checkpoint_store is not None:
                checkpoint = checkpoint_store.publish_checkpoint(
                    work_root,
                    generation_id,
                    base_generation_id=current_id,
                )
            return {
                "status": "no_changes",
                "previous_generation_id": current_id,
                "checkpoint_restore": checkpoint_restore,
                "checkpoint": checkpoint,
                "ingestion": ingestion,
            }

        excel_summary = []
        qa = _replace_outputs(work_root, excel_summary)
        manifest = write_generation_manifest(
            work_root,
            generation_id,
            mode="automatic-incremental",
            code_revision=code_revision,
            qa={
                "status": "passed",
                "payment_reference_identity_mismatches": int(
                    qa["payment_reference_identity"]["mismatches"].sum()
                ),
                "excel_workbooks": 17,
            },
            metadata={
                "previous_generation_id": current_id,
                "change_set_run_id": change_set["run_id"],
                "analysis_manifest_hash": analysis_manifest_hash,
                "analytical_excel_contract_version": (
                    ANALYTICAL_EXCEL_CONTRACT_VERSION
                ),
                "excel_refresh_without_source_changes": bool(
                    excel_needs_refresh and not has_changes
                ),
            },
            # publish_generation performs the complete checksum verification
            # immediately before upload, so avoid an identical extra pass here.
            verify=False,
        )

    generation_location = store.publish_generation(work_root, generation_id)
    latest_location = store.publish_latest(
        {
            "generation_id": generation_id,
            "generation_manifest_hash": payload_hash(manifest),
        },
        expected_generation_id=current_id,
    )
    return {
        "status": "published",
        "generation_id": generation_id,
        "generation_location": generation_location,
        "latest_location": latest_location,
    }
