from __future__ import annotations

from pathlib import Path
import json
import os
import shutil
import uuid
import hashlib

import pandas as pd
from pandas.testing import assert_frame_equal

from .normalization.payments import (
    NORMALIZATION_VERSION,
    PAYMENT_PATHS,
    FOP_LABEL_RE,
    normalize_person_name,
)


TARGETS = {
    "payer_name_normalized": "payer_type_normalized",
    "receiver_name_normalized": "receiver_type_normalized",
}


def standardize_person_names(frame: pd.DataFrame):
    """Return a copy with canonical names and explicit FOP cleanup."""

    required = set(TARGETS) | set(TARGETS.values())
    missing = required - set(frame.columns)
    if missing:
        raise KeyError(f"Payment frame missing name columns: {sorted(missing)}")

    result = frame.copy()
    stats = {}
    for name_column, type_column in TARGETS.items():
        person_mask = result[type_column].eq("Фізична особа")
        before = result[name_column].copy()
        fop_mask = (
            result[name_column]
            .fillna("")
            .astype(str)
            .map(lambda value: FOP_LABEL_RE.search(value) is not None)
        )
        eligible_mask = person_mask | fop_mask
        changed_unique_values = 0
        for counterparty_type, indexes in (
            result.loc[eligible_mask]
            .groupby(type_column, dropna=False)
            .groups.items()
        ):
            source = result.loc[indexes, name_column]
            unique_names = source.dropna().astype(str).unique()
            mapping = {
                value: normalize_person_name(value, counterparty_type)
                for value in unique_names
            }
            result.loc[indexes, name_column] = source.map(
                lambda value: mapping.get(str(value)) if pd.notna(value) else value
            )
            changed_unique_values += sum(
                new != old for old, new in mapping.items()
            )
        stats[name_column] = {
            "person_rows": int(person_mask.sum()),
            "explicit_fop_rows": int((fop_mask & ~person_mask).sum()),
            "changed_rows": int(
                (~before.fillna("<NULL>").eq(
                    result[name_column].fillna("<NULL>")
                )).sum()
            ),
            "changed_unique_values": int(changed_unique_values),
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
        expected = before[name_column].copy()
        person_mask = before[type_column].eq("Фізична особа")
        fop_mask = (
            before[name_column]
            .fillna("")
            .astype(str)
            .map(lambda value: FOP_LABEL_RE.search(value) is not None)
        )
        eligible_mask = person_mask | fop_mask
        for counterparty_type, indexes in (
            before.loc[eligible_mask]
            .groupby(type_column, dropna=False)
            .groups.items()
        ):
            source = before.loc[indexes, name_column]
            mapping = {
                value: normalize_person_name(value, counterparty_type)
                for value in source.dropna().astype(str).unique()
            }
            expected.loc[indexes] = source.map(
                lambda value: mapping.get(str(value)) if pd.notna(value) else value
            )
        if not expected.equals(after[name_column]):
            raise ValueError(
                f"Backfill left non-canonical values in {name_column}."
            )
    return after


def build_name_backfill_validation(
    normalized_root,
    enriched_root,
    output_root,
    *,
    sections=None,
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
        sections = tuple(sections or PAYMENT_PATHS)
        for layer, root in (
            ("normalized", normalized_root),
            ("enriched", enriched_root),
        ):
            summaries[layer] = {}
            for section in sections:
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


def _file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def promote_name_backfill_validation(
    validation_root,
    normalized_root,
    enriched_root,
    backup_root,
    *,
    sections=None,
):
    """Promote a proven validation copy with backup and automatic rollback."""

    validation_root = Path(validation_root)
    normalized_root = Path(normalized_root)
    enriched_root = Path(enriched_root)
    backup_root = Path(backup_root)
    if backup_root.exists():
        raise FileExistsError(backup_root)
    with (validation_root / "manifest.json").open(
        "r", encoding="utf-8"
    ) as file:
        validation_manifest = json.load(file)
    if validation_manifest.get("normalization_version") != NORMALIZATION_VERSION:
        raise ValueError("Validation copy uses another normalization version.")
    if validation_manifest.get("production_modified") is not False:
        raise ValueError("Validation manifest does not prove isolation.")

    sections = tuple(sections or PAYMENT_PATHS)
    roots = {
        "normalized": normalized_root,
        "enriched": enriched_root,
    }
    files = []
    for layer, root in roots.items():
        for section in sections:
            current = root / "payments" / f"{section}.parquet"
            candidate = (
                validation_root / layer / "payments" / f"{section}.parquet"
            )
            if not current.exists() or not candidate.exists():
                raise FileNotFoundError(
                    current if not current.exists() else candidate
                )
            before = pd.read_parquet(current)
            after = pd.read_parquet(candidate)
            validate_name_backfill(before, after)
            files.append((layer, section, current, candidate))

    backup_root.mkdir(parents=True)
    records = []
    for layer, section, current, candidate in files:
        backup = backup_root / layer / "payments" / current.name
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(current, backup)
        records.append({
            "layer": layer,
            "section": section,
            "before_sha256": _file_hash(backup),
            "after_sha256": _file_hash(candidate),
            "backup": str(backup),
        })

    replaced = []
    try:
        for layer, section, current, candidate in files:
            temp = current.with_name(
                current.name + ".tmp." + uuid.uuid4().hex
            )
            shutil.copy2(candidate, temp)
            os.replace(temp, current)
            replaced.append((layer, section, current))

        for layer, section, current, _ in files:
            backup = backup_root / layer / "payments" / current.name
            validate_name_backfill(
                pd.read_parquet(backup),
                pd.read_parquet(current),
            )
    except Exception:
        for layer, section, current in reversed(replaced):
            backup = backup_root / layer / "payments" / current.name
            temp = current.with_name(
                current.name + ".rollback." + uuid.uuid4().hex
            )
            shutil.copy2(backup, temp)
            os.replace(temp, current)
        raise

    promotion_manifest = {
        "schema_version": 1,
        "normalization_version": NORMALIZATION_VERSION,
        "validation_root": str(validation_root),
        "backup_root": str(backup_root),
        "files": records,
    }
    with (backup_root / "promotion_manifest.json").open(
        "w", encoding="utf-8"
    ) as file:
        json.dump(promotion_manifest, file, ensure_ascii=False, indent=2)
    return promotion_manifest
