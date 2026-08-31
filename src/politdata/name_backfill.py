from __future__ import annotations

from pathlib import Path
import json
import os
import shutil
import uuid

import pandas as pd
from pandas.testing import assert_frame_equal

from .normalization.payments import (
    NORMALIZATION_VERSION,
    PAYMENT_PATHS,
    normalize_person_name,
)


TARGETS = {
    "payer_name_normalized": "payer_type_normalized",
    "receiver_name_normalized": "receiver_type_normalized",
}


def standardize_person_names(frame: pd.DataFrame):
    """Return a copy with canonical casing only for natural persons."""

    required = set(TARGETS) | set(TARGETS.values())
    missing = required - set(frame.columns)
    if missing:
        raise KeyError(f"Payment frame missing name columns: {sorted(missing)}")

    result = frame.copy()
    stats = {}
    for name_column, type_column in TARGETS.items():
        person_mask = result[type_column].eq("Фізична особа")
        source = result.loc[person_mask, name_column]
        unique_names = source.dropna().astype(str).unique()
        mapping = {
            value: normalize_person_name(value, "Фізична особа")
            for value in unique_names
        }
        before = result[name_column].copy()
        result.loc[person_mask, name_column] = source.map(
            lambda value: mapping.get(str(value)) if pd.notna(value) else value
        )
        stats[name_column] = {
            "person_rows": int(person_mask.sum()),
            "changed_rows": int(
                (~before.fillna("<NULL>").eq(
                    result[name_column].fillna("<NULL>")
                )).sum()
            ),
            "changed_unique_values": int(
                sum(new != old for old, new in mapping.items())
            ),
        }

    validate_name_backfill(frame, result)
    return result, stats


def validate_name_backfill(before: pd.DataFrame, after: pd.DataFrame):
    """Prove that a backfill changes only canonical person-name fields."""

    if list(before.columns) != list(after.columns):
        raise ValueError("Backfill changed payment schema or column order.")
    if len(before) != len(after):
        raise ValueError("Backfill changed payment row count.")

    stable_columns = [
        column for column in before.columns if column not in TARGETS
    ]
    assert_frame_equal(
        before[stable_columns].reset_index(drop=True),
        after[stable_columns].reset_index(drop=True),
        check_dtype=True,
        check_exact=True,
    )

    for name_column, type_column in TARGETS.items():
        non_person = ~before[type_column].eq("Фізична особа")
        if not before.loc[non_person, name_column].equals(
            after.loc[non_person, name_column]
        ):
            raise ValueError(
                f"Backfill changed non-person values in {name_column}."
            )
        person = before[type_column].eq("Фізична особа")
        expected = after.loc[person, name_column].map(
            lambda value: normalize_person_name(value, "Фізична особа")
        )
        if not expected.equals(after.loc[person, name_column]):
            raise ValueError(
                f"Backfill left non-canonical values in {name_column}."
            )
    return after


def build_name_backfill_validation(
    normalized_root,
    enriched_root,
    output_root,
):
    """Build an atomic validation copy; production datasets are untouched."""

    normalized_root = Path(normalized_root)
    enriched_root = Path(enriched_root)
    output_root = Path(output_root)
    if output_root.exists():
        raise FileExistsError(output_root)
    temp = output_root.with_name(
        ".tmp." + output_root.name + "." + uuid.uuid4().hex
    )

    try:
        summaries = {}
        for layer, root in (
            ("normalized", normalized_root),
            ("enriched", enriched_root),
        ):
            summaries[layer] = {}
            for section in PAYMENT_PATHS:
                source = root / "payments" / f"{section}.parquet"
                if not source.exists():
                    raise FileNotFoundError(source)
                before = pd.read_parquet(source)
                after, stats = standardize_person_names(before)
                target = temp / layer / "payments" / f"{section}.parquet"
                target.parent.mkdir(parents=True, exist_ok=True)
                after.to_parquet(target, index=False)
                roundtrip = pd.read_parquet(target)
                validate_name_backfill(before, roundtrip)
                summaries[layer][section] = {
                    "rows": len(after),
                    "payment_amount_sum": str(
                        after["payment_amount"].sum(skipna=True)
                    ),
                    **stats,
                }

        manifest = {
            "schema_version": 1,
            "normalization_version": NORMALIZATION_VERSION,
            "production_modified": False,
            "layers": summaries,
        }
        with (temp / "manifest.json").open("w", encoding="utf-8") as file:
            json.dump(manifest, file, ensure_ascii=False, indent=2)
        output_root.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp, output_root)
        return manifest
    except Exception:
        if temp.exists():
            shutil.rmtree(temp)
        raise
