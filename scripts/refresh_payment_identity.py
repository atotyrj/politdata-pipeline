"""Validate and optionally promote canonical payment identity fields."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from politdata.payment_identity_refresh import (
    promote_refreshed_payments,
    refresh_payment_identity,
)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--payment-root",
        type=Path,
        default=Path("data/processed/enriched_v0_1/payments"),
    )
    parser.add_argument(
        "--organization-reference",
        type=Path,
        default=Path(
            "data/processed/enriched_v0_1/reference/organization_reference.parquet"
        ),
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--promote", action="store_true")
    parser.add_argument("--confirm-promote", action="store_true")
    parser.add_argument("--backup-root", type=Path)
    args = parser.parse_args(argv)

    summary = refresh_payment_identity(
        args.payment_root,
        args.organization_reference,
        args.output_root,
    )
    result = {"status": "validated", "sections": summary}
    if args.promote:
        if not args.confirm_promote:
            raise SystemExit("--promote requires --confirm-promote")
        if args.backup_root is None:
            raise SystemExit("--promote requires --backup-root")
        result["promoted_files"] = promote_refreshed_payments(
            args.output_root,
            args.payment_root,
            args.backup_root,
        )
        result["status"] = "promoted"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
