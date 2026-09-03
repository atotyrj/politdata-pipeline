"""Assemble a validated local generation without re-downloading source data."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import shutil

from .storage import atomic_json, file_hash, verify_generation


RAW_COMPONENTS = (
    "organization_details",
    "parties",
    "party_account_versions",
    "party_accounts",
    "report_details",
    "report_lists",
)
INTERIM_COMPONENTS = (
    "change_sets",
    "manifests",
    "reports",
    "schema",
    "state",
    "state_funding",
    "organization_manifest.parquet",
)


def _link_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def _materialize_component(source: Path, destination: Path) -> int:
    if source.is_file():
        _link_or_copy(source, destination)
        return 1
    if not source.is_dir():
        raise FileNotFoundError(source)
    count = 0
    for candidate in sorted(source.rglob("*")):
        if candidate.is_symlink():
            raise RuntimeError(f"Baseline source contains a symbolic link: {candidate}")
        if candidate.is_file():
            _link_or_copy(candidate, destination / candidate.relative_to(source))
            count += 1
    return count


def write_generation_manifest(
    generation_root,
    generation_id,
    *,
    mode,
    code_revision=None,
    qa=None,
    metadata=None,
    verify=True,
):
    """Hash every generation artifact and write its integrity manifest."""

    generation_root = Path(generation_root)
    manifest_path = generation_root / "generation_manifest.json"
    manifest_path.unlink(missing_ok=True)
    checksums = {}
    for artifact in sorted(generation_root.rglob("*")):
        if artifact.is_file():
            checksums[artifact.relative_to(generation_root).as_posix()] = file_hash(
                artifact
            )
    manifest = {
        "schema_version": 1,
        "generation_id": str(generation_id),
        "mode": str(mode),
        "status": "validated",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "code_revision": code_revision,
        "artifact_checksums": checksums,
        "qa": qa or {"status": "passed"},
        **dict(metadata or {}),
    }
    atomic_json(manifest_path, manifest)
    if verify:
        verify_generation(generation_root)
    return manifest


def assemble_existing_baseline(
    destination,
    generation_id,
    *,
    raw_root="data/raw",
    interim_root="data/interim",
    processed_root="data/processed",
    excel_root="outputs/analytical_excel_simplified_2026_09_02",
    code_revision=None,
    qa=None,
):
    """Build an immutable-generation directory from the current validated state.

    Probe captures, historical backups, and validation scratch space are excluded.
    Files are hard-linked when supported so the assembly does not duplicate the
    local dataset; bundle creation remains independent and checksum-verified.
    """

    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    destination.mkdir(parents=True)
    counts = {"raw": 0, "interim": 0, "processed": 0, "outputs": 0}
    try:
        raw_root = Path(raw_root)
        for name in RAW_COMPONENTS:
            counts["raw"] += _materialize_component(
                raw_root / name, destination / "raw" / name
            )

        interim_root = Path(interim_root)
        for name in INTERIM_COMPONENTS:
            source = interim_root / name
            if source.exists():
                counts["interim"] += _materialize_component(
                    source, destination / "interim" / name
                )

        counts["processed"] = _materialize_component(
            Path(processed_root), destination / "processed"
        )

        excel_root = Path(excel_root)
        workbooks = sorted(excel_root.glob("*.xlsx"))
        if not workbooks:
            raise RuntimeError(f"No Excel workbooks found in {excel_root}")
        for workbook in workbooks:
            _link_or_copy(workbook, destination / "outputs" / workbook.name)
        counts["outputs"] = len(workbooks)

        manifest = write_generation_manifest(
            destination,
            generation_id,
            mode="existing-local-baseline",
            code_revision=code_revision,
            qa=qa,
            metadata={"artifact_counts": counts},
        )
        return manifest
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
