from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import shutil
import uuid

from .change_set import (
    DEFAULT_CURRENT_CHANGE_SET_PATH,
    load_change_set,
)


DEFAULT_FRAGMENT_ROOT = Path(
    "data/interim/normalized_changes"
)
DEFAULT_DELTA_ROOT = Path(
    "data/processed/normalized_deltas_v0_1"
)
PROMOTION_STATE_FILENAME = "state.json"


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _read_json(path):
    with Path(path).open(
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def _atomic_write_json(path, payload):
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
                payload,
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


def _manifest_hash(manifest):
    canonical = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _empty_state():
    return {
        "schema_version": 1,
        "latest_run_id": None,
        "runs": [],
        "organizations": {},
        "reports": {},
    }


def load_promotion_state(delta_root=DEFAULT_DELTA_ROOT):
    path = Path(delta_root) / PROMOTION_STATE_FILENAME
    if not path.exists():
        return _empty_state()

    state = _read_json(path)
    if state.get("schema_version") != 1:
        raise ValueError("Unsupported promotion-state schema.")
    return state


def _require_fragment(path):
    if not path.is_file():
        raise FileNotFoundError(path)


def validate_normalized_fragment_run(fragment_dir, manifest):
    """Validate the immutable fragment set before publication."""

    fragment_dir = Path(fragment_dir)
    run_id = manifest.get("run_id")
    if not run_id or fragment_dir.name != run_id:
        raise ValueError(
            "Fragment directory does not match manifest run_id."
        )

    if manifest.get("organization_count") != len(
        manifest.get("organizations", [])
    ):
        raise ValueError("Organization fragment count mismatch.")

    if manifest.get("report_count") != len(
        manifest.get("reports", [])
    ):
        raise ValueError("Report fragment count mismatch.")

    for item in manifest.get("organizations", []):
        if item.get("deleted"):
            continue
        organization_id = str(item["organization_id"])
        for artifact in (
            "organizations",
            "organization_heads",
            "organization_addresses",
        ):
            _require_fragment(
                fragment_dir
                / artifact
                / f"{organization_id}.parquet"
            )

    for item in manifest.get("reports", []):
        report_id = str(item["report_id"])

        for section in item.get("payments", {}):
            _require_fragment(
                fragment_dir
                / "payments"
                / section
                / f"{report_id}.parquet"
            )

        if item.get("property_moneys", 0) > 0:
            _require_fragment(
                fragment_dir
                / "properties"
                / "property_moneys"
                / f"{report_id}.parquet"
            )

        for section, rows in item.get(
            "report_sections",
            {},
        ).items():
            if rows > 0:
                _require_fragment(
                    fragment_dir
                    / "report_sections"
                    / section
                    / f"{report_id}.parquet"
                )

    return manifest


def _apply_entity_index(state, change_set, run_id):
    for change in change_set["organization_changes"]:
        organization_id = str(change["organization_id"])
        state["organizations"][organization_id] = {
            "run_id": run_id,
            "change_type": change["change_type"],
            "deleted": change["change_type"] == "disappeared",
            "content_hash": change.get("new_content_hash"),
        }

    for change in change_set["report_changes"]:
        report_id = str(change["report_id"])
        state["reports"][report_id] = {
            "run_id": run_id,
            "organization_id": str(
                change["organization_id"]
            ),
            "change_type": change["change_type"],
            "deleted": False,
            "content_hash": change.get("new_content_hash"),
        }


def promote_normalized_fragments(
    change_set_path=DEFAULT_CURRENT_CHANGE_SET_PATH,
    fragment_root=DEFAULT_FRAGMENT_ROOT,
    delta_root=DEFAULT_DELTA_ROOT,
):
    """
    Atomically publish one normalized change run as an immutable delta.

    Existing validated Parquet files and earlier delta runs are never
    rewritten. A small atomic entity index provides insert/update/delete
    semantics and points each logical entity at its latest run.
    """

    change_set = load_change_set(change_set_path)
    normalization = change_set["stages"]["normalization"]
    if normalization["status"] != "completed":
        raise ValueError(
            "Normalization stage must be completed before promotion."
        )

    run_id = change_set["run_id"]
    fragment_dir = Path(fragment_root) / run_id
    manifest_path = fragment_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(manifest_path)

    manifest = _read_json(manifest_path)
    if manifest.get("run_id") != run_id:
        raise ValueError(
            "Change set and fragment manifest run_id differ."
        )

    validate_normalized_fragment_run(
        fragment_dir,
        manifest,
    )
    manifest_hash = _manifest_hash(manifest)
    delta_root = Path(delta_root)
    runs_root = delta_root / "runs"
    destination = runs_root / run_id
    state = load_promotion_state(delta_root)
    existing_run = next(
        (
            item
            for item in state["runs"]
            if item["run_id"] == run_id
        ),
        None,
    )

    if existing_run is not None:
        if existing_run["manifest_hash"] != manifest_hash:
            raise ValueError(
                "Promoted run_id has a different manifest hash."
            )
        if not destination.is_dir():
            raise FileNotFoundError(destination)
        return existing_run

    if not destination.exists():
        runs_root.mkdir(parents=True, exist_ok=True)
        temp_destination = runs_root / (
            ".tmp." + run_id + "." + uuid.uuid4().hex
        )
        try:
            shutil.copytree(
                fragment_dir,
                temp_destination,
            )
            os.replace(temp_destination, destination)
        finally:
            if temp_destination.exists():
                shutil.rmtree(temp_destination)
    else:
        destination_manifest = _read_json(
            destination / "manifest.json"
        )
        if _manifest_hash(destination_manifest) != manifest_hash:
            raise FileExistsError(destination)

    promoted_at = _utc_now_iso()
    run_entry = {
        "run_id": run_id,
        "promoted_at_utc": promoted_at,
        "manifest_hash": manifest_hash,
        "path": str(destination),
        "organization_count": manifest["organization_count"],
        "report_count": manifest["report_count"],
    }
    state["runs"].append(run_entry)
    state["latest_run_id"] = run_id
    _apply_entity_index(state, change_set, run_id)
    _atomic_write_json(
        delta_root / PROMOTION_STATE_FILENAME,
        state,
    )
    return run_entry
