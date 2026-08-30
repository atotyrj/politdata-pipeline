from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import uuid

import pyarrow.parquet as pq

from .change_set import (
    DEFAULT_CURRENT_CHANGE_SET_PATH,
    load_change_set,
    save_change_set,
    set_change_set_stage_status,
)
from .enrichment_runner import DEFAULT_ENRICHMENT_DELTA_ROOT


STATE_FILENAME = "state.json"


def _read_json(path):
    with Path(path).open("r", encoding="utf-8") as file:
        return json.load(file)


def _atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp." + uuid.uuid4().hex)
    try:
        with temp.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _manifest_hash(manifest):
    value = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def load_enrichment_state(delta_root=DEFAULT_ENRICHMENT_DELTA_ROOT):
    path = Path(delta_root).parent / STATE_FILENAME
    if not path.exists():
        return {
            "schema_version": 1,
            "latest_run_id": None,
            "runs": [],
            "reports": {},
        }
    state = _read_json(path)
    if state.get("schema_version") != 1:
        raise ValueError("Unsupported enrichment-state schema.")
    return state


def validate_enrichment_delta(run_dir, manifest):
    """Validate file identity and row-count invariants without base rebuilds."""

    run_dir = Path(run_dir)
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported enrichment manifest schema.")
    if manifest.get("run_id") != run_dir.name:
        raise ValueError("Enrichment run directory and manifest differ.")

    allowed_reports = {str(value) for value in manifest["affected_report_ids"]}
    observed = {"payments": {}, "report_sections": {}}
    for layer in observed:
        layer_root = run_dir / layer
        if not layer_root.exists():
            continue
        for section_dir in layer_root.iterdir():
            if not section_dir.is_dir():
                continue
            count = 0
            for path in section_dir.glob("*.parquet"):
                report_id = path.stem
                if report_id not in allowed_reports:
                    raise ValueError(
                        f"Unexpected report fragment in enrichment delta: {report_id}"
                    )
                table = pq.read_table(path, columns=["source_report_id"])
                values = {str(value) for value in table.column(0).to_pylist()}
                if values - {report_id}:
                    raise ValueError(
                        f"Fragment contains another report ID: {path}"
                    )
                count += table.num_rows
            observed[layer][section_dir.name] = count

    expected = {
        layer: {
            section: int(rows)
            for section, rows in manifest.get("rows", {}).get(layer, {}).items()
            if int(rows) != 0
        }
        for layer in observed
    }
    observed = {
        layer: {
            section: rows
            for section, rows in sections.items()
            if rows != 0
        }
        for layer, sections in observed.items()
    }
    if observed != expected:
        raise ValueError(
            f"Enrichment row counts differ: expected={expected}, observed={observed}"
        )
    return {
        "report_count": len(allowed_reports),
        "fragment_rows": sum(
            sum(sections.values()) for sections in observed.values()
        ),
    }


def promote_enrichment_delta(
    change_set_path=DEFAULT_CURRENT_CHANGE_SET_PATH,
    delta_root=DEFAULT_ENRICHMENT_DELTA_ROOT,
):
    """QA one immutable enrichment delta and atomically advance current state."""

    change_set = load_change_set(change_set_path)
    if change_set["stages"]["enrichment"]["status"] != "completed":
        raise ValueError("Enrichment stage must be completed before QA/promotion.")
    run_id = change_set["run_id"]
    run_dir = Path(delta_root) / run_id
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(manifest_path)
    manifest = _read_json(manifest_path)

    change_set = set_change_set_stage_status(change_set, "qa", "running")
    save_change_set(change_set, change_set_path)
    try:
        qa = validate_enrichment_delta(run_dir, manifest)
        state = load_enrichment_state(delta_root)
        digest = _manifest_hash(manifest)
        existing = next(
            (item for item in state["runs"] if item["run_id"] == run_id), None
        )
        if existing is not None:
            if existing["manifest_hash"] != digest:
                raise ValueError("Promoted run has a different manifest hash.")
            entry = existing
        else:
            entry = {
                "run_id": run_id,
                "promoted_at_utc": datetime.now(timezone.utc).isoformat(),
                "manifest_hash": digest,
                **qa,
            }
            state["runs"].append(entry)
            state["latest_run_id"] = run_id
            for report_id in manifest["affected_report_ids"]:
                state["reports"][str(report_id)] = {"run_id": run_id}
            _atomic_json(Path(delta_root).parent / STATE_FILENAME, state)

        change_set = set_change_set_stage_status(change_set, "qa", "completed")
        save_change_set(change_set, change_set_path)
        return entry
    except Exception as exc:
        change_set = set_change_set_stage_status(
            change_set, "qa", "failed", error=repr(exc)
        )
        save_change_set(change_set, change_set_path)
        raise
