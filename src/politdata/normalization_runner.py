from __future__ import annotations

from pathlib import Path
import json
import os
import shutil
import uuid

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .change_set import (
    DEFAULT_CURRENT_CHANGE_SET_PATH,
    load_change_set,
    save_change_set,
    set_change_set_stage_status,
)
from .normalization.payments import (
    PAYMENT_PATHS,
    normalize_report_payments,
    write_fragment,
)
from .normalization.organizations import (
    ORGANIZATION_ADDRESS_SCHEMA,
    ORGANIZATION_HEAD_SCHEMA,
    ORGANIZATION_SCHEMA,
    normalize_organization_card,
)
from .normalization.property_moneys import (
    extract_property_moneys,
    normalize_property_money_row,
)
from .normalization.report_sections import (
    SECTION_PATHS,
    extract_section_rows,
    normalize_employee_counts,
    normalize_source_row,
)
from .report_details import (
    report_detail_content_hash,
    validate_report_detail_payload,
)
from .change_detection import (
    organization_content_hash,
)


DEFAULT_REPORT_STATE_PATH = Path(
    "data/interim/state/report_detail_state.parquet"
)
DEFAULT_REPORT_RAW_DIR = Path(
    "data/raw/report_details"
)
DEFAULT_ORGANIZATION_RAW_DIR = Path(
    "data/raw/party_accounts"
)
DEFAULT_FRAGMENT_ROOT = Path(
    "data/interim/normalized_changes"
)


def _load_payload(path):
    with Path(path).open(
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def _context_from_state(row, detail):
    report_id = str(row["report_id"])
    year = row.get("year", detail.get("year"))
    quarter = row.get(
        "quarter",
        detail.get("quarter"),
    )

    if pd.isna(year) or pd.isna(quarter):
        raise ValueError(
            f"Report context missing year/quarter: {report_id}"
        )

    selection_method = row.get("selection_method")
    if pd.isna(selection_method):
        selection_method = None

    return {
        "source_report_id": report_id,
        "official_selected_report_id": report_id,
        "analysis_selected_report_id": report_id,
        "organization_id": str(row["organization_id"]),
        "root_party_id": str(row["root_party_id"]),
        "year": int(year),
        "quarter": int(quarter),
        "period_label": f"{int(year)} Q{int(quarter)}",
        "report_type": detail.get("report_type"),
        "analysis_override": False,
        "analysis_selection_method": selection_method,
    }


def _write_dynamic_fragment(path, rows):
    if not rows:
        return None

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(
        path.stem + ".tmp." + uuid.uuid4().hex + path.suffix
    )

    try:
        pd.DataFrame(rows).to_parquet(
            temp_path,
            index=False,
        )
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return path


def _write_schema_fragment(path, rows, schema):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(
        path.stem + ".tmp." + uuid.uuid4().hex + path.suffix
    )

    try:
        table = pa.Table.from_pylist(rows, schema=schema)
        pq.write_table(
            table,
            temp_path,
            compression="zstd",
        )
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return path


def _normalize_one_organization(
    change,
    raw_dir,
    output_root,
):
    organization_id = str(change["organization_id"])

    if change["change_type"] == "disappeared":
        return {
            "organization_id": organization_id,
            "change_type": "disappeared",
            "deleted": True,
        }

    raw_path = raw_dir / f"{organization_id}.json"
    if not raw_path.exists():
        raise FileNotFoundError(raw_path)

    payload = _load_payload(raw_path)
    record = payload.get("results")
    if not isinstance(record, dict):
        raise ValueError(
            f"Invalid organization-card payload: {organization_id}"
        )

    actual_id = str(record.get("id"))
    if actual_id != organization_id:
        raise ValueError(
            "Organization ID mismatch: "
            f"expected={organization_id}, actual={actual_id}"
        )

    actual_hash = organization_content_hash(record)
    expected_hash = change.get("new_content_hash")
    if expected_hash and actual_hash != expected_hash:
        raise ValueError(
            "RAW semantic hash does not match change set: "
            f"organization_id={organization_id}"
        )

    normalized = normalize_organization_card(record)
    schemas = {
        "organizations": ORGANIZATION_SCHEMA,
        "organization_heads": ORGANIZATION_HEAD_SCHEMA,
        "organization_addresses": ORGANIZATION_ADDRESS_SCHEMA,
    }

    for artifact, schema in schemas.items():
        _write_schema_fragment(
            output_root
            / artifact
            / f"{organization_id}.parquet",
            normalized[artifact],
            schema,
        )

    return {
        "organization_id": organization_id,
        "change_type": change["change_type"],
        "deleted": False,
        "content_hash": actual_hash,
        "organization_rows": 1,
        "head_rows": len(normalized["organization_heads"]),
        "address_rows": len(
            normalized["organization_addresses"]
        ),
    }


def _normalize_one_report(
    change,
    state_row,
    raw_dir,
    output_root,
):
    report_id = str(change["report_id"])
    raw_path_value = state_row.get("raw_path")

    if raw_path_value is None or pd.isna(raw_path_value):
        raw_path = raw_dir / f"{report_id}.json"
    else:
        raw_path = Path(raw_path_value)

    if not raw_path.exists():
        raise FileNotFoundError(raw_path)

    payload = _load_payload(raw_path)
    detail = validate_report_detail_payload(
        payload,
        report_id,
    )
    actual_hash = report_detail_content_hash(payload)
    expected_hash = change.get("new_content_hash")

    if expected_hash and actual_hash != expected_hash:
        raise ValueError(
            "RAW semantic hash does not match change set: "
            f"report_id={report_id}"
        )

    context = _context_from_state(
        state_row,
        detail,
    )
    payment_rows = normalize_report_payments(
        detail,
        context,
    )
    summary = {
        "report_id": report_id,
        "organization_id": context["organization_id"],
        "content_hash": actual_hash,
        "payments": {},
        "property_moneys": 0,
        "report_sections": {},
    }

    for section in PAYMENT_PATHS:
        rows = payment_rows[section]
        path = (
            output_root
            / "payments"
            / section
            / f"{report_id}.parquet"
        )
        write_fragment(path, rows)
        summary["payments"][section] = len(rows)

    money_rows = [
        normalize_property_money_row(
            row,
            source_report_id=report_id,
            organization_id=context["organization_id"],
            root_party_id=context["root_party_id"],
            report_year=context["year"],
            report_quarter=context["quarter"],
        )
        for row in extract_property_moneys(detail)
        if isinstance(row, dict)
    ]
    _write_dynamic_fragment(
        output_root
        / "properties"
        / "property_moneys"
        / f"{report_id}.parquet",
        money_rows,
    )
    summary["property_moneys"] = len(money_rows)

    signed_date = detail.get("signed_date")
    source_is_signed = signed_date not in {
        None,
        "",
    }

    for section in SECTION_PATHS:
        rows = [
            normalize_source_row(
                source_row,
                source_report_id=report_id,
                source_section=section,
                source_row_index=index,
                organization_id=context["organization_id"],
                root_party_id=context["root_party_id"],
                report_year=context["year"],
                report_quarter=context["quarter"],
                source_is_signed=source_is_signed,
                source_signed_date=signed_date,
                report_schema_version_source=detail.get(
                    "schema_version"
                ),
                report_type_source=detail.get("report_type"),
                is_party_office_source=(
                    state_row.get("entity_type") == "office"
                ),
            )
            for index, source_row in enumerate(
                extract_section_rows(detail, section)
            )
        ]
        _write_dynamic_fragment(
            output_root
            / "report_sections"
            / section
            / f"{report_id}.parquet",
            rows,
        )
        summary["report_sections"][section] = len(rows)

    employee_rows = normalize_employee_counts(
        detail,
        source_report_id=report_id,
        organization_id=context["organization_id"],
        root_party_id=context["root_party_id"],
        report_year=context["year"],
        report_quarter=context["quarter"],
        source_is_signed=source_is_signed,
        source_signed_date=signed_date,
        report_schema_version_source=detail.get("schema_version"),
        report_type_source=detail.get("report_type"),
        is_party_office_source=(
            state_row.get("entity_type") == "office"
        ),
    )
    _write_dynamic_fragment(
        output_root
        / "report_sections"
        / "employee_counts"
        / f"{report_id}.parquet",
        employee_rows,
    )
    summary["report_sections"]["employee_counts"] = 1

    return summary


def normalize_changed_fragments(
    change_set_path=DEFAULT_CURRENT_CHANGE_SET_PATH,
    report_state_path=DEFAULT_REPORT_STATE_PATH,
    raw_dir=DEFAULT_REPORT_RAW_DIR,
    organization_raw_dir=DEFAULT_ORGANIZATION_RAW_DIR,
    fragment_root=DEFAULT_FRAGMENT_ROOT,
):
    """
    Normalize only reports named by the current change set.

    Output is committed atomically to a run-specific interim directory.
    Existing validated normalized/enriched datasets are never modified.
    """

    change_set_path = Path(change_set_path)
    report_state_path = Path(report_state_path)
    raw_dir = Path(raw_dir)
    organization_raw_dir = Path(organization_raw_dir)
    fragment_root = Path(fragment_root)
    change_set = load_change_set(change_set_path)
    run_id = change_set["run_id"]
    output_root = fragment_root / run_id

    if output_root.exists():
        raise FileExistsError(output_root)

    state_by_id = None
    if change_set["report_changes"]:
        state = pd.read_parquet(report_state_path)
        state["report_id"] = state["report_id"].astype(str)

        if state["report_id"].duplicated().any():
            raise ValueError(
                "Report detail state contains duplicate report IDs."
            )

        state_by_id = state.set_index(
            "report_id",
            drop=False,
        )
    change_set = set_change_set_stage_status(
        change_set,
        "normalization",
        "running",
    )
    save_change_set(change_set, change_set_path)
    temp_root = fragment_root / (
        ".tmp." + run_id + "." + uuid.uuid4().hex
    )

    try:
        organization_summaries = [
            _normalize_one_organization(
                change,
                organization_raw_dir,
                temp_root,
            )
            for change in change_set[
                "organization_changes"
            ]
        ]
        summaries = []
        for change in change_set["report_changes"]:
            report_id = str(change["report_id"])

            if report_id not in state_by_id.index:
                raise KeyError(
                    f"Changed report missing from state: {report_id}"
                )

            row = state_by_id.loc[report_id].to_dict()
            if row.get("status") != "success":
                raise ValueError(
                    f"Changed report is not successful: {report_id}"
                )

            summaries.append(
                _normalize_one_report(
                    change,
                    row,
                    raw_dir,
                    temp_root,
                )
            )

        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "change_set_path": str(change_set_path),
            "report_count": len(summaries),
            "reports": summaries,
            "organization_count": len(
                organization_summaries
            ),
            "organizations": organization_summaries,
            "deleted_organization_ids": [
                item["organization_id"]
                for item in organization_summaries
                if item["deleted"]
            ],
            "organization_normalization_pending": False,
        }
        temp_root.mkdir(parents=True, exist_ok=True)
        with (temp_root / "manifest.json").open(
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                manifest,
                file,
                ensure_ascii=False,
                indent=2,
            )

        fragment_root.mkdir(parents=True, exist_ok=True)
        os.replace(temp_root, output_root)

        change_set = set_change_set_stage_status(
            change_set,
            "normalization",
            "completed",
        )

        save_change_set(change_set, change_set_path)
        return manifest

    except Exception as exc:
        if temp_root.exists():
            shutil.rmtree(temp_root)

        change_set = set_change_set_stage_status(
            change_set,
            "normalization",
            "failed",
            error=repr(exc),
        )
        save_change_set(change_set, change_set_path)
        raise


def normalize_changed_report_fragments(*args, **kwargs):
    """Backward-compatible name for the combined normalization runner."""

    return normalize_changed_fragments(*args, **kwargs)
