"""Refresh denormalized payment identity fields from the canonical reference."""

from __future__ import annotations

from pathlib import Path
import os
import shutil
import uuid

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .qa import (
    PAYMENT_EXPECTED_ROWS,
    REFERENCE_IDENTITY_COLUMNS,
    validate_payment_reference_identity,
)


def refresh_payment_identity(
    payment_root,
    organization_reference,
    output_root,
    *,
    batch_size=50_000,
):
    """Rewrite only canonical identity columns, preserving rows and schema."""

    payment_root = Path(payment_root)
    output_root = Path(output_root)
    if output_root.exists():
        raise FileExistsError(output_root)
    reference = pd.read_parquet(
        organization_reference,
        columns=["organization_id", *REFERENCE_IDENTITY_COLUMNS],
    )
    if reference["organization_id"].duplicated().any():
        raise ValueError("organization_reference organization_id must be unique.")
    reference["organization_id"] = reference["organization_id"].astype("string")

    temporary = output_root.parent / f".{output_root.name}.{uuid.uuid4().hex[:8]}.tmp"
    summaries = []
    try:
        temporary.mkdir(parents=True)
        for section in PAYMENT_EXPECTED_ROWS:
            source = payment_root / f"{section}.parquet"
            parquet = pq.ParquetFile(source)
            schema = parquet.schema_arrow
            missing = {
                "organization_id",
                *REFERENCE_IDENTITY_COLUMNS,
            } - set(schema.names)
            if missing:
                raise KeyError(f"{section} missing identity columns: {sorted(missing)}")
            destination = temporary / source.name
            writer = pq.ParquetWriter(destination, schema, compression="snappy")
            rows = 0
            changed = {column: 0 for column in REFERENCE_IDENTITY_COLUMNS}
            try:
                for batch in parquet.iter_batches(batch_size=int(batch_size)):
                    frame = batch.to_pandas()
                    frame["organization_id"] = frame["organization_id"].astype("string")
                    merged = frame.merge(
                        reference,
                        on="organization_id",
                        how="left",
                        validate="many_to_one",
                        sort=False,
                        suffixes=("", "_reference"),
                    )
                    unresolved = merged["organization_level_reference"].isna()
                    if unresolved.any():
                        raise RuntimeError(
                            f"{section}: {int(unresolved.sum()):,} rows did not resolve reference."
                        )
                    for column in REFERENCE_IDENTITY_COLUMNS:
                        replacement = merged[f"{column}_reference"]
                        left = merged[column].astype("string").fillna("<NULL>")
                        right = replacement.astype("string").fillna("<NULL>")
                        changed[column] += int((left != right).sum())
                        merged[column] = replacement
                    table = pa.Table.from_pandas(
                        merged.loc[:, schema.names],
                        schema=schema,
                        preserve_index=False,
                        safe=False,
                    )
                    writer.write_table(table)
                    rows += table.num_rows
            finally:
                writer.close()
            if rows != parquet.metadata.num_rows:
                raise RuntimeError(f"{section}: row count changed during identity refresh.")
            summaries.append(
                {
                    "section": section,
                    "rows": rows,
                    "changed_fields": changed,
                }
            )

        validate_payment_reference_identity(
            temporary,
            organization_reference=organization_reference,
        )
        os.replace(temporary, output_root)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return summaries


def promote_refreshed_payments(
    refreshed_root,
    payment_root,
    backup_root,
):
    """Atomically replace validated payment files while retaining a backup."""

    refreshed_root = Path(refreshed_root)
    payment_root = Path(payment_root)
    backup_root = Path(backup_root)
    if backup_root.exists():
        raise FileExistsError(backup_root)
    backup_root.mkdir(parents=True)
    promoted = []
    try:
        for section in PAYMENT_EXPECTED_ROWS:
            source = refreshed_root / f"{section}.parquet"
            current = payment_root / source.name
            backup = backup_root / source.name
            shutil.copy2(current, backup)
            replacement = current.with_name(f".{current.name}.{uuid.uuid4().hex[:8]}.tmp")
            try:
                shutil.copy2(source, replacement)
                os.replace(replacement, current)
            finally:
                replacement.unlink(missing_ok=True)
            promoted.append(str(current))
    except Exception:
        for backup in backup_root.glob("*.parquet"):
            shutil.copy2(backup, payment_root / backup.name)
        raise
    return promoted
