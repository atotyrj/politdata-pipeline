"""Small, deliberately safe command-line interface for PolitData runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .change_set import DEFAULT_CURRENT_CHANGE_SET_PATH, load_change_set
from .ingestion_preflight import build_ingestion_preflight
from .incremental_pipeline import run_incremental_downstream
from .ingestion_runner import run_limited_organization_ingestion


def change_set_summary(change_set):
    """Return the operator-facing, non-sensitive state of one change set."""

    return {
        "run_id": change_set["run_id"],
        "status": change_set["status"],
        "created_at_utc": change_set["created_at_utc"],
        "organization_changes": len(change_set["organization_changes"]),
        "report_changes": len(change_set["report_changes"]),
        "affected_organization_ids": len(
            change_set["affected_organization_ids"]
        ),
        "affected_report_ids": len(change_set["affected_report_ids"]),
        "stages": {
            name: details["status"]
            for name, details in change_set["stages"].items()
        },
    }


def _change_set_path(value):
    return Path(value)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="politdata",
        description=(
            "Safe local controls for the PolitData incremental pipeline. "
            "These commands never start RAW ingestion."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command, help_text in (
        ("preflight", "Read-only readiness check before online ingestion."),
        ("status", "Read and summarize a change set without writing data."),
        (
            "downstream",
            "Run or resume downstream work for an existing change set.",
        ),
    ):
        command_parser = subparsers.add_parser(command, help=help_text)
        if command != "preflight":
            command_parser.add_argument(
                "--change-set",
                type=_change_set_path,
                default=DEFAULT_CURRENT_CHANGE_SET_PATH,
                help=(
                    "Path to an existing change set "
                    f"(default: {DEFAULT_CURRENT_CHANGE_SET_PATH})."
                ),
            )
        command_parser.add_argument(
            "--json",
            action="store_true",
            help="Print machine-readable JSON.",
        )

    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Explicit bounded online organization sync, then downstream.",
    )
    ingest_parser.add_argument(
        "--organization-limit",
        required=True,
        type=int,
        help="Maximum organization cards this online run may fetch.",
    )
    ingest_parser.add_argument(
        "--change-set",
        type=_change_set_path,
        default=DEFAULT_CURRENT_CHANGE_SET_PATH,
    )
    ingest_parser.add_argument(
        "--skip-downstream",
        action="store_true",
        help="Create the factual change set but do not process it yet.",
    )
    ingest_parser.add_argument("--json", action="store_true")

    return parser


def _print_result(value, *, as_json):
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2, default=str))
        return

    for key, item in value.items():
        if isinstance(item, dict):
            print(f"{key}:")
            for nested_key, nested_value in item.items():
                print(f"  {nested_key}: {nested_value}")
        else:
            print(f"{key}: {item}")


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "preflight":
        _print_result(build_ingestion_preflight(), as_json=args.json)
        return 0

    if args.command == "ingest":
        _print_result(
            run_limited_organization_ingestion(
                organization_limit=args.organization_limit,
                change_set_path=args.change_set,
                run_downstream=not args.skip_downstream,
            ),
            as_json=args.json,
        )
        return 0

    path = args.change_set

    if not path.exists():
        raise SystemExit(
            "Change set not found: "
            f"{path}. Create it through committed ingestion first; "
            "this CLI will not start a RAW scan."
        )

    if args.command == "status":
        _print_result(
            change_set_summary(load_change_set(path)),
            as_json=args.json,
        )
        return 0

    result = run_incremental_downstream(change_set_path=path)
    _print_result(result, as_json=args.json)
    return 0


if __name__ == "__main__":
    main()
