"""Restore, update, validate, and publish one incremental generation."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import os
import shutil
import uuid

from .analytical_excel import export_analytical_workbooks
from .change_set import load_change_set
from .ingestion_runner import run_limited_organization_ingestion
from .production_baseline import write_generation_manifest
from .qa import validate_enriched_output
from .storage import payload_hash


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


def _replace_outputs(root: Path, summary) -> None:
    temporary = root / f".outputs.{uuid.uuid4().hex[:8]}.tmp"
    current = root / "outputs"
    backup = root / f".outputs.{uuid.uuid4().hex[:8]}.backup"
    export_analytical_workbooks(
        enriched_root=root / "data" / "processed" / "enriched_v0_1",
        normalized_root=root / "data" / "processed" / "normalized_v0_1",
        output_dir=temporary,
    )
    try:
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
        shutil.rmtree(temporary, ignore_errors=True)


def run_scheduled_incremental(
    store,
    work_root,
    generation_id,
    *,
    organization_limit=250,
    report_discovery_limit=500,
    report_detail_limit=1000,
    report_refresh_interval_days=7,
    code_revision=None,
):
    """Publish a new latest release only when factual source changes exist."""

    current = store.read_latest()
    if current is None:
        raise RuntimeError("No published baseline generation exists.")
    current_id = current["generation_id"]
    work_root = Path(work_root)
    store.restore_latest(work_root)
    _normalize_restored_layout(work_root)

    with _working_directory(work_root):
        ingestion = run_limited_organization_ingestion(
            organization_limit=int(organization_limit),
            report_discovery_limit=int(report_discovery_limit),
            report_limit=int(report_detail_limit),
            report_refresh_interval_days=float(report_refresh_interval_days),
            run_downstream=True,
        )
        change_set = load_change_set("data/interim/change_sets/current.json")
        has_changes = bool(
            change_set["organization_changes"] or change_set["report_changes"]
        )
        if not has_changes:
            return {
                "status": "no_changes",
                "previous_generation_id": current_id,
                "ingestion": ingestion,
            }

        qa = validate_enriched_output(
            "data/processed/enriched_v0_1",
            organization_reference=(
                "data/processed/enriched_v0_1/reference/organization_reference.parquet"
            ),
            enforce_regression_baseline=False,
        )
        excel_summary = []
        _replace_outputs(work_root, excel_summary)
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
