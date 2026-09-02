"""Create the lean, numbered analytical Excel review set."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from politdata.analytical_excel import export_analytical_workbooks


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--enriched-root",
        type=Path,
        default=Path("data/processed/enriched_v0_1"),
    )
    parser.add_argument(
        "--normalized-root",
        type=Path,
        default=Path("data/processed/normalized_v0_1"),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    summary = export_analytical_workbooks(
        enriched_root=args.enriched_root,
        normalized_root=args.normalized_root,
        output_dir=args.output_dir,
        progress=lambda message: print(message, flush=True),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
