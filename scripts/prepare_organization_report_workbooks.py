"""Prepare compact JSON payloads for the two organization-report workbooks.

This is presentation-only. It reads the validated processed layers and never
scans RAW files or rebuilds normalization/enrichment.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from politdata.analytical_excel import (
    build_organization_report_name_history,
    build_reporting_organization_matrix,
)


def _write_payload(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        frame.to_json(orient="split", index=False, force_ascii=False),
        encoding="utf-8",
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--enriched-root",
        type=Path,
        default=Path("data/processed/enriched_v0_1"),
    )
    parser.add_argument("--matrix-json", type=Path, required=True)
    parser.add_argument("--history-json", type=Path, required=True)
    args = parser.parse_args(argv)

    report_context = pd.read_parquet(
        args.enriched_root / "reference" / "report_context.parquet"
    )
    organization_reference = pd.read_parquet(
        args.enriched_root / "reference" / "organization_reference.parquet"
    )
    report_organizations = pd.read_parquet(
        args.enriched_root / "report_state" / "organizations.parquet"
    )
    report_regional_offices = pd.read_parquet(
        args.enriched_root / "report_state" / "regional_offices.parquet"
    )

    matrix = build_reporting_organization_matrix(
        report_context,
        organization_reference,
    )
    history = build_organization_report_name_history(
        report_context,
        report_organizations,
        report_regional_offices,
    )
    _write_payload(matrix, args.matrix_json)
    _write_payload(history, args.history_json)

    metrics = {
        "selected_reports": int(len(report_context)),
        "matrix_rows": int(len(matrix)),
        "matrix_columns": int(len(matrix.columns)),
        "history_rows": int(len(history)),
        "history_columns": int(len(history.columns)),
        "reported_names": int(history["organization_name_as_reported"].notna().sum()),
        "comparable_adjacent_reports": int(
            history["organization_name_changed_since_previous_report"].notna().sum()
        ),
        "name_changes": int(
            history["organization_name_changed_since_previous_report"].eq(1).sum()
        ),
    }
    print(json.dumps(metrics, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
