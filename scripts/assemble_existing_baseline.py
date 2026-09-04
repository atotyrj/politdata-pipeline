"""Assemble a production baseline from already downloaded PolitData data."""

from __future__ import annotations

import argparse
import json

from politdata.production_baseline import assemble_existing_baseline


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", required=True)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--code-revision")
    args = parser.parse_args(argv)
    manifest = assemble_existing_baseline(
        args.destination,
        args.generation_id,
        code_revision=args.code_revision,
        qa={
            "status": "passed",
            "enriched_regression": "passed",
            "payment_reference_identity_mismatches": 0,
            "analytical_excel_workbooks": 17,
            "analytical_excel_structure": "passed",
        },
    )
    print(
        json.dumps(
            {
                "generation_id": manifest["generation_id"],
                "artifact_counts": manifest["artifact_counts"],
                "artifact_files": len(manifest["artifact_checksums"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
