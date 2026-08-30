from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os
import uuid

import pandas as pd


CHANGE_SET_SCHEMA_VERSION = 1

CHANGE_TYPES = {
    "new",
    "meaningful_change",
    "disappeared",
}

STAGE_NAMES = (
    "normalization",
    "references",
    "enrichment",
    "qa",
)

STAGE_STATUSES = {
    "pending",
    "running",
    "completed",
    "failed",
    "skipped",
}


def utc_now_iso():
    return datetime.now(
        timezone.utc
    ).isoformat()


def _clean_id(value):
    if value is None or pd.isna(value):
        return None

    text = str(value).strip()
    return text or None


def _sorted_unique(values):
    return sorted({
        clean
        for value in values
        if (clean := _clean_id(value))
    })


def organization_changes_from_sync_log(
    sync_log,
):
    """
    Extract downstream-relevant organization changes from one
    ``run_organization_sync`` result.

    Technical refreshes and unchanged fetches are deliberately excluded.
    A failed discovery commit is rejected because downstream stages must
    never consume an uncommitted discovery view.
    """

    if not sync_log.get("committed", False):
        raise ValueError(
            "Organization sync was not committed."
        )

    changes = []

    for result in sync_log.get("results", []):
        change_type = result.get("status")

        if change_type not in {
            "new",
            "meaningful_change",
        }:
            continue

        organization_id = _clean_id(
            result.get("organization_id")
        )

        if organization_id is None:
            raise ValueError(
                "Organization change missing organization_id."
            )

        changes.append({
            "organization_id": organization_id,
            "change_type": change_type,
            "candidate_reasons": _sorted_unique(
                result.get("candidate_reasons", [])
            ),
            "changed_fields": sorted({
                str(field)
                for field in result.get(
                    "changed_fields",
                    [],
                )
            }),
            "old_content_hash": result.get(
                "old_content_hash"
            ),
            "new_content_hash": result.get(
                "new_content_hash"
            ),
        })

    existing_ids = {
        item["organization_id"]
        for item in changes
    }

    for organization_id in _sorted_unique(
        sync_log.get("disappeared_ids", [])
    ):
        if organization_id in existing_ids:
            raise ValueError(
                "Organization cannot be both changed and disappeared: "
                f"{organization_id}"
            )

        changes.append({
            "organization_id": organization_id,
            "change_type": "disappeared",
            "candidate_reasons": [
                "missing_from_discovery"
            ],
            "changed_fields": [],
            "old_content_hash": None,
            "new_content_hash": None,
        })

    return sorted(
        changes,
        key=lambda item: item["organization_id"],
    )


def report_changes_from_states(
    previous_state,
    current_state,
):
    """
    Compare successful report-detail states by semantic ``content_hash``.

    Only new reports and reports whose semantic hash changed are emitted.
    RAW-only changes are therefore ignored by design.
    """

    required = {
        "report_id",
        "organization_id",
        "content_hash",
        "status",
    }

    for name, frame in (
        ("previous_state", previous_state),
        ("current_state", current_state),
    ):
        missing = required - set(frame.columns)

        if missing:
            raise ValueError(
                f"{name} missing columns: {sorted(missing)}"
            )

        if frame["report_id"].astype(str).duplicated().any():
            raise ValueError(
                f"{name} contains duplicate report_id values."
            )

    previous = {
        str(row["report_id"]): row
        for row in previous_state.to_dict("records")
    }

    changes = []

    current_success = current_state[
        current_state["status"] == "success"
    ]

    for row in current_success.to_dict("records"):
        report_id = _clean_id(row.get("report_id"))
        organization_id = _clean_id(
            row.get("organization_id")
        )
        new_hash = _clean_id(row.get("content_hash"))

        if report_id is None or organization_id is None:
            raise ValueError(
                "Successful report missing report_id or organization_id."
            )

        if new_hash is None:
            raise ValueError(
                f"Successful report missing content_hash: {report_id}"
            )

        old = previous.get(report_id)

        if old is None or old.get("status") != "success":
            change_type = "new"
            old_hash = None
        else:
            old_hash = _clean_id(old.get("content_hash"))

            if old_hash == new_hash:
                continue

            change_type = "meaningful_change"

        changes.append({
            "report_id": report_id,
            "organization_id": organization_id,
            "change_type": change_type,
            "old_content_hash": old_hash,
            "new_content_hash": new_hash,
        })

    return sorted(
        changes,
        key=lambda item: item["report_id"],
    )


def create_change_set(
    *,
    organization_changes=(),
    report_changes=(),
    run_id=None,
    created_at_utc=None,
):
    """Create and validate a versioned changed-only run contract."""

    change_set = {
        "schema_version": CHANGE_SET_SCHEMA_VERSION,
        "run_id": run_id or uuid.uuid4().hex,
        "created_at_utc": created_at_utc or utc_now_iso(),
        "status": "pending",
        "organization_changes": list(
            organization_changes
        ),
        "report_changes": list(report_changes),
        "affected_organization_ids": [],
        "affected_report_ids": [],
        "stages": {
            stage: {
                "status": "pending",
                "started_at_utc": None,
                "finished_at_utc": None,
                "error": None,
            }
            for stage in STAGE_NAMES
        },
    }

    change_set["affected_organization_ids"] = (
        _sorted_unique(
            [
                item.get("organization_id")
                for item in change_set[
                    "organization_changes"
                ]
            ]
            + [
                item.get("organization_id")
                for item in change_set[
                    "report_changes"
                ]
            ]
        )
    )

    change_set["affected_report_ids"] = (
        _sorted_unique(
            item.get("report_id")
            for item in change_set[
                "report_changes"
            ]
        )
    )

    validate_change_set(change_set)
    return change_set


def validate_change_set(change_set):
    required = {
        "schema_version",
        "run_id",
        "created_at_utc",
        "status",
        "organization_changes",
        "report_changes",
        "affected_organization_ids",
        "affected_report_ids",
        "stages",
    }

    missing = required - set(change_set)
    if missing:
        raise ValueError(
            f"Change set missing fields: {sorted(missing)}"
        )

    if change_set["schema_version"] != CHANGE_SET_SCHEMA_VERSION:
        raise ValueError(
            "Unsupported change-set schema version."
        )

    if not _clean_id(change_set["run_id"]):
        raise ValueError("Change set run_id is empty.")

    organization_ids = []
    for item in change_set["organization_changes"]:
        if item.get("change_type") not in CHANGE_TYPES:
            raise ValueError(
                "Invalid organization change_type."
            )
        organization_ids.append(
            item.get("organization_id")
        )

    report_ids = []
    report_organization_ids = []
    for item in change_set["report_changes"]:
        if item.get("change_type") not in CHANGE_TYPES - {
            "disappeared"
        }:
            raise ValueError(
                "Invalid report change_type."
            )
        report_ids.append(item.get("report_id"))
        report_organization_ids.append(
            item.get("organization_id")
        )

    if len(_sorted_unique(organization_ids)) != len(
        organization_ids
    ):
        raise ValueError(
            "Duplicate organization changes."
        )

    if len(_sorted_unique(report_ids)) != len(report_ids):
        raise ValueError("Duplicate report changes.")

    expected_organization_ids = _sorted_unique(
        organization_ids + report_organization_ids
    )
    expected_report_ids = _sorted_unique(report_ids)

    if change_set["affected_organization_ids"] != (
        expected_organization_ids
    ):
        raise ValueError(
            "affected_organization_ids do not match changes."
        )

    if change_set["affected_report_ids"] != expected_report_ids:
        raise ValueError(
            "affected_report_ids do not match changes."
        )

    if set(change_set["stages"]) != set(STAGE_NAMES):
        raise ValueError("Change set stages are incomplete.")

    for stage in change_set["stages"].values():
        if stage.get("status") not in STAGE_STATUSES:
            raise ValueError("Invalid change-set stage status.")

    return change_set


def save_change_set(change_set, path):
    """Validate and atomically persist a change set as JSON."""

    validate_change_set(change_set)
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
                change_set,
                file,
                ensure_ascii=False,
                indent=2,
            )
            file.flush()
            os.fsync(file.fileno())

        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return path


def load_change_set(path):
    with Path(path).open(
        "r",
        encoding="utf-8",
    ) as file:
        change_set = json.load(file)

    return validate_change_set(change_set)
