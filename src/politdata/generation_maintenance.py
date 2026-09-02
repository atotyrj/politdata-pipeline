"""Retention, rollback, and public catalog operations for immutable generations."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import PurePosixPath

from .storage import LatestConflictError, atomic_json, payload_hash


PUBLIC_ARTIFACT_PREFIXES = ("processed/", "outputs/")
CATALOG_SCHEMA_VERSION = 1


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _generation_sort_key(record):
    return (str(record.get("completed_at_utc") or ""), record["generation_id"])


def _generation_records(store):
    records = []
    for generation_id in store.list_generation_ids():
        manifest = store.read_generation_manifest(generation_id)
        manifest_id = str(manifest.get("generation_id") or "")
        if manifest_id != str(generation_id):
            raise ValueError(
                f"Generation directory and manifest disagree: {generation_id}"
            )
        records.append(
            {
                "generation_id": str(generation_id),
                "completed_at_utc": manifest.get("completed_at_utc"),
                "generation_manifest_hash": payload_hash(manifest),
                "manifest": manifest,
            }
        )
    return sorted(records, key=_generation_sort_key, reverse=True)


def build_retention_plan(store, *, keep_latest=3, protected_generation_ids=()):
    """Build a read-only, hash-bound deletion plan for old generations."""

    keep_latest = int(keep_latest)
    if keep_latest < 1:
        raise ValueError("keep_latest must be at least 1.")
    records = _generation_records(store)
    current = store.read_latest()
    current_id = str(current.get("generation_id")) if current else None
    protected = {str(value) for value in protected_generation_ids}
    if current_id:
        protected.add(current_id)
    protected.update(record["generation_id"] for record in records[:keep_latest])

    delete = [
        {
            "generation_id": record["generation_id"],
            "generation_manifest_hash": record["generation_manifest_hash"],
            "completed_at_utc": record["completed_at_utc"],
        }
        for record in records
        if record["generation_id"] not in protected
    ]
    keep = [
        record["generation_id"]
        for record in records
        if record["generation_id"] in protected
    ]
    return {
        "schema_version": 1,
        "status": "planned",
        "current_generation_id": current_id,
        "keep_latest": keep_latest,
        "protected_generation_ids": sorted(protected),
        "keep_generation_ids": keep,
        "delete": delete,
    }


def apply_retention_plan(store, plan, *, expected_current_generation_id):
    """Apply a previously built plan only if latest still matches expectation."""

    expected = str(expected_current_generation_id)
    planned_current = plan.get("current_generation_id")
    if planned_current != expected:
        raise LatestConflictError(
            f"Retention plan current generation differs: plan={planned_current}, expected={expected}"
        )
    current = store.read_latest()
    actual = str(current.get("generation_id")) if current else None
    if actual != expected:
        raise LatestConflictError(
            f"Latest generation changed: expected={expected}, actual={actual}"
        )

    deleted = []
    for item in plan.get("delete") or []:
        generation_id = str(item["generation_id"])
        if generation_id == expected:
            raise LatestConflictError("Retention plan includes the latest generation.")
        store.delete_generation(
            generation_id,
            expected_manifest_hash=item["generation_manifest_hash"],
        )
        deleted.append(generation_id)
    return {
        "status": "applied",
        "current_generation_id": expected,
        "deleted_generation_ids": deleted,
        "kept_generation_ids": list(plan.get("keep_generation_ids") or []),
    }


def rollback_latest(store, target_generation_id, *, expected_current_generation_id):
    """Atomically point latest at a verified existing generation."""

    target = str(target_generation_id)
    expected = str(expected_current_generation_id)
    current = store.read_latest()
    actual = str(current.get("generation_id")) if current else None
    if actual != expected:
        raise LatestConflictError(
            f"Latest generation changed: expected={expected}, actual={actual}"
        )
    if target == expected:
        raise ValueError("Rollback target is already latest.")
    manifest = store.read_generation_manifest(target)
    rolled_back_at = _utc_now_iso()
    pointer = {
        "schema_version": 1,
        "generation_id": target,
        "generation_path": store.generation_location(target),
        "generation_manifest_hash": payload_hash(manifest),
        "published_at_utc": rolled_back_at,
        "rollback_from_generation_id": expected,
        "rollback_at_utc": rolled_back_at,
    }
    store.publish_latest(pointer, expected_generation_id=expected)
    return {
        "status": "rolled_back",
        "previous_generation_id": expected,
        "generation_id": target,
        "latest_path": store.latest_location,
    }


def _safe_public_artifact(relative, prefixes):
    normalized = str(relative).replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        return None
    if not any(normalized.startswith(prefix) for prefix in prefixes):
        return None
    return normalized


def _media_type(path):
    suffix = PurePosixPath(path).suffix.lower()
    return {
        ".csv": "text/csv",
        ".json": "application/json",
        ".parquet": "application/vnd.apache.parquet",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(suffix, "application/octet-stream")


def build_public_artifact_catalog(
    store,
    *,
    include_prefixes=PUBLIC_ARTIFACT_PREFIXES,
):
    """Describe verified analytical artifacts without exposing RAW or local paths."""

    prefixes = tuple(
        value.replace("\\", "/").rstrip("/") + "/"
        for value in include_prefixes
    )
    if not prefixes:
        raise ValueError("At least one public artifact prefix is required.")
    if any(
        not any(prefix.startswith(allowed) for allowed in PUBLIC_ARTIFACT_PREFIXES)
        for prefix in prefixes
    ):
        raise ValueError("Public artifact prefixes must stay in processed/ or outputs/.")
    latest = store.read_latest()
    latest_id = str(latest.get("generation_id")) if latest else None
    generations = []
    for record in _generation_records(store):
        manifest = record["manifest"]
        artifacts = []
        for relative, checksum in sorted(
            (manifest.get("artifact_checksums") or {}).items()
        ):
            public_path = _safe_public_artifact(relative, prefixes)
            if public_path is None:
                continue
            artifacts.append(
                {
                    "path": public_path,
                    "sha256": str(checksum),
                    "media_type": _media_type(public_path),
                    "relative_download_path": (
                        f"generations/{record['generation_id']}/{public_path}"
                    ),
                }
            )
        generations.append(
            {
                "generation_id": record["generation_id"],
                "is_latest": record["generation_id"] == latest_id,
                "completed_at_utc": manifest.get("completed_at_utc"),
                "code_revision": manifest.get("code_revision"),
                "source_watermark": manifest.get("source_watermark"),
                "row_counts": manifest.get("row_counts") or {},
                "qa": manifest.get("qa"),
                "generation_manifest_hash": record[
                    "generation_manifest_hash"
                ],
                "artifacts": artifacts,
            }
        )
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "generated_at_utc": _utc_now_iso(),
        "latest_generation_id": latest_id,
        "public_prefixes": list(prefixes),
        "generations": generations,
    }


def write_public_artifact_catalog(store, path, *, include_prefixes=PUBLIC_ARTIFACT_PREFIXES):
    """Atomically write a public artifact catalog."""

    catalog = build_public_artifact_catalog(
        store,
        include_prefixes=include_prefixes,
    )
    atomic_json(path, catalog)
    return catalog
