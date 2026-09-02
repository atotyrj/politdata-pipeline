"""Export validated normalized Parquet schemas as a versioned contract."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

import pyarrow.parquet as pq
import pyarrow as pa

from politdata.normalization.organizations import (
    ORGANIZATION_ADDRESS_SCHEMA,
    ORGANIZATION_HEAD_SCHEMA,
    ORGANIZATION_SCHEMA,
)
from politdata.normalization.payments import NORMALIZED_PAYMENT_SCHEMA


CONTEXT_TYPES = {
    "source_report_id": pa.string(),
    "source_section": pa.string(),
    "source_row_index": pa.int64(),
    "organization_id": pa.string(),
    "root_party_id": pa.string(),
    "report_year": pa.int64(),
    "report_quarter": pa.int64(),
    "source_is_signed": pa.bool_(),
    "source_signed_date": pa.string(),
    "report_schema_version_source": pa.int64(),
    "report_type_source": pa.string(),
    "is_party_office_source": pa.bool_(),
    "source_row_json": pa.string(),
}


def _canonical_schema(name, observed):
    if name == "organizations":
        return ORGANIZATION_SCHEMA
    if name == "organization_heads":
        return ORGANIZATION_HEAD_SCHEMA
    if name == "organization_addresses":
        return ORGANIZATION_ADDRESS_SCHEMA
    if name.startswith("payments/"):
        return NORMALIZED_PAYMENT_SCHEMA
    fields = []
    for field in observed:
        field_type = CONTEXT_TYPES.get(field.name, field.type)
        if pa.types.is_null(field_type):
            field_type = pa.string()
        fields.append(pa.field(field.name, field_type, nullable=True))
    return pa.schema(fields)


def export_contract(source_root, output_path):
    source_root = Path(source_root)
    output_path = Path(output_path)
    datasets = {}
    for path in sorted(source_root.rglob("*.parquet")):
        parquet = pq.ParquetFile(path)
        name = str(path.relative_to(source_root).with_suffix("")).replace("\\", "/")
        schema = _canonical_schema(
            name, parquet.schema_arrow.remove_metadata()
        ).remove_metadata()
        datasets[name] = {
            "arrow_schema_base64": base64.b64encode(schema.serialize()).decode("ascii"),
            "validated_rows": parquet.metadata.num_rows,
        }
    if not datasets:
        raise RuntimeError(f"No normalized Parquet datasets found below {source_root}")
    payload = {
        "schema_version": 1,
        "source": "validated normalized_v0_1 baseline",
        "datasets": datasets,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return len(datasets)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=Path)
    parser.add_argument("output_path", type=Path)
    args = parser.parse_args(argv)
    count = export_contract(args.source_root, args.output_path)
    print(f"Exported {count} normalized schemas to {args.output_path}")


if __name__ == "__main__":
    main()
